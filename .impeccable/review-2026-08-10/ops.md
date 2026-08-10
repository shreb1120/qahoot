# Qaboom — Operations, Deployment & Resilience Review

**Date:** 2026-08-10
**Scope:** deployment, backups, resilience, config, CSP/static, migrations, monitoring
**Method:** read-only inspection of `/srv/qaboom`, systemd, nginx, cron, journald, and read-only
`SELECT`s against the live database. No files, services, firewall or data were modified.

**Already known and accepted — not re-reported:** publicly reachable Postgres on 5433 and a
service on 3080 (Docker bypassing ufw); no virtualenv; Clerk still on a `pk_test` instance.
Interactions with those are noted where relevant.

---

## Summary

| # | Area | Severity | Finding |
|---|------|----------|---------|
| H1 | Deployment | **High** | TLS auto-renewal is broken; certbot crashes on every timer run. Cert dies 2026-11-05. |
| H2 | Backups | **High** | Audio backups will silently delete themselves from ~2026-08-21 onward. |
| H3 | Resilience | **High** | Recovery sweeper re-submits *queued* calls, not just stranded ones — duplicate vendor spend, unbounded queue. |
| H4 | Resilience | **High** | One failed startup sweep silently disables the recovery sweeper for the whole process lifetime. |
| H5 | Backups | **High** | No restore has ever been performed; backup failure has no path to a human; secrets aren't backed up. |
| H6 | Deployment | **High** | `www.qaboom.io` HTTPS redirects to plain **http://**, putting invite tokens on the wire in cleartext. |
| H7 | Config / Resilience | **High** | No connect, statement or lock timeout anywhere — one slow query or lock hangs the entire site indefinitely. |
| M1 | Log hygiene | Medium | Single-use invite tokens are written to journald (7d) and nginx access.log (14d) in plaintext. |
| M2 | Config | Medium | `SESSION_COOKIE_SECURE` defaults to **false** and is absent from `.env.example` — silently drops Secure cookie *and* HSTS. |
| M3 | Config | Medium | Missing vendor API keys default to `""`; app starts healthy and every upload fails at the vendor. |
| M4 | Deployment | Medium | Nothing checks the schema version at boot; the app starts happily against a stale schema. |
| M5 | Deployment | Medium | Every restart stalls ~90s: pool threads are non-daemon and `atexit` drains the whole queue. |
| M6 | Deployment | Medium | nginx caps bodies at 100 MB, the app claims 500 MB — users get a bare nginx 413 and a wrong number. |
| M7 | Resilience | Medium | A JWT with an unknown `kid` forces an uncached 30 s JWKS fetch per request; 8 threads = cheap DoS. |
| M8 | Resilience | Medium | A DB error mid-`load_user` leaves a user org-less and able to create a duplicate org, orphaning their data. |
| M9 | Capacity | Medium | Uploads, Postgres and logs share one filesystem with no quota or monitoring; rate limits permit ~60 GB/h vs 76 GB free. |
| M10 | Monitoring | Medium | No health endpoint and no external uptime check — only total process death is detectable. |
| M11 | CSP | Medium | `'unsafe-inline'` disables script CSP entirely; Clerk's JS is loaded from a jsdelivr wildcard with no SRI. |
| M12 | Migrations | Medium | Downgrades are destructive (one drops the billing ledger); index/rename DDL takes blocking locks with no `lock_timeout`. |
| L1 | Deployment | Low | Startup-recovery failure is printed without a traceback. |
| L2 | Deployment | Low | systemd unit has no hardening directives and uses `Restart=on-failure`, not `always`. |
| L3 | Backups | Low | `nightly.sh` `set -e` means a retention failure silently skips the rate-limit purge. |
| L4 | Resilience | Low | `aai.settings` is process-global and mutated from four worker threads. |
| L5 | Log hygiene | Low | Vendor/OS error strings, including absolute server paths, are shown verbatim to customers. |
| L6 | Monitoring | Low | Cron logs are never rotated; nothing verifies metering completeness (0 `analysis` usage events exist today). |

**Counts:** High 7 · Medium 12 · Low 6

---

## 1. Deployment

### H1 — TLS certificate auto-renewal is broken and fails silently · **High**

**Location:** `/lib/systemd/system/certbot.service` (`ExecStart=/usr/bin/certbot -q renew`),
`certbot.timer`; caused by `deploy/setup.sh:79-81`.

Evidence gathered on the host:

```
$ certbot --version
Traceback (most recent call last):
  File "/usr/bin/certbot", line 33, in <module>
    sys.exit(load_entry_point('certbot==1.21.0', ...)())
  File "/usr/bin/certbot", line 25, in importlib_load_entry_point
    return next(matches).load()

$ dpkg -l | grep certbot
ii  certbot 1.21.0-1build1          # still installed, still broken
ii  python3-certbot-nginx 1.21.0-1

$ snap list | grep certbot        # (no output — snap certbot was never installed)
$ ls /snap/bin/certbot /usr/local/bin/certbot
No such file or directory

$ systemctl list-timers | grep certbot
Mon 2026-08-10 19:28:44 PDT  5h 41min left   Mon 2026-08-10 09:51:45 PDT  certbot.timer
```

`deploy/setup.sh:79-81` was written to fix exactly this — remove the apt package, install the snap,
symlink it. It never took effect, and every step is wrapped in `2>/dev/null || true`, so the failure
was swallowed. The broken apt certbot is still the one on `$PATH`, and `certbot.timer` fires it
twice a day with `-q`, which suppresses the traceback.

**Failure scenario:** the current certificate is valid `Aug 7 2026 → Nov 5 2026`. Renewal normally
happens around 2026-09-06. Every attempt from then on will crash before contacting Let's Encrypt and
exit quietly. On **2026-11-05** `https://qaboom.io` starts serving an expired certificate. Because
the app sets `Strict-Transport-Security: max-age=31536000; includeSubDomains` (`app.py:105-108`),
returning customers get a browser interstitial they **cannot click through** — the site is hard-down
for every existing user, with no way to reach it until a human notices and fixes certbot. Nothing
alerts; the only trace is a journald entry under `certbot.service` that nobody reads.

**Fix:** `snap install --classic certbot`, symlink to `/usr/local/bin/certbot`, `apt remove certbot
python3-certbot-nginx`, then verify with `certbot renew --dry-run` and confirm a non-empty
`journalctl -u certbot.service`. Remove the `2>/dev/null || true` from `deploy/setup.sh:79-81` so
the next run cannot fail silently. Add an expiry check to the nightly job (see M10).

### H6 — `www.qaboom.io` over HTTPS redirects to plain HTTP · **High**

**Location:** `/etc/nginx/sites-available/qaboom.io`, `www.qaboom.io` server block:

```nginx
server {
    server_name www.qaboom.io;
    return 301 http://qaboom.io$request_uri;   # ← http, from a :443 listener
    listen [::]:443 ssl;
    listen 443 ssl;
}
```

The `http://` came from `deploy/setup.sh:20`, where it was correct — that block only listened on
port 80. Certbot then bolted TLS onto the same block without touching the redirect target.

**Failure scenario:** commit `3e16d31` ("Trust the reverse proxy, so external URLs are https") exists
because invite links carry a single-use token in the URL. An admin who happens to be on
`https://www.qaboom.io` when they click *Invite* gets `url_for(..., _external=True)` →
`https://www.qaboom.io/org/join/<token>` (`blueprints/org_bp.py:126`). The recipient opens it and
nginx answers `301 → http://qaboom.io/org/join/<token>`. The browser then issues that request **in
cleartext**, token and all, before the port-80 block bounces it back to HTTPS. Anyone on the path —
café Wi-Fi, a corporate middlebox, the recipient's ISP — reads the token and, with any Clerk account
(signup is public), joins the org and gains access to every call recording, transcript and
compliance report the tenant owns. HSTS only protects browsers that have already visited
`qaboom.io`; a link opened from email on a fresh browser is exactly the case it does not cover.

**Fix:** change the redirect to `https://qaboom.io$request_uri`.

### M4 — Nothing verifies the schema matches the code at boot · **Medium**

**Location:** `app.py:27-51` (`create_app`), `deploy/setup.sh:70-73`.

`deploy/setup.sh` copies the unit, reloads systemd and restarts — it never runs
`alembic upgrade head`, and there is no other deploy script. `create_app()` calls `init_db()`
(`app.py:51`), which only builds an engine; no connection is made and no schema check happens.

**Failure scenario:** a deploy ships `models.py` with a new column and the operator forgets the
migration. The service starts clean, systemd reports `active (running)`, the landing page and login
work. The first query touching that table raises `psycopg2.errors.UndefinedColumn`, the request
returns a branded 500 (`app.py:280-288`), and every affected page is broken for as long as it takes
someone to look. The reverse — code rolled back while the schema stays forward — starts equally
cleanly.

Checked today: `alembic_version` is `a1c6d0f4e839`, which matches head, so the current deployment is
consistent. The gap is that nothing would have told you otherwise.

**Fix:** compare `alembic_version` against the head revision at startup and log `ERROR` (or refuse to
start) on mismatch; add `alembic upgrade head` to a real deploy script alongside `build-css.sh`.

### M5 — Every restart stalls for ~90 seconds · **Medium**

**Location:** `pipeline.py:9-15` (docstring), `pipeline.py:171-179` (`_get_pool`),
`/etc/systemd/system/qaboom.service` (no `TimeoutStopSec`).

The module docstring states *"Workers are daemons, so a deploy still kills them mid-flight"*. That is
not true of `ThreadPoolExecutor`. Verified on this host's Python:

```
worker daemon = False
atexit hook present = True
queue type = SimpleQueue
```

`concurrent.futures.thread` registers an `atexit` hook that joins every worker, and the sentinel it
enqueues sits *behind* everything already queued — so on shutdown Python runs the **entire remaining
queue** to completion before the process can exit.

**Failure scenario:** `systemctl restart qaboom` during a bulk import. Waitress closes its listener
immediately, then the interpreter blocks in `atexit` draining queued transcriptions (each bounded at
`TRANSCRIBE_TIMEOUT = 1800 s`, `pipeline.py:69`). systemd's default `TimeoutStopSec=90s` expires and
SIGKILLs the process. Net effect: ~90 seconds of `502 Bad Gateway` on every deploy, and the calls
that were mid-flight are killed anyway — so the docstring's conclusion is right by accident while its
reasoning is wrong. Anyone reading it will size restart windows incorrectly.

**Fix:** register an explicit shutdown that calls `_pool.shutdown(wait=False, cancel_futures=True)`
on SIGTERM, set `TimeoutStopSec=20` and `KillMode=mixed`, and correct the docstring.

### M6 — Upload size limit disagrees between nginx and the app · **Medium**

**Location:** `config.py:30` (`MAX_CONTENT_LENGTH = 500 * 1024 * 1024`) vs
`/etc/nginx/sites-available/qaboom.io` (`client_max_body_size 100M`).

**Failure scenario:** a customer uploads a 180 MB recording — well inside the limit the product
advertises. nginx rejects it before the app ever sees it, so the branded 413 handler
(`app.py:270-278`) never runs and the user gets nginx's bare `413 Request Entity Too Large` page. If
they *do* reach the app's handler, it tells them "Uploads are limited to 500 MB" — a number that is
not enforceable. Support then hears "your uploader is broken" with no server-side error to correlate.

**Fix:** pick one number. `client_max_body_size 500M` plus a `client_body_timeout` is the likely
intent; note the body is buffered to disk first (see M9).

### L1 — Startup recovery failure is logged without a traceback · **Low**

**Location:** `serve.py:45-46` — `except Exception: print("Startup recovery failed; continuing")`.

One line with no exception, no traceback, and it is a bare `print` rather than the logger. When H4
(below) bites, this single line is the *only* evidence, and it tells you nothing about why.

### L2 — systemd unit is unhardened and restarts too narrowly · **Low**

**Location:** `/etc/systemd/system/qaboom.service`.

No `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp` or `ReadWritePaths`. The
process runs as `claude`, the same account that owns `/srv/qaboom`, `/home/claude/backups` and the
`.env`; a code-execution bug in the app reaches all of them, and this host also runs runbucket.com
under local accounts. `Restart=on-failure` also means a clean `exit 0` — a path Waitress can take on
an unexpected socket teardown — leaves the service stopped rather than restarting.

### Log hygiene (scope item 1)

Reviewed `app.py:73-78` (request log), `pipeline.py:150-168` (usage log), `auth.py:53` (JWT failures
at DEBUG), `csrf.py:82,88`, `ratelimit.py:98-101`.

Good: no request bodies, no headers, no cookies, no API keys are logged. JWT verification failures
log the exception at `DEBUG` (below the configured `INFO`), so tokens never reach the journal that
way. Usage logging records token counts, not content.

Two problems, below: **M1** (invite tokens in the request path) and **L5** (vendor error strings
shown to customers).

### M1 — Single-use invite tokens are written to two logs in plaintext · **Medium**

**Location:** `app.py:73-78` logs `request.path` at `INFO` on every request;
`blueprints/org_bp.py:130` defines `@org_bp.get("/join/<token>")`.

The token is generated by `secrets.token_urlsafe(32)` and is valid for 7 days
(`org_bp.py:32,108-109`). Because it lives in the **path**, not the query string, it is captured by:

* journald — `SystemMaxUse=1G`, `MaxRetentionSec=7day`, readable by group `adm` / `systemd-journal`
* `/var/log/nginx/access.log` — `rw-r----- www-data adm`, logrotate `rotate 14` (14 days of history)

Both retention windows meet or exceed the token's own validity.

**Failure scenario:** an admin invites a new reviewer on Monday; the reviewer clicks the link on
Thursday. For those three days the live token sits in two log files. Anyone with `adm` group
membership, anyone who later ships these logs to a third-party aggregator, or anyone who obtains a
log archive, can replay `GET /org/join/<token>` from a freshly-created Clerk account and become a
member of that tenant — with read access to every recording and compliance report. Note the
interaction with the accepted risk of Docker services on 3080/5433: another process on this host that
can read `adm`-group logs inherits tenant access.

**Fix:** scrub `/org/join/` paths in the request logger (log `/org/join/<redacted>`), and consider
moving the token to a POST body on a confirmation page.

### L5 — Raw exception text is shown to customers · **Low**

**Location:** `pipeline.py:427` (`_set_status(db, call, "error", str(exc))`) rendered at
`templates/calls/processing.html:19`.

Any `OSError` or vendor exception string is stored verbatim and displayed. A missing-file error
surfaces `[Errno 2] No such file or directory: '/srv/qaboom/uploads/<org-uuid>/<call-uuid>_x.mp3'` to
the end user — absolute server paths and internal identifiers. Jinja escapes it, so this is
disclosure, not XSS. Map exceptions to customer-facing copy and keep `str(exc)` in the log only.

---

## 2. Backups

`deploy/backup.sh` runs at 03:15, `deploy/nightly.sh` at 03:45, both from the `claude` user crontab
with output appended to `/home/claude/backups/{qaboom,nightly}.log`. The design is thoughtful — a
custom-format dump plus a hard-linked incremental audio mirror, with two verification steps. Two
things defeat it.

### H2 — Audio backups will start deleting themselves around 2026-08-21 · **High**

**Location:** `deploy/backup.sh:27-35`.

```bash
rsync -a --delete ${LATEST:+--link-dest="$LATEST"} /srv/qaboom/uploads/ "$DEST/audio-$STAMP/"
...
find "$DEST" -maxdepth 1 -name 'audio-*' -type d -mtime "+$KEEP_DAYS" -exec rm -rf {} +
```

`rsync -a src/ dst/` propagates the **source directory's** mtime onto the destination directory. The
retention `find` then ages the backup by the mtime of `/srv/qaboom/uploads`, not by when the backup
was taken. Confirmed on the host — every mirror, including one created this morning, carries the
uploads directory's mtime:

```
$ stat -c '%y' /srv/qaboom/uploads
2026-08-07 13:29:56

$ ls -la /home/claude/backups/qaboom/
drwxrwxr-x 4 claude claude 4096 Aug  7 13:29 audio-20260808-205126
drwxrwxr-x 4 claude claude 4096 Aug  7 13:29 audio-20260809-031501
drwxrwxr-x 4 claude claude 4096 Aug  7 13:29 audio-20260810-071955   ← taken today
```

`/srv/qaboom/uploads` only changes mtime when a *new org directory* is created — per-call files land
in `uploads/<org_id>/` (`blueprints/calls_bp.py:361-368`), one level down, which does not touch the
parent. So the clock is effectively frozen at 2026-08-07.

**Failure scenario:** on **2026-08-21** (Aug 7 + `KEEP_DAYS=14`), line 35 matches every `audio-*`
directory *including the one created seconds earlier on line 31*, and deletes them all. From that
night onward the audio half of the backup is destroyed on every run, permanently, and the script
still prints `ok  db-....dump  13 calls  ...` because the verification (lines 40-47) only inspects
the database dump. Nobody finds out until a restore is attempted and every compliance report has
evidence that cannot be played back — which the script's own header calls "only half a restore".

The `db-*.dump` files are unaffected: those get a fresh mtime from `pg_dump`.

**Fix:** stamp the mirror explicitly after rsync (`touch "$DEST/audio-$STAMP"`) or, better, select
victims by name rather than mtime — the directory names already carry a sortable timestamp.

### H5 — The backup is verified, but never restored, and its failure reaches nobody · **High**

**Location:** `deploy/backup.sh:37-47`; crontab lines `15 3 * * *` / `45 3 * * *`.

What the script *does* check is genuinely better than most: `pg_restore --list` proves the archive is
readable, and counting `COPY` rows for `calls` catches the classic "grows every night, restores an
empty schema" failure. But it is not a restore proof, and three gaps sit around it.

**a) No restore has ever been performed.** There is no restore script, no documented procedure, and
nothing in `deploy/` or `LAUNCH.md` that reconstitutes a database from a dump. The row check is
`rows -lt 1` (line 44) — a dump that lost 12 of 13 calls passes. Concrete scenario: the `qahoot` role
loses `SELECT` on one table, `pg_dump` still succeeds, the archive still lists, `calls` still has 13
rows, the check passes green for weeks — and the missing table is only discovered during the incident
you took backups for.

**b) A failing backup is invisible.** Both cron lines redirect stdout *and* stderr into a log file,
which suppresses cron's mail. `set -euo pipefail` (line 9) means a `pg_dump` failure exits non-zero
before ever reaching the verification block, and the only record is a line in
`/home/claude/backups/qaboom.log`. Scenario: Postgres restarts at 03:15 during the dump; the script
dies; nothing is written for that night; the log gains one stderr line nobody reads. Repeat for 30
nights and the retention `find` has aged out every good dump — 14 days of retention means silent
failure has a **14-day fuse**.

**c) The secrets and the host config are not in the backup.** `/srv/qaboom/.env` is `0600` (correct)
and gitignored (correct) and exists in exactly one place. A restore also needs
`/etc/systemd/system/qaboom.service`, `/etc/nginx/sites-available/qaboom.io`, `/etc/letsencrypt/`
and the crontab — none of which are captured. Scenario: the host is lost. The DB dump and the audio,
if they had been copied off-host, would still not bring the service back, because `SECRET_KEY`
(invalidating every session), `DATABASE_URL`, the Clerk keys, the Stripe secret and the Stripe
**webhook** secret are gone. Stripe secrets are recoverable from the dashboard; `SECRET_KEY` is not,
and rotating it silently logs out every user and invalidates every CSRF token
(`csrf.py:45-56`).

**Fix, in order:** (1) alert on failure — have the script `curl` a dead-man's-switch (healthchecks.io
or equivalent) *after* the verification passes, so both a crash and a bad dump page you; (2) write
`deploy/restore.sh` and actually run it once into a scratch database, recording the result and the
wall-clock time it took; (3) add `.env` (encrypted with `age` or `gpg`) and the four config files to
the backup set; (4) tighten the row check from `-lt 1` to a comparison against the previous night's
count.

### L3 — A retention failure silently skips the rate-limit purge · **Low**

**Location:** `deploy/nightly.sh:6,10` — `set -euo pipefail` followed by `python3 scripts/retention.py`.

If `retention.py` exits non-zero (a bad `DATABASE_URL`, a permission error on an audio file), the
script aborts and the rate-limit purge on lines 13-25 never runs. `rate_limit_counters` then grows
unbounded, and the only evidence is a traceback in a log nobody reads. The two jobs are independent;
they should not share a failure.

### Note on off-host copies

Accepted as known, but worth stating precisely what it costs: `/home/claude/backups` (48 MB) and
`/srv/qaboom/uploads` (47 MB) are on the same `/dev/sda1` as the database (`df` shows a single
filesystem for `/`, `/home` and `/srv`). A disk failure, a filesystem corruption, an accidental
`rm -rf`, or the disk-full scenario in M9 takes the primary and every backup together. An `rclone
sync` of `/home/claude/backups` to object storage is a ~10-line addition to `backup.sh` and is the
single highest-value change in this section after H5(1).

---

## 3. Resilience

Today's additions — bounded vendor waits and a 10-minute recovery sweeper — are the right shape. The
vendor deadlines are correct. The sweeper has two defects, both of which make things worse than
before under the exact conditions it was added for.

### H3 — The sweeper re-submits calls that are queued, not stranded · **High**

**Location:** `pipeline.py:486-489` and `pipeline.py:499-506`; `models.py:244-246`.

```python
cutoff = datetime.now(timezone.utc) - STRANDED_AFTER      # 30 minutes
stranded = db.query(Call).filter(
    Call.status.in_(ACTIVE_STATUSES), Call.upload_date < cutoff
).all()
```

Two facts combine badly:

1. **`upload_date` is the only clock and it never advances.** `Call` has no `updated_at`
   (`models.py:244-255` — `upload_date` is set once at insert, nothing else is a timestamp). So once
   a call is 30 minutes old, it satisfies the staleness test *forever*, no matter how recently work
   was done on it.
2. **`_running` only covers calls a worker has already picked up.** `spawn()` (`pipeline.py:466`)
   calls `_pool.submit(...)`, which appends to an **unbounded `SimpleQueue`** (verified:
   `queue type = SimpleQueue`). `_running.add(call_id)` does not happen until `run_pipeline` actually
   starts (`pipeline.py:228-232`). A call sitting in the executor queue is therefore in status
   `pending`, older than 30 minutes, and **not** in `_running` — indistinguishable, to the sweeper,
   from a call abandoned by a dead process.

**Failure scenario:** a QA manager imports 25 calls (the ceiling that `ratelimit.py:34`
`MAX_CONCURRENT_PER_ORG` permits). `MAX_WORKERS = 4` (`pipeline.py:45`), and a typical call takes
several minutes end to end, so the queue takes roughly an hour to drain. At T+30 min the sweeper
wakes, finds every still-queued call stale and not in `_running`, and submits each one **again** —
the queue now holds duplicates. At T+40 min it does it again. Each duplicate that eventually runs
re-uploads the audio to AssemblyAI and re-grades it at Anthropic: `recover_stranded`'s docstring
accepts paying the vendor twice for a genuine rescue, but here it is paid twice for work that was
never lost. The customer is correctly billed once (the `usage_events` idempotency key holds, and
transcription is pinned to `attempt 1` at `pipeline.py:319`) — so this shows up as **vendor cost
silently multiplying against fixed revenue**, with `_attempt()` (`pipeline.py:136-147`) faithfully
recording analysis attempts 2, 3, 4… and nothing alerting on it. The executor queue grows by one
entry per stale call per sweep for as long as the backlog lasts.

**Fix:** add `Call.updated_at` (touched by `_set_status`) and key staleness off that; and/or track
submitted-but-not-started IDs in `_running` at `spawn()` time rather than at `run_pipeline()` entry.
The second alone fixes the common case and is a two-line change.

### H4 — One bad startup sweep disables the sweeper permanently · **High**

**Location:** `serve.py:31-46`.

```python
try:
    import pipeline
    ...
    pipeline.recover_stranded(...)          # ← can raise
    pipeline.start_recovery_sweeper(...)    # ← never reached if it does
except Exception:
    print("Startup recovery failed; continuing", file=sys.stderr)
```

Both calls share one `try`. If `recover_stranded()` raises, `start_recovery_sweeper()` is skipped and
the timer never starts for the life of the process.

**Failure scenario:** the unit declares `After=network.target postgresql.service` and
`Wants=postgresql.service` — ordering, not readiness. On a host reboot, Postgres routinely takes a
few seconds longer to accept connections than systemd takes to reach the `qaboom` unit. The startup
sweep raises `OperationalError`, one line is printed with no traceback (L1), and the app serves
traffic perfectly — while the recovery sweeper added today is **silently absent**. The exact failure
it was built to fix (a call abandoned by a crashed worker sitting in `transcribing` forever, already
paid for at AssemblyAI) is back, and now it is invisible because the log line that would have told
you — `"Recovery sweeper started (every 600s)"` — is the one thing missing from a journal full of
normal traffic.

Confirmed present in the current process (`Recovery sweeper started (every 600s)` at 13:46:40), so
this is latent, not active.

**Fix:** separate the two `try` blocks, and start the sweeper first — it is the durable mechanism and
the startup sweep is just an optimisation. Log the traceback via `logger.exception`.

### What happens when each dependency is down or slow

**AssemblyAI — handled correctly.** `pipeline.py:182-205` replaces the SDK's unbounded
`transcribe()` poll with `submit()` plus a wall-clock deadline; `ASSEMBLYAI_HTTP_TIMEOUT = 300`
bounds a hung socket and `TRANSCRIBE_TIMEOUT = 1800` bounds the whole job. The transcript id is
logged before the first poll (`pipeline.py:191`), so a timeout leaves a trail to already-paid work.
Worst case a call errors out after 30 minutes with a clear message. This is right.

**Anthropic — handled, with one thing to know.** `ANTHROPIC_TIMEOUT = 600` and
`ANTHROPIC_MAX_RETRIES = 2` are stated explicitly rather than inherited (`pipeline.py:75-76,333-337`),
which is the correct instinct. The consequence worth writing down: 600 s × (1 + 2 retries) = up to
**30 minutes** on one worker, and combined with a preceding 30-minute transcription a single call can
hold one of four workers for an hour — twice `STRANDED_AFTER`. Today `_running` keeps the sweeper off
it, but that is the only thing standing between this and H3.

**Postgres — the weak link.** See H7. Additionally, if Postgres is down the *public marketing page*
goes down with it: `load_user` runs as a `before_request` on every route including `/`
(`app.py:67`), and although it catches its own exceptions (`auth.py:110-112`), `open_db`/`teardown`
and the `inject_usage` context processor sit around it. A DB outage is a full-site outage, not a
degraded-but-browsable site.

**Clerk — see M7.**

### H7 — No connect, statement or lock timeout anywhere · **High**

**Location:** `db.py:15` (`create_engine(database_url, pool_pre_ping=True)` — no `connect_args`),
plus the live server settings, read today:

```
statement_timeout                     = 0
lock_timeout                          = 0
idle_in_transaction_session_timeout   = 0
max_connections                       = 100
```

`pool_pre_ping` recovers from a *dropped* connection. It does nothing for a *hung* one. There is no
`connect_timeout`, so a Postgres that accepts TCP but never completes the handshake blocks
indefinitely; no `statement_timeout`, so a slow query runs forever; no `lock_timeout`, so a query
waiting on a lock waits forever.

**Failure scenario (concrete and imminent):** the next schema migration. `d7e4c9a1b2f3:31-34`
carries its own warning — *"takes ACCESS EXCLUSIVE and is not backward compatible with running code.
Stop the service before running"* — and nothing enforces it, because there is no deploy script
(M4). Run `alembic upgrade head` against the live database with the service up: the `ALTER TABLE`
queues behind any in-flight read of `calls`, and because Postgres lock requests queue in order,
**every subsequent query on `calls` queues behind the ALTER**. Waitress has `threads=8`
(`serve.py:17`); eight page loads later the entire app is unresponsive with no timeout to break the
deadlock. nginx's `proxy_read_timeout 300s` means users watch a spinner for five minutes and then get
a 504. The four pipeline workers hang on the same lock. Recovery requires a human with `psql` running
`pg_terminate_backend`.

The same shape occurs without a migration: any long-running analytics query, an autovacuum-blocked
DDL, or the accepted publicly-reachable Postgres on 5433 being used to hold a lock.

**Fix:** `create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5,
"options": "-c statement_timeout=30000 -c idle_in_transaction_session_timeout=60000"})`, with a
longer statement timeout for the pipeline's own sessions if needed, and `SET lock_timeout` at the top
of every migration.

### M7 — An unknown `kid` forces an uncached JWKS fetch per request · **Medium**

**Location:** `auth.py:33-54`, PyJWT 2.13.0 `PyJWKClient.get_signing_key`.

`PyJWKClient` caches the JWK set for `lifespan=300` seconds, which is fine for legitimate traffic. But
when a token's `kid` is not in the cached set, the library **refreshes unconditionally**:

```python
signing_key = self.match_kid(signing_keys, kid)
if not signing_key:
    signing_keys = self.get_signing_keys(refresh=True)   # network, every time
```

The default `timeout` is 30 s, and `load_user` runs before every route (`app.py:67`).

**Failure scenario:** an unauthenticated attacker sends `Authorization: Bearer <JWT with a random
kid>` — trivially forged, never verified, no account needed. Each request forces an outbound HTTPS
round trip to Clerk that bypasses the cache. With `threads=8`, eight concurrent such requests occupy
every Waitress thread; if Clerk is slow or rate-limits us in response, each holds for up to 30 s and
the site is unreachable for real customers. nginx's `limit_req` is per-IP (20 r/s, burst 40), so a
handful of source addresses is enough, and it costs the attacker nothing. Secondarily, this is a
free amplifier against Clerk's API from our IP.

**Fix:** cache negative `kid` lookups (an LRU of recently-rejected kids, short TTL) so an unknown kid
triggers at most one fetch per window, and pass a shorter `timeout` (5 s) to `PyJWKClient`.

### M8 — A DB error mid-`load_user` can orphan a user's organization · **Medium**

**Location:** `auth.py:104-112`.

```python
try:
    user = _sync_user(clerk_user_id, email, g.db)
    g.user = user                                  # ← set before the org lookup
    if user.org_id:
        g.org = g.db.query(Organization)...        # ← if this raises…
except Exception:
    logger.exception("Error loading user from DB")
    g.db.rollback()                                # …g.user survives, g.org stays None
```

**Failure scenario:** a transient connection drop or a cancelled statement between those two queries.
The request continues with `g.user` set and `g.org` `None`. `org_required` (`auth.py:179-182`) then
redirects to `/org/setup`, which renders the *create your organization* form because `g.org` is falsy
(`blueprints/org_bp.py:38`). A user who has been working for months is shown a first-run setup screen;
if they fill it in, `setup_post` (`org_bp.py:43-81`) creates a **new** Organization, seeds a new
checklist, and reassigns `user.org_id` — the guard on line 46 is the same falsy `g.org`, so it does
not protect. Their real org, its calls, transcripts, reports and usage ledger still exist and are now
unreachable to them; undoing it requires manual `UPDATE`s by someone with database access.

**Fix:** on exception, clear `g.user` as well as `g.org` — a half-loaded identity is more dangerous
than none — and re-read `user.org_id` from the database inside `setup_post` rather than trusting the
request-scoped `g.org`.

### M9 — One filesystem, no quota, no monitoring · **Medium**

**Location:** `config.py:29-30`, `ratelimit.py:30-34`, `df` output.

`/`, `/home` and `/srv` are all `/dev/sda1` (97 G, 76 G free), which holds the uploads, the backups,
the nginx request-body temp files, journald, *and* the PostgreSQL data directory. Audio is retained
indefinitely — `scripts/retention.py` ships inert by design and `retention_days` is NULL for all four
orgs (confirmed in the nightly log).

**Failure scenario:** the abuse ceiling permits 120 uploads/hour/org (`ratelimit.py:30`) at up to
500 MB each (`config.py:30`) — about **60 GB/hour**, which exhausts 76 GB of free space in roughly 75
minutes from a single compromised account behaving entirely within the documented limits. When the
disk fills, PostgreSQL stops accepting writes and, depending on timing, may refuse to start; nginx
cannot write logs; the 03:15 backup fails (silently, per H5b); and the app's own error pages need the
database to render. This is a total outage with a data-loss risk attached, triggered by a feature
working as designed.

**Fix:** cap per-org storage, or lower the effective ceiling (`MAX_CONTENT_LENGTH` of 500 MB is far
above any real call recording — 100 MB matches nginx and would cap the burst at 12 GB/h), and add a
disk-space check to `nightly.sh` that alerts above 80%.

### L4 — `aai.settings` is global and written from four threads · **Low**

**Location:** `pipeline.py:260-261` — `aai.settings.api_key = ...` / `aai.settings.http_timeout = ...`
inside `run_pipeline`, which runs on up to four concurrent workers.

Benign today because every worker writes identical values from the same config. It becomes a real
bug the moment anything is per-request (a per-org key, a longer timeout for large files): worker A's
setting would apply to worker B's in-flight call. Worth a comment at minimum; better, construct a
`Transcriber` with explicit settings rather than mutating module state.

---

## 4. Config

`config.py` is short and mostly right: `SECRET_KEY`, `DATABASE_URL`, `CLERK_PUBLISHABLE_KEY` and
`CLERK_JWKS_URL` use `os.environ[...]` (lines 9, 17, 20, 21), so a missing one raises `KeyError` at
import and the process refuses to start — loud, immediate, correct. `SESSION_COOKIE_HTTPONLY=True`
and `SAMESITE="Lax"` are set. `alembic.ini:88` holds a `postgresql://placeholder/placeholder` dummy
that `migrations/env.py:21-24` overrides from the environment, raising if unset — also correct, and
no credentials are committed.

Two defaults degrade silently.

### M2 — `SESSION_COOKIE_SECURE` defaults to false, and takes HSTS with it · **Medium**

**Location:** `config.py:12-14`, `app.py:105-108`.

```python
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
...
if app.config.get("SESSION_COOKIE_SECURE"):
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
```

One missing environment variable turns off **two** protections. And it is missing from the template:
`.env.example` defines only `ANTHROPIC_API_KEY`, `ASSEMBLYAI_API_KEY`, `CLERK_JWKS_URL`,
`CLERK_PUBLISHABLE_KEY`, `DATABASE_URL`, `SECRET_KEY` — no `SESSION_COOKIE_SECURE`, no `STRIPE_*`, no
`ASSEMBLYAI_COST_PER_HOUR`. The live `.env` does set it (verified by key name only), so production is
currently fine.

**Failure scenario:** the host is rebuilt, or a staging environment is stood up, from `.env.example`
as the documentation invites. The app starts, looks healthy, and serves session cookies without the
`Secure` flag and responses without HSTS. The `www` → `http://` redirect (H6) then hands an attacker
a session cookie over cleartext. Nothing logs, nothing warns; the only way to notice is to inspect
response headers by hand.

**Fix:** flip the default to `true` (opt *out* for local development, not opt in), and add every
variable the app reads to `.env.example`. A startup log line stating the effective cookie/HSTS mode
costs nothing and makes the state visible.

### M3 — Missing vendor keys start a healthy-looking app that fails every upload · **Medium**

**Location:** `config.py:25-26` — `ASSEMBLYAI_API_KEY` / `ANTHROPIC_API_KEY` default to `""`.

The comment says these are "not required for Phase 1 to start", which is no longer true — the
product's only function is the pipeline.

**Failure scenario:** a key is rotated and the new value is fat-fingered into `.env`, or the variable
is dropped during an edit. The service restarts cleanly, the dashboard loads, uploads are accepted,
files are written to disk, `Call` rows are created and rate-limit counters are consumed. Every call
then marches `pending → transcribing → error` with a vendor authentication message shown to the
customer, and the operator's evidence is `systemctl status` reporting `active (running)`. Because
`recover_stranded` re-queues non-terminal calls, and the failure is terminal, the calls at least stop
— but the customer sees a wall of red and the cause is two layers away.

**Fix:** log `ERROR` at startup for each empty vendor key, and short-circuit the upload route with an
honest "analysis is temporarily unavailable" rather than accepting money-spending work that cannot
succeed.

Also worth noting: `ASSEMBLYAI_COST_PER_HOUR` defaults to `0` (`pipeline.py:107`), which is a
*deliberate* and correct choice — margin reporting reads as obviously empty rather than quietly
wrong. Contrast that with M2/M3 and it is clear which pattern the file should standardise on.

---

## 5. Static assets & CSP

**Location:** `app.py:92-104`.

```
script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://*.clerk.accounts.dev https://*.clerk.com;
style-src  'self' 'unsafe-inline' https://*.clerk.accounts.dev;
```

The rest of the policy is good: `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'`,
and the Tailwind/Inter CDNs are genuinely gone (both are self-hosted and fingerprinted via
`static_url`, `app.py:136-158`).

### Real risk of `'unsafe-inline'` — lower than it looks

`'unsafe-inline'` on `script-src` means the CSP provides **no** protection against injected script.
It only matters if there is an injection sink. Surveying the templates:

* No `|safe` filter anywhere in `templates/`.
* One `tojson` interpolation — `templates/calls/upload.html:168`,
  `const ALLOWED = {{ allowed_extensions | tojson }}`, a server-controlled constant list.
* Jinja autoescaping is on (default for `.html`), and the untrusted values that reach templates —
  org name, agent name, uploaded filename, `client_phone`, model-generated report evidence,
  `call.error_message` — all render through normal escaped interpolation.

So today this is defence-in-depth that happens to be switched off, not a live hole. Severity
**Medium**, not High.

### The larger risk in the same directive: unpinned third-party JS

`templates/base.html:217` and `templates/auth/logout.html:9` load Clerk from
`https://cdn.jsdelivr.net/npm/@clerk/clerk-js@5/dist/clerk.browser.js` — a **floating major-version
tag on a public package CDN, with no Subresource Integrity hash**. The CSP allows the whole of
`cdn.jsdelivr.net`, which serves arbitrary npm and GitHub content.

**Failure scenario:** any `5.x` publish to `@clerk/clerk-js` — malicious, compromised-maintainer, or
simply broken — executes immediately on every Qaboom page, including the page that holds the session
cookie and the CSRF token. There is no pin and no integrity check to stop it, and the wildcard in the
CSP means a script injected by any other means can also pull arbitrary code from jsdelivr as a
bypass gadget. This is a bigger exposure than `'unsafe-inline'` and is cheaper to fix.

### What removing `'unsafe-inline'` would take

Inventory: 8 templates contain inline `<script>` (`base.html`, `calls/report.html`,
`calls/upload.html`, `calls/processing.html`, `calls/review.html`, `calls/history.html`,
`agents/profile.html`, `auth/logout.html`) and 4 inline event-handler attributes.

The cheap route is a per-request nonce: mint one in a `before_request`, expose it as a template
global (the pattern already exists for `csrf_token`, `csrf.py:96-100`), emit
`'nonce-<value>'` in the directive, and add `nonce="{{ csp_nonce }}"` to those 8 tags. The 4 inline
handlers must become `addEventListener` calls — nonces do not cover attributes. Roughly an hour,
low risk, and it can be validated with `Content-Security-Policy-Report-Only` first.

`style-src 'unsafe-inline'` should stay: Clerk's embedded components inject inline styles and there
is no nonce hook for them.

**Recommended order:** (1) pin the Clerk script to an exact version and add an SRI hash, or self-host
it; (2) drop `https://cdn.jsdelivr.net` from `script-src` once (1) is done and the source is
`cdn.jsdelivr.net/npm/@clerk/clerk-js@5.x.y` — or better, move to Clerk's own domain and remove
jsdelivr entirely; (3) nonce the 8 inline scripts and drop `'unsafe-inline'` from `script-src`.

---

## 6. Migrations

The seven migrations in `migrations/versions/` are unusually well-reasoned — the comments in
`e8b3f6c2a71d`, `f4a91c8e0d27` and `a1c6d0f4e839` explain not just what but why, and the decision in
`a1c6d0f4e839:12-15` not to backfill a sign-off that nobody performed is exactly right. The database
is at head (`alembic_version = a1c6d0f4e839`) and consistent with the code.

### M12 — Destructive downgrades and blocking DDL, with no lock timeout · **Medium**

**a) `downgrade()` destroys billing evidence.** `f4a91c8e0d27:117-124` drops `usage_events`,
`usage_periods`, `subscriptions` and `stripe_events`. `a01b05647a81:107-121` drops every table in the
schema. `be9630ffec9b:31` and `c3f1a2b4d5e6:47-54` drop columns.

*Scenario:* a bad deploy is rolled back and someone runs `alembic downgrade -1` — the ordinary
reflex. The usage ledger is gone. `f4a91c8e0d27`'s own header calls that ledger "an invoice's own
evidence", and `usage_events.org_id` is deliberately `ON DELETE RESTRICT` to protect it from
cascades — a protection that `drop_table` walks straight past. Since the audio mirror may be
self-deleting (H2) and no restore has been rehearsed (H5), recovery is not assured.

*Fix:* make the destructive downgrades raise `NotImplementedError` with a pointer to the restore
procedure. A downgrade you would never actually run is better as a loud refusal than a working
footgun.

**b) DDL takes blocking locks, and nothing times out.** `d7e4c9a1b2f3:33` renames a column
(`ACCESS EXCLUSIVE`, instant but code-breaking — the migration says so and nothing enforces it).
Every `create_index` (`c3f1a2b4d5e6:33`, `d7e4c9a1b2f3:34`, `e8b3f6c2a71d:53,56-59`,
`a1c6d0f4e839:50-53`, `f4a91c8e0d27:63`) is non-`CONCURRENTLY`, taking a `SHARE` lock that blocks
writes for the build. `e8b3f6c2a71d:44-51` runs a full-table `UPDATE` on `reports`.

At today's scale (13 calls, 4 orgs) all of these complete in milliseconds. The finding is the
combination with H7: `lock_timeout = 0`, so a migration that cannot get its lock **waits forever**
while queueing every subsequent query behind it. At 100k calls the `create_index` alone is a
multi-minute write outage.

*Fix:* `SET lock_timeout = '3s'` at the top of each migration so it fails fast rather than building a
lock queue; use `postgresql_concurrently=True` (with `autocommit_block()`) for index creation once
tables are non-trivial; and gate migrations behind a deploy script that stops the service where the
migration says to (`d7e4c9a1b2f3:31-32`).

**c) One migration can fail mid-deploy by design.** `e8b3f6c2a71d:56-59` creates a *unique* partial
index enforcing one active checklist per org. If the invariant is ever violated, the migration aborts.
Alembic wraps it in a transaction so nothing is half-applied, but the deploy stops with the new code
already on disk. The author verified the invariant held before writing it; worth a pre-flight check
in the deploy script rather than discovering it during a release.

---

## 7. Monitoring gaps — what fails silently right now

Ranked by how long the failure would go unnoticed. There is currently **no alerting of any kind**: no
uptime check, no error tracking, no metrics, no dead-man's switch. Every item below is discovered by a
human happening to look, or by a customer complaining.

| What breaks | How long before anyone notices | Why it's invisible |
|---|---|---|
| **TLS renewal (H1)** | ~87 days, then a hard outage | `certbot -q` suppresses the traceback; journald entry nobody reads |
| **Audio backups (H2)** | Until a restore is attempted — possibly never | `backup.sh` still prints `ok`; only the DB half is verified |
| **A failed nightly backup (H5b)** | Up to 14 days, when retention ages out the last good dump | cron output redirected to a file; no mail, no check |
| **Recovery sweeper not started (H4)** | Indefinitely | One `print` with no traceback, in a journal full of 200s |
| **Duplicate vendor spend (H3)** | Until the AssemblyAI/Anthropic invoice arrives | Every re-run looks like a successful call in the UI |
| **Metering broken** | Indefinitely | Today `usage_events` holds 13 `transcription` rows and **0 `analysis` rows** for 13 completed calls. That is explained by the backfill predating analysis metering — but nothing verifies that a completed call has both meter rows, so a genuine break in `pipeline.py:370-379` would look identical |
| **Vendor key wrong (M3)** | Until a customer reports failed calls | `systemctl status` says `active (running)` |
| **Disk filling (M9)** | Until Postgres stops writing | No disk check anywhere |
| **App hung on a DB lock (H7)** | Until a customer reports it | No health endpoint; process is alive so systemd is satisfied |
| **A single 500** | Never | `handle_500` logs to journald with 7-day retention; nothing aggregates or counts |

### M10 — No health endpoint, no external check · **Medium**

**Location:** no route in `blueprints/` serves a liveness/readiness check (`tests/test_authorization.py::PUBLIC_PATHS`
enumerates the public surface; there is no `/healthz`).

systemd only observes process exit. nginx has no upstream health check. So the entire class of
"process alive, application broken" — a DB lock queue (H7), an exhausted thread pool (M7), a missing
vendor key (M3), a schema mismatch (M4) — is undetectable from outside.

**The cheapest set of changes that closes most of this table:**

1. Add `GET /healthz` returning JSON: `SELECT 1` against the database, the `alembic_version` vs
   expected head, whether each vendor key is non-empty, whether the recovery sweeper thread is alive
   (`pipeline._sweeper.is_alive()`), and free disk percent. Non-200 when any check fails.
2. Point a free external monitor (UptimeRobot / healthchecks.io) at `/healthz` — this covers H1
   indirectly too, since an expired certificate fails the check.
3. Have `backup.sh` ping a dead-man's-switch **after** its verification passes, so both a crash and a
   bad dump alert.
4. Add certificate-expiry and disk-percent checks to `nightly.sh`, alerting under 21 days / over 80%.
5. Add error aggregation (Sentry or equivalent) to `handle_500` and to `pipeline.py:422-434`, so a
   pipeline failure rate is a number someone can see rather than a `logger.exception` in a 7-day
   journal.

---

## Suggested order of work

1. **H1** — fix certbot today. It is the only finding with a fixed, known outage date.
2. **H2** — one `touch` in `backup.sh`. Ten days until it starts destroying audio backups.
3. **H4** — split the `try` in `serve.py`. Two-line change to a mechanism added today.
4. **H6** — one word in the nginx redirect.
5. **H3** — move `_running.add()` into `spawn()`; add `Call.updated_at` when convenient.
6. **H5** — dead-man's-switch first, then write and *run* a restore.
7. **H7** — engine `connect_args` with timeouts, and `SET lock_timeout` in migrations.
8. **M10** — `/healthz` plus an external monitor; this is what makes the rest observable.
