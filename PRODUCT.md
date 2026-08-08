# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two distinct roles inside a customer organization:

- **QA / compliance manager (the buyer):** owns the account, defines the
  organization's compliance checklist, invites teammates, reviews graded
  reports, and applies manager overrides (approve/flag individual items).
  Cares about consistency, audit defensibility, and regulatory risk.
- **Frontline QA reviewers (members):** do the day-to-day work — upload call
  recordings, wait for grading, read reports and transcripts, revisit past
  calls. Higher volume, more time in the tool than the manager.

Both belong to one org and share that org's call history; data is never
visible across orgs.

## Product Purpose

A multi-tenant SaaS tool that automatically QAs recorded sales/service calls
for regulatory compliance. A call recording is transcribed (speaker labels +
timestamps), then graded by Claude against the organization's own compliance
checklist: which required disclosures were covered, which were missed, and
which prohibited/high-risk phrases were said — each with a timestamp and quote.
The result is a structured pass/fail report saved to the org's history.

It exists because manual compliance QA is slow, inconsistent, and expensive
(a human reviewer costs roughly $7–12/call in labor); this does it for cents
(~$0.20–0.40 per 30-minute call). Success means a company can sign up, define
its own required disclosures and banned phrases, have its team upload calls,
and get back graded reports — without touching code or ever seeing another
customer's data.

## Positioning

The compliance logic itself is configurable per organization, not hardcoded.
Every regulated industry — and every company within one — has a different
required script, so there is no single universal checklist. Each org defines
its own compliance profile (required items, phrasing guidance, auto-fail
phrases) as data, and the Claude grading prompt is assembled at analysis time
from whichever profile is active for that org. This is the core differentiator
from a generic transcription or call-analytics tool, and from the internal
single-tenant predecessor it was forked from.

The engine is not speculative: it is a genericization of a working internal
tool that has been in daily production use at a debt-settlement company,
proving the core idea works before it was turned into a product.

## Operating Context

- **Go-to-market:** horizontal from day one — positioned as compliance QA for
  any regulated/scripted/liability-sensitive call vertical (debt settlement,
  credit repair, insurance, solar, and similar), with no single lead industry.
  Terminology and starter templates should not assume one industry.
- **Commercial stage:** pre-launch. The internal tool is proven, but the SaaS
  product has no external customers yet. The immediate goal is landing the
  first paying customer. Future work should read as a credible, trustworthy
  product to a compliance-manager buyer evaluating it cold.
- **Core workflow:** sign up → create org → invite teammates → manager defines
  compliance checklist (blank or from a starter template) → reviewer uploads a
  call (audio/video, up to ~2hrs / 500MB) → app transcribes and grades against
  the active profile → structured report with timestamps → saved to org
  history, visible only within the org → revisit any time.
- **Calls carry metadata:** agent (who took the call), call date, client phone,
  and an internal ALV id, filterable in history.
- Reviewers work through a queue with live-polling status (pending →
  transcribing → analyzing → complete/error). Reports include an audio player,
  transcript view, a scorecard, and PDF export of the writeup.

## Capabilities and Constraints

- **Multi-tenancy is the foundational constraint:** every tenant-owned row
  carries an `org_id`; Org A must never reach Org B's calls, transcripts,
  reports, or checklist through any route, including direct API calls — not
  just what the UI shows. Enforced in application code, backed by DB FKs/indexes.
- **Roles:** `admin` and `member`. Checklist/profile and org mutations are
  admin-only; members upload and review.
- **Configurable compliance profiles** per org (JSONB `script_sections_json`):
  required items, phrasing guidance, auto-fail phrases. One active profile per
  org in v1. New orgs can start blank or from a genericized "debt settlement
  starter template." Editing is a plain add/edit/delete form — functional, not
  a drag-and-drop builder.
- **Report shape (proven, keep it):** approval-script and post-enrollment
  sections each with covered/total counts and per-item `{name, status,
  timestamp, note}`; a `high_risk_phrases` list with `{phrase, timestamp,
  quote}`; and a `final_determination`. Managers can override individual item
  verdicts (`overrides_json`).
- **Auth:** managed provider (Clerk), not hand-rolled. User id = Clerk user id.
  Invites are link/token-based (no email sending required in v1).
- **Stack:** Flask + Waitress, PostgreSQL, SQLAlchemy + Alembic, server-rendered
  Jinja templates + Tailwind (CDN) + vanilla JS. AssemblyAI for transcription,
  Anthropic Claude for grading. Deployed at qaboom.io behind nginx + TLS.
- **v1 non-goals (do not assume these exist):** Stripe/billing, bulk/batch
  upload, multiple simultaneous active profiles per org, a cross-customer admin
  dashboard, CSV export.

## Brand Commitments

- **Name:** Qaboom (forked and rebranded from an internal "call-qa-tool" /
  "Qahoot" lineage; the underlying repo and DB retain older names, but the
  product is Qaboom).
- No other binding voice, logo, or identity constraints have been established.

## Evidence on Hand

- A working, proven internal predecessor tool (in-repo: `qa_prompt.py`,
  `writeup.py`, `qa_prompt`'s real checklist) — genuine proof the grading works,
  not a mockup.
- Real cost figures from production use (~$0.20–0.40 per 30-min call vs.
  $7–12/call human labor).
- Reference documents in-repo: `Verbal_Warning_Template.pdf`,
  `new_template.pdf`, `SPEC.md`, `README.md`.
- **No external customers, testimonials, case studies, logos, benchmarks, or
  pricing exist yet** — future work must not fabricate any of these.

## Product Principles

1. **Tenant isolation is sacred.** No feature is worth a cross-org data leak;
   every surface assumes strict per-org scoping.
2. **The checklist is the customer's, not ours.** Compliance logic is data the
   org controls; never present a hardcoded or industry-locked script as the
   product.
3. **Grade to defend a decision.** Reports exist to support a real
   pass/fail/override judgment about regulatory risk — every verdict is
   traceable to a timestamped, quoted moment in the call.
4. **Boring and maintainable over clever.** The owner is a non-engineer;
   prefer well-understood patterns.
5. **Earn trust while pre-launch.** With no customer proof yet, credibility is
   carried by clarity, precision, and evident rigor rather than social proof.
</content>
</invoke>
