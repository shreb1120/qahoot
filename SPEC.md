# Call Compliance QA — SaaS Build Spec

## Mission — read this first

We run a debt-settlement company. Sales calls in this industry are legally
required to include specific disclosures (approval amounts, no-prepayment-
penalty language, credit impact warnings, etc.), and saying the wrong thing
(promising "0% interest," guaranteeing outcomes, implying legal
representation) can expose the company to real regulatory and legal risk.

We built an internal tool that listens to a recorded sales call, transcribes
it, and uses Claude to check whether the agent said everything required and
avoided saying anything prohibited. It works, it's in daily use, and it's
cheap to run (~$0.20-0.40 per 30-minute call). That internal tool is proof
that the core idea works — we are not guessing whether AI can do this job.

**The business goal now is to turn that internal tool into a product other
companies can pay to use.** Any company that runs scripted, regulated, or
liability-sensitive sales/service calls has the same problem we do: manual
QA review is slow, inconsistent, and expensive (a human reviewer costs
roughly $7-12 per call reviewed in labor; our tool costs cents). The
opportunity is selling this as a SaaS product to companies in debt
settlement, credit repair, insurance, solar, and similar regulated-call
industries.

**Critically: every company's required script is different.** A solar
company doesn't care about "no prepayment penalty" — they care about their
own required disclosures. This means the product cannot have one hardcoded
checklist (ours). Each customer organization needs to be able to define
their own compliance checklist, and the tool needs to grade calls against
whichever org's checklist applies. This is the single biggest way this
project differs from the internal tool, and it's why "make it multi-tenant"
isn't just an account-system problem — it's also a "make the compliance
logic itself configurable" problem.

**What "done" looks like:** a company can sign up, define their own list of
required disclosures and banned phrases, have their team upload calls, and
get back a graded report — without ever touching our code or seeing any
other customer's data. That's the product.

## What exists today (starting point)

A working internal Flask app, in this repo, that does the following for
one company (us) only:

1. QA reviewer uploads a call recording in the browser
2. AssemblyAI transcribes it with speaker labels + timestamps
3. The transcript + our hardcoded compliance checklist get sent to Claude
4. Claude returns a structured JSON report: which required items were
   covered, which weren't, and any auto-fail phrases detected, each with a
   timestamp
5. The report and transcript are saved and viewable later in a history tab

Current stack: Flask + Waitress, SQLite, one shared password for the whole
team (no individual accounts), AssemblyAI, Claude via Anthropic API,
server-rendered HTML + vanilla JS. Runs on a Windows server, LAN-only,
deliberately not exposed to the internet.

**Read the existing README.md and codebase in this repo before doing
anything else.** The transcription-to-report pipeline logic already works
— your job is not to reinvent it, it's to make it multi-tenant and
configurable, then put it somewhere the public internet can safely reach it.

## What has to change, and why

### 1. Multi-tenancy (highest priority — everything else depends on this)
There is currently no concept of "which customer does this belong to."
Every table needs an owner, and every query needs to filter by that owner,
so that Org A can never see Org B's calls, transcripts, reports, or
compliance checklist under any circumstance — including by hitting an API
route directly, not just by what the UI happens to show.

### 2. Configurable compliance checklists per organization
Our checklist (18 approval-script items, 15 post-enrollment items, a list
of auto-fail phrases) must stop being a hardcoded Python file and become
data: a `compliance_profiles` table scoped to `org_id`. The Claude prompt
gets assembled at analysis time from whichever profile is active for that
org. An org admin needs *some* way to create/edit their checklist items —
a plain form (add/edit/delete required items and auto-fail phrases) is
enough for v1, it does not need to be polished.

New orgs should be able to start from a blank profile, or from a
pre-loaded "debt settlement starter template" (which can literally be our
own checklist, genericized/anonymized).

### 3. Real per-user accounts (replace the shared password)
Individual login, not one shared password. Lean toward **org-based
accounts with multiple seats** (a company signs up once, invites
teammates, everyone shares that org's call history) since the buyer is
typically a QA/compliance manager who wants their team looking at the
same data. Use a managed auth provider rather than hand-rolling this.

### 4. Database: SQLite → Postgres
SQLite works for one company's internal volume; it will not hold up to
concurrent multi-tenant traffic or most managed hosting. Design the
Postgres schema together with the multi-tenancy and compliance-profile
work as one migration.

### 5. Hosting: LAN-only → properly secured public internet
The existing app is deliberately hardened to *refuse* public exposure (no
TLS, IP allowlists, loopback-only by default). A SaaS product needs the
opposite posture: TLS everywhere, rate limiting, secure session handling,
hardened for internet-facing traffic. Some existing security work
transfers directly (CSRF protection, security headers, login throttling,
no debug mode) — extend it, don't reinvent it.

### 6. Billing
Not needed yet. Stripe integration comes later once there's a real paying
customer ready to onboard. Don't build this now.

### 7. Domain and hosting target
The product will be hosted at **qaboom.io** (domain purchased), and
will run on this same machine you are currently working in. This doesn't
change anything about Phases 1-3, but it matters for Phase 4
(production hardening/deploy): TLS/cert setup, CORS/allowed-origin config,
cookie domain settings, and any nginx/reverse-proxy config should target
this real domain rather than a placeholder. Do not start any
domain/hosting-specific configuration until Phase 4 is reached.

## Target v1 user flow

1. A company signs up, creates an org account, invites teammates
2. An org admin defines their compliance checklist (or starts from a
   template)
3. A user uploads a call recording (audio/video, up to ~2hrs/500MB)
4. App transcribes it, analyzes it against that org's active checklist,
   and shows a structured pass/fail report with timestamps
5. The report is saved to that org's history, visible only within that org
6. Users can revisit past calls/reports any time

## Explicit non-goals for v1 (do not build yet)

- A polished drag-and-drop checklist builder (a basic form is enough)
- Multiple simultaneous active profiles per org
- Bulk upload / batch processing
- CSV/PDF export
- Stripe billing
- An admin dashboard for us to see stats across all customers
- Audio player synced to transcript timestamps

## Data model (starting point — you may refine this, explain why if you do)

- **organizations** — id, name, created_at
- **users** — id, org_id, email, role (admin/member), created_at
- **compliance_profiles** — id, org_id, name, script_sections_json
  (required items, phrasing guidance, auto-fail phrases), is_active,
  created_at, updated_at
- **calls** — id, org_id, compliance_profile_id, uploaded_by_user_id,
  filename, upload_date, audio_file_url, status, duration
- **transcripts** — id, call_id, raw_transcript_json
- **reports** — id, call_id, report_json, pass_fail_status, created_at

Report JSON shape (already proven in the existing tool — keep this shape
unless you have a strong reason to change it):

```json
{
  "approval_script": {
    "covered_count": 15,
    "total_count": 18,
    "items": [
      {"name": "Approval amount stated", "status": "covered", "timestamp": "04:12", "note": ""},
      {"name": "No prepayment penalty", "status": "not_covered", "timestamp": null, "note": "Never mentioned"}
    ]
  },
  "post_enrollment_script": { "...same shape..." },
  "high_risk_phrases": [
    {"phrase": "0% interest", "timestamp": "12:03", "quote": "..."}
  ],
  "final_determination": "FAIL - Approval Script"
}
```

## Build phases

Work through these in order. **Stop after each phase and wait for
explicit approval before starting the next one.** Do not try to do
multiple phases in one pass.

**Phase 1 — Multi-tenant foundation.** Org/user model, real auth, Postgres
schema including `compliance_profiles`. No upload functionality needs to
work yet — the goal is: an org can sign up, users can log in, and the data
model correctly supports isolation and per-org checklists.

**Phase 2 — Checklist configuration + pipeline, tenant-scoped.** An org
admin can build/edit their checklist through a basic UI. Upload → AssemblyAI
transcript → Claude analysis using that org's active profile → structured
report, saved and visible only within that org.

**Phase 3 — Dashboard, history, and report UI polish.**

**Phase 4 — Production hardening and deploy.** TLS, rate limiting, secure
sessions, hardened for public exposure. Only after this phase is approved
should the app go on a publicly reachable VPS.

## Ground rules

- Work in a new repo/branch, not the original internal tool's repo — that
  is a separate live production system at our company and must not be
  touched.
- Never commit API keys or secrets. Use `.env` / `.env.example`, confirm
  `.gitignore` covers `.env`.
- If a step is destructive or hard to reverse (dropping a table, force-
  pushing, deleting data), stop and ask first, even if it seems obviously
  correct.
- If something in this spec is ambiguous or you're making a real
  architectural judgment call, explain your reasoning and the alternatives
  you considered before implementing it.
- Prefer boring, well-understood patterns over clever ones — this needs to
  be maintainable by a non-engineer (me) after you're done.
- After Phase 1 specifically: before moving on, give me an exact manual
  test procedure to verify Org A cannot access Org B's data through any
  route, including direct API calls, not just through the UI. I will run
  this myself before approving Phase 2.
