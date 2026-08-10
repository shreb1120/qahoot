# Qaboom security review — 2026-08-10

Read-only review of `/srv/qaboom` at `3e16d31` (working tree, master). Scope: tenant
isolation, authz, CSRF, auth, upload handling, secrets, injection, Stripe webhook.

Headline: **tenant isolation and CSRF are in good shape and I could not break either.**
Every finding below is in the perimeter around them — the identity provider it trusts,
the invite flow, and one host-level exposure.

## Summary

| Sev | Location | Issue |
|---|---|---|
| High | `auth.py:44-51` (+ `.env` `CLERK_PUBLISHABLE_KEY=pk_test…`) | Session JWTs are verified with no issuer, audience or `azp` binding, against a Clerk **development** instance serving production. A token minted for an attacker's origin is accepted as the victim. |
| Medium | `blueprints/org_bp.py:130-160` | `GET /org/join/<token>` is a state-changing GET that overwrites `user.role`. One click demotes an admin to member — permanently, since no route can promote anyone back. |
| Medium | `blueprints/org_bp.py:136-154`, `models.py:146` | Invite tokens are pure bearer credentials: `invited_email` is stored and never checked. Anyone with the link joins the tenant and reads every recording. |
| Medium | `app.py:74-78` | Every request path is logged at INFO, so `/org/join/<token>` writes a live tenant-access credential into journald and nginx logs in cleartext. |
| Medium | host: `ss -lntp` → `0.0.0.0:5433`, `[::]:5433` | PostgreSQL exposed to the internet on the host that stores customer call audio. (Not the app's DB — app uses `127.0.0.1:5432`.) |
| Low | `csrf.py:59-64` | CSRF token accepted from the query string; no caller needs it, and it puts a session secret into URLs, referrers and logs. |
| Low | `app.py:92-104` | `script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net` — CSP would stop essentially no XSS. Defence-in-depth only; no XSS found. |
| Low | nginx `www.qaboom.io` block | `return 301 http://qaboom.io$request_uri` downgrades to cleartext for one hop, carrying the full path (including an invite token, if the link is ever typed with `www.`). |
| Low | `blueprints/calls_bp.py:825` | Phone filter builds an `ILIKE` pattern without escaping `%`/`_`. Correctness, not injection; the helper already exists at `agents_bp.py:32`. |

---

## High

### H1 — Clerk JWTs are accepted with no issuer/audience/`azp` binding, on a dev instance

**Where:** `auth.py:42-54` (verification), `auth.py:80-96` (`Authorization: Bearer` accepted
on every request), `config.py:20-21`, `.env` → `CLERK_PUBLISHABLE_KEY=pk_test…`,
`CLERK_JWKS_URL=https://smiling-gnat-71.clerk.accounts.dev/.well-known/jwks.json`.

```python
return jwt.decode(
    token,
    signing_key.key,
    algorithms=["RS256"],
    options={"verify_exp": True, "verify_nbf": True},
)
```

Algorithm pinning is correct (`RS256` only — `alg: none` and HS256 key-confusion are both
dead), and expiry/nbf are enforced. What is missing is any claim that binds the token to
*this application*: no `issuer=`, no `audience=`, and no check of Clerk's `azp`
(authorized party) claim. PyJWT validates `aud` only when you pass `audience=`, so as
written the app accepts **any** RS256 token signed by that Clerk instance, regardless of
which origin asked Clerk to mint it.

That would be a hardening note against a Clerk *production* instance, which is pinned to a
configured domain. This is a `pk_test_…` **development** instance backing a live product,
and development instances deliberately answer any origin so `localhost` works. The
publishable key is not a secret — it is rendered into `base.html:216-221` on every page.

**Exploit.** Attacker registers `evil.com` and mounts Clerk's JS with Qaboom's publishable
key, copied from view-source. A Qaboom user visits `evil.com` while holding a Clerk
session (the `__client` cookie lives on `smiling-gnat-71.clerk.accounts.dev`). Clerk's
frontend API, being a dev instance, mints a session JWT for that visitor to `evil.com`.
The page ships the JWT to the attacker's *server*, which replays it against
`https://qaboom.io/...` as `Authorization: Bearer <jwt>` — a server-to-server call, so
CORS never applies. `load_user` resolves `g.user` to the victim.

CSRF does not contain this. The attacker's server can `GET /dashboard` with the Bearer
token, receive a `Set-Cookie` session carrying a freshly minted `_csrf`
(`csrf.py:49-56` mints lazily on any render), and read the matching token out of the
returned HTML. It then has both halves of the double-submit and can POST freely. Result:
full read and write access to the victim's org — call recordings, transcripts, consumer
phone numbers, checklist edits, and (if the victim is an admin) Stripe Checkout and the
billing portal.

**Precondition, stated honestly:** the attacker page needs the Clerk FAPI call to carry the
victim's `__client` cookie in a third-party context. Browsers that block third-party
cookies outright break this step. I did not attempt it against the live Clerk instance
(out of scope for a read-only review), so treat the chain as plausible rather than
demonstrated — but the two code-level defects it rests on are certain, and one of them
(`azp`) exists specifically to stop it.

**Fix:** pass `issuer=` and verify `azp` against `https://qaboom.io` in `_verify_session_token`,
and move to a Clerk production instance (`pk_live_…`). The instance swap is already tracked
in the owner's notes as a cosmetic "Development mode badge" task — it is not cosmetic.

---

## Medium

### M2 — `GET /org/join/<token>` overwrites `user.role`, and demotion is irreversible

**Where:** `blueprints/org_bp.py:130-160`, specifically line 153:

```python
user.org_id = invite_record.org_id
user.role = invite_record.role      # always "member" (org_bp.py:117)
```

Two problems compound.

*It is a GET.* `csrf.validate()` returns immediately for safe methods (`csrf.py:69`), so
this mutation has no CSRF protection at all — correctly, since a GET is not supposed to
mutate.

*Role is overwritten unconditionally.* The conflict guard at line 147 only fires when the
user belongs to a *different* org. An admin of org A following an invite link **for org A**
sails past it and is written down to `member`.

`role` is set to `"admin"` in exactly one place in the codebase — `org_bp.py:65`, during
org creation — and there is no member-management route at all. So a demoted admin cannot
be restored without direct database access.

**Exploit.** Any member of the org (invite links get pasted into Slack and forwarded by
mail, and any invited member has held one) sends the org's sole admin a link to a page
that top-level-navigates to `https://qaboom.io/org/join/<token>`, or simply asks them to
"check this invite link works". The admin clicks, is silently demoted, and the org
permanently loses checklist editing (`profile_bp`, 8 admin-only routes), member invites,
`/org/settings`, and Stripe Checkout and portal access — while still being billed. The
invite is consumed in the same request, so the evidence disappears.

A subresource (`<img src=…>`) will not do it: the Flask session cookie is `SameSite=Lax`
and Clerk's `__session` likewise, so this needs a top-level navigation. That is a low bar.

**Fix:** accept invites on POST behind CSRF (a GET landing page with a confirm button), and
never lower an existing role — apply `invite_record.role` only when `user.org_id` is None.

### M3 — Invite tokens are bearer-only; `invited_email` is never enforced

**Where:** `blueprints/org_bp.py:103-127` (creation), `136-141` (redemption),
`models.py:146` (`invited_email` column).

`invite()` accepts and stores an email, and `org/settings.html` shows it, so an admin
reasonably reads "I invited alice@corp.com" as a restriction. Redemption looks up only
`token`, `accepted=False` and `expires_at` — the email is never compared to `g.user.email`.

**Exploit.** Any person with a Qaboom account (self-serve signup is open) who obtains the
URL — a forwarded invite mail, a Slack scrollback, a shared browser's history, or the
server logs from M4 — has 7 days to walk in. On success they get member access to the
whole tenant: `/calls/` history, `/calls/<id>/audio` (raw consumer call recordings),
transcripts, client phone numbers, and every report. Members can also write: override
compliance verdicts, sign off the review queue, reassign calls, and burn Anthropic spend
via `/calls/<id>/writeup`.

**Fix:** when `invited_email` is set, require `g.user.email` to match it.

### M4 — Invite tokens are written to the logs in cleartext

**Where:** `app.py:73-78`.

```python
logger.info("%s %s → %d (user=%s)", request.method, request.path, ...)
```

`request.path` for a redemption is `/org/join/<43-char-token>`. That token is a live
credential for tenant membership (see M3) for 7 days, and it lands in journald, and in
nginx's access log, in plaintext, retained indefinitely.

I checked the current journal (`journalctl -u qaboom`, 50k lines) and found zero
`/org/join/` entries — invites simply have not been exercised in production yet. The code
path is unconditional, so this is latent, not absent. Query strings are *not* logged
(`request.path`, not `full_path`), which is why the CSRF-token-in-args issue below stays
Low.

**Fix:** redact the last path segment for the `org.accept_invite` endpoint, or log
`request.endpoint` plus `request.view_args` with the token elided.

### M5 — PostgreSQL listening on all interfaces, port 5433

**Where:** host, not repo. Verified with `ss -lntp`:

```
LISTEN 0 4096  0.0.0.0:5433  0.0.0.0:*
LISTEN 0 4096     [::]:5433     [::]:*
```

For contrast, `127.0.0.1:5432` (the app's own database — `DATABASE_URL` uses `localhost`
with no port) and `127.0.0.1:5000` (the app) are both correctly loopback-only.

So the exposed instance is **not** Qaboom's database. It is still a Postgres reachable
from the internet on 185.28.23.100 / `2a02:4780:10:3e2c::1`, on the same host that stores
customers' raw call recordings under `/srv/qaboom/uploads`. Any auth weakness or
CVE in it is a foothold on that host. I could not read the firewall or identify the owning
process (both need root), and did not attempt to connect.

This matches the already-known open item in the project notes. **Fix:** bind to loopback or
firewall the port; if it belongs to another service on the box, that service's owner still
should not be advertising it.

---

## Low

### L6 — CSRF token accepted from the query string
`csrf.py:59-64` — `_submitted()` falls back to `request.args.get("csrf_token")`. Not
exploitable on its own (an attacker still needs the token's value, which is HMAC'd from
the session), and query strings are not logged by `app.py:74`. But it invites callers to
put a session-bound secret into URLs, where it reaches `Referer` headers, browser history
and nginx access logs. No caller in the codebase uses it. Delete the branch.

### L7 — CSP provides no meaningful XSS containment
`app.py:92-104` — `script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://*.clerk.accounts.dev https://*.clerk.com`.
`'unsafe-inline'` alone defeats the directive's purpose, and jsDelivr is a general-purpose
mirror of npm and GitHub, so allowlisting it permits arbitrary attacker-chosen JavaScript.
I found no XSS to exploit this with, so it is purely the loss of a safety net. The comment
already flags a nonce for Phase 4; pinning the Clerk script with SRI would also help.

### L8 — `www.qaboom.io` redirects to `http://`
`/etc/nginx/sites-available/qaboom.io`, www server block: `return 301 http://qaboom.io$request_uri;`
sends a TLS visitor back over cleartext for one hop before the `http→https` redirect picks
them up. HSTS covers this only after the browser has already seen a `qaboom.io` response.
Cookies are `Secure` so nothing authenticates over that hop, but the **path** does travel
in the clear — and `/org/join/<token>` is a path. This is the same class of problem commit
`3e16d31` fixed on the application side. Change to `https://qaboom.io$request_uri`.

### L9 — Unescaped LIKE wildcards in the phone filter
`blueprints/calls_bp.py:825` — `Call.client_phone.ilike(f"%{phone_filter}%")`. Parameterised,
so not injection; but a reviewer searching for a literal `%` or `_` gets wrong results.
`agents_bp.py:32-34` already has `_escape_like` for exactly this, applied at `agents_bp.py:45`.
Same-tenant data only.

### L10 — Upload size limits disagree
`config.py:30` sets `MAX_CONTENT_LENGTH = 500 MB`; nginx sets `client_max_body_size 100M`.
A 200 MB recording is rejected by nginx with its own page, and the app's friendly 413
handler (`app.py:270-278`) — which tells the user the limit is 500 MB — never runs. Not a
security issue; the tighter limit is the one enforced.

---

## Checked and found correct

Stating these explicitly, since "we looked" is the useful part.

**Tenant isolation — no leak found.** I enumerated every `db.query` / `db.get` / `select`
in the request path (`blueprints/*.py`, `stats.py`, `review.py`, `usage.py`, `ratelimit.py`)
and traced each one's scoping. All 49 are org-scoped or org-independent. The join-shaped
queries — the class that leaked before — are all scoped on both sides:

- `stats.py:118-120` filters `Agent.org_id == org_id` **and** `Call.org_id == org_id`; the
  regression test `test_agent_performance_does_not_count_another_orgs_call` guards it.
- `review.py:85` and `review.py:101` reach `Report` through a join and filter `Call.org_id` —
  correct, since `Report` carries no `org_id`.
- `dashboard_bp.py:46`, `calls_bp.py:585-591` (`review_bulk`), `calls_bp.py:834` (history's
  result filter): same shape, all filter `Call.org_id`.
- `_org_call_or_404` (`calls_bp.py:187-198`) scopes on `org_id` and is used by all ten
  `<call_id>` routes, including `/audio` and `/report.pdf`. `agent_id` from the upload and
  reassignment forms is re-validated against the org (`calls_bp.py:356`, `:649`).

The one query without an org filter, `calls_bp.py:502` (`g.db.get(User, rpt.reviewed_by_user_id)`),
is safe: that column is only ever written from `g.user.id` in `review.sign_off`, so it can
only name someone who reviewed a call in that org.

`tests/test_tenant_isolation.py` covers 13 cases including cross-org override writes and
audio access.

**AuthZ — consistent.** `@admin_required` is on every mutation in `profile_bp` (all 8),
`agents_bp` (add/import/delete), `org_bp` (settings, invite) and `billing_bp`
(checkout, portal). `org_bp.setup`/`setup_post` are correctly `login_required` (there is no
org yet) and both guard on `g.org`. The member-writable routes in `calls_bp` (override,
review, review/bulk, agent assignment, writeup) are a deliberate tier documented at
`calls_bp.py:640-643` — reviewers are the intended users. `review_bulk` re-filters to
`Report.verdict == "pass"` in SQL so a tampered form cannot bulk-clear failures
(`calls_bp.py:591`). No upward escalation path exists: the only assignment of `"admin"` is
at org creation. (The *downward* one is M2.)

**CSRF — sound design.** One global `before_request` hook rather than per-route decorators
(`csrf.py:94`), so a new route cannot be forgotten. Token is HMAC-SHA256 of a per-session
random over `SECRET_KEY`, compared with `hmac.compare_digest`. The exempt list has exactly
one entry and `tests/test_csrf.py:120` asserts `len(EXEMPT_ENDPOINTS) == 1`. The
`WTF_CSRF_ENABLED is False` bypass is test-only and production never sets it. Lazy minting
means anonymous readers get no cookie.

**Stripe webhook — correct.** `billing_bp.py:176-179` verifies the signature against the raw
body *before* anything is parsed, and every failure path is `abort(400)`; there is no
unsigned route through the function. Replay is guarded by inserting into `stripe_events`
with `ON CONFLICT DO NOTHING` and bailing when no row comes back (`:187-196`), and the
guard row is deleted on handler failure so Stripe's retry is not swallowed (`:206`). The
subtlety of verifying with the SDK but reading from `json.loads` of the same authenticated
bytes is deliberate and documented. `_org_id_for` reads `metadata.org_id`, which only our
own API key can set — customers cannot write it from the billing portal.

**File upload — well handled.** Extension allowlist on the last extension
(`calls_bp.py:63-66`, `:89-90`); `secure_filename` plus a call-UUID prefix
(`:363-392`) makes traversal impossible; files land in `/srv/qaboom/uploads/<org_id>/`,
outside the static root, dir `0750` and files `0640` (verified on disk). Empty files are
deleted and rolled back. Serving goes through `send_file` behind `_org_call_or_404` with a
guessed mimetype, and `X-Content-Type-Options: nosniff` (`app.py:83`) closes the
"HTML disguised as `.webm`" stored-XSS route. Both entitlement and rate limits run *before*
any row or file is written (`:309-328`). One nit: `uploads/667ad7e8-…/` is currently `0770`
rather than `0750` — same owner and group, so no practical exposure; `os.chmod(…, 0o750)`
at `:375` will correct it on the next upload to that org.

**Injection — clean.** No `|safe`, no `{% autoescape false %}`, no `Markup()` anywhere in
`templates/` or the Python. Every Jinja value that lands inside a `<script>` block is
server-controlled (`url_for` with UUIDs, integers, or `|tojson`); nothing user-supplied
reaches a JS string literal. `render_template_string` is imported at `calls_bp.py:44` but
never called — dead import, worth deleting so it cannot become a habit. No SQL is built by
string concatenation; every `db.execute` takes a SQLAlchemy construct, and the only
`text()` uses are static index predicates in `models.py:176` and `:310`.

**Open redirect — closed.** `safe_next` (`auth.py:125-139`) rejects anything not starting
with `/`, plus `//`, `/\`, `://`, CR and LF. Its output reaches only an HTML attribute
(`data-next`, autoescaped) and `afterSignInUrl` in `static/js/auth.js`, never string-
concatenated into script.

**Secrets — not leaking.** `.env` is `0600 claude:claude`. No template references
`config`, `environ` or any key; the only credential rendered is `clerk_key`, which is
public by design. No API key, JWT, session value or Stripe secret appears in any log
statement — `auth.py:53` logs only the exception on a bad JWT, never the token.
`jsonify` responses return no sensitive fields. The `Authorization` header is read but
never logged.

**Proxy trust — correct.** `ProxyFix(x_proto=1, x_host=1, x_for=0)` (`app.py:48`) paired
with waitress `trusted_proxy="127.0.0.1"` and an explicit `trusted_proxy_headers` set
(`serve.py:66-70`). Declining to trust `X-Forwarded-For` is the right call given
`remote_addr` is unused. Note that nginx does not strip a client-supplied
`X-Forwarded-Host`, so `url_for(_external=True)` is attacker-influenceable — but only
within the attacker's own response (their invite link, their Stripe return URL), so there
is no cross-user impact. Worth pinning `SERVER_NAME` or having nginx set
`X-Forwarded-Host $host` anyway.

**Security headers.** `X-Frame-Options: DENY` and `frame-ancestors 'none'` (both),
`nosniff`, `Referrer-Policy: same-origin`, and HSTS with `includeSubDomains` gated on
`SESSION_COOKIE_SECURE`, which `.env` sets to `true`. Session cookie is `HttpOnly`,
`SameSite=Lax`, `Secure`.

**Rate limiting.** `ratelimit.py` caps uploads per org and per user, in-flight
concurrency, and write-up generation, with an atomic `ON CONFLICT DO UPDATE` increment so
concurrent requests cannot both read a stale count. nginx adds 20 r/s general and 5 r/s on
`/auth/`.
