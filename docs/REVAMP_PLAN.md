# Qaboom (repo: qahoot) — revamp plan

**Audited:** 2026-08-24 against public `https://github.com/shreb1120/qahoot` @ `5cb671a`  
**Product name in UI:** **Qaboom** (qaboom.io). “Qahoot” is the GitHub/repo lineage name only.

### Implementation progress

- **Phase 1 (grader trust) — on GitHub:** branch `cursor/phase1-grader-trust-b892` / PR #1.
- **Phase 2 (UI unify) — in progress / this branch:** steel-teal action, unified radius, Plus Jakarta display, risk-first dashboard, brand-first marketing hero.
- **Phase 1 detail:** `not_assessed` → `incomplete` (not FAIL); debt-relief `grader_extensions` opt-in on debt-settlement template only; `ANTHROPIC_MODEL` env; safer Claude JSON parse; report UI copy updated.

---

## Executive verdict

**Do not throw this away and rebuild as a Next.js SPA.**

You already have a real multi-tenant compliance-QA SaaS: Flask + Postgres + Clerk + Stripe + AssemblyAI + Claude, org-scoped checklists, manager review/sign-off, usage metering, and a report page with timeline evidence. That is far past a prototype.

What stalled you is a **trust + polish** problem, not a “missing product” problem:

1. **Grading trust** — incomplete model answers can fail the call; debt-settlement logic is baked into “generic” prompts; model IDs drift; JSON parsing is brittle; same call can grade differently.
2. **Visual sameness** — Inter + indigo + dual CSS eras (`qb-*` vs `rp-*`) reads like a competent peer tool, not a sharp modern brand.
3. **Docs/ops drift** — root `README.md` still describes the old single-tenant SQLite + `APP_PASSWORD` world while the code is SaaS.

**Recommended path:** **salvage backend, surgically fix the grader, unify and restyle the Flask UI.** A full frontend rewrite would burn months and discard the report transport that is already your best asset (`templates/calls/_report_body.html`).

---

## What you built (accurate picture)

```
Upload (org checklist required)
  → Call(status=pending) + background thread
  → AssemblyAI (speaker labels) → Transcript
  → Claude grades against ComplianceProfile (assembled prompt)
  → normalize_report() → Report JSON
  → Processing poll → Report UI (timeline + overrides)
  → Failures → manager review / sign-off queue
```

| Layer | Reality in repo | Stale README claim |
|-------|-----------------|--------------------|
| Web | Flask 3.1 + Waitress, Jinja, Clerk JWT | Shared `APP_PASSWORD` |
| DB | Postgres + SQLAlchemy + Alembic, `org_id` everywhere | SQLite `qa_history.db` |
| Jobs | In-process thread pool + recovery sweeper | In-memory only (partially true) |
| ASR | AssemblyAI universal + speakers | Correct |
| LLM | `pipeline.MODEL = "claude-opus-5"` | Says `claude-sonnet-4-6` |
| UI | Server HTML + Tailwind **build** + `overrides.css` | “No build step” / old CDN story |

Differentiator (from `PRODUCT.md`): **checklist is per-org data**, prompt assembled at grade time — not a hardcoded script. Engine was generalized from a working debt-settlement internal tool.

---

## Keep / kill / rewrite

### KEEP (high value — do not rewrite)

| Asset | Why |
|-------|-----|
| `report_normalizer.py` | Checklist is source of truth; omitted items → `not_assessed`; derived counts/verdict |
| `pipeline.py` job shell | Status machine, transcription deadline, recovery, usage metering before fragile parse |
| `prompt_builder.py` core | Sections/phrases from `ComplianceProfile` |
| `review.py` + sign-off | Manager override ≠ sign-off; severity queue |
| Multi-tenant models + migrations | Orgs, profiles, agents, clients, usage, billing |
| Report UI / tour | Timestamped evidence + audio seek is the product |
| Test suite | Normalizer, pipeline durability, tenant isolation, billing, etc. |
| Deploy/ops under `deploy/` | nginx, systemd, backups — iterate, don’t discard |

### FIX (targeted rewrites — this is the real “revamp”)

| Issue | Evidence | Fix |
|-------|----------|-----|
| **`not_assessed` fails the call** | `tests/test_verdict.py`: `not_assessed` → `"FAIL — Approval Script"` | Treat incomplete grading as **error / regrade**, not agent FAIL. Agent stats already know `not_assessed` ≠ miss (`stats.py`) — verdict logic disagrees. |
| **Debt-settlement locked into “generic” grader** | `prompt_builder.py` forces `program_flip` / `prior_settlement` / `secured_vehicle` / `litigation` | Gate industry extras behind profile flags or template type. PRODUCT.md says horizontal GTM — current prompt contradicts that. |
| **Model ID drift** | `MODEL = "claude-opus-5"` vs README sonnet vs tests defaulting to `claude-sonnet-4-5` | One env-driven model id; update README/tests; pick cost/quality deliberately. |
| **Brace JSON parse** | `raw.find("{")` / `rfind("}")` | Prefer Anthropic structured outputs / tool schema; one repair retry; fail job visibly. |
| **Grade variance** | `scripts/measure_variance.py` | Temperature/policy + structured schema; measure before/after; optional second-pass on disagreements. |
| **Dead `audio_parts` path** | Migrations + `merge_transcripts` tested; upload/pipeline don’t use it | Finish split-conversation or delete until needed. |
| **Dual design systems** | `.qb-*` (shadowed cards) vs `.rp-*` (flat panels) in `static/overrides.css` | One radius/elevation/token layer; delete orphaned leftovers. |
| **Inter + indigo blandness** | `DESIGN.md`, `base.html` | New type pairing + tighter accent use; keep teal/indigo as **speaker data colors only**. |
| **Stale README** | Claims SQLite / shared password / no CSS build | Rewrite README to match PRODUCT/LAUNCH reality. |

### KILL / defer

| Idea | Why defer |
|------|-----------|
| Full Next.js / React rewrite | Doesn’t fix grader trust; throws away working report SSR; highest schedule risk before first customers |
| Microservices / separate worker fleet | Only when single-process pool + deploy abandon-jobs becomes a real outage |
| New checklist CMS rewrite | Editor exists (`profile/`); polish UX, don’t replace data model |
| More marketing pages | Product trust first; marketing already avoids fake social proof (good) |

---

## Why it doesn’t feel like the SaaS you want

1. **Looks like every other indigo/Inter ops tool** — competent, not memorable (`DESIGN.md` explicitly chose Gong/Chorus-adjacent).
2. **Two visual eras in one app** — dashboard/`qb-*` vs report/`rp-*` feel like different products.
3. **Trust leaks into “is it broken?”** — reviewers see FAIL when the model skipped an item; industry-specific flip logic on non–debt-settlement orgs; occasional regrade disagreement.
4. **Chrome tells** — raw `admin`/`member` pills, watermark, marketing hero that leads with inset report card rather than brand-forward composition (see also prior `.impeccable` audits).

The report transport itself is category-grade. Don’t rebuild that from scratch.

---

## How to proceed (sequenced)

### Phase 0 — Align (half a day)

- Agree product name: **Qaboom** everywhere user-facing; rename GitHub when convenient.
- Rewrite root README to match live SaaS stack (or point README → PRODUCT + LAUNCH).
- Pick one Claude model via env; document cost target.

### Phase 1 — Make grading trustworthy (do this first)

Acceptance: same checklist + same transcript → stable structured scorecard; model dropouts never look like agent misses.

1. Change determination rules: `not_assessed` → job `needs_regrade` / soft warning, **not** FAIL.
2. Feature-flag `program_flip` / ineligibility codes per profile or industry template.
3. Structured Claude output (JSON schema) + Zod/Pydantic validate; drop brace slicing.
4. Re-run `scripts/measure_variance.py` and a handful of golden calls; commit fixtures.
5. Surface clear `error_message` on Calls when vendors/keys/parse fail (empty API keys today fail late).

### Phase 2 — Visual / UX unification (no SPA)

Acceptance: app feels like one modern product; report remains the hero.

1. Merge `qb`/`rp` into one token set in `overrides.css` (+ CSS variables).
2. Typography: replace Inter display with a distinctive pairing; keep Inter only if you must for body.
3. Color: one action accent; reserve indigo/teal for speakers; cool neutrals (avoid purple-glow AI cliché).
4. Dashboard: risk-first ordering, humanized roles, denser empty states.
5. Marketing hero: brand-forward first viewport (wordmark + one promise + one CTA); demo report below fold or secondary.
6. Touch targets / contrast leftovers from `.impeccable` audit.

### Phase 3 — Product completeness

1. Ship or delete `audio_parts` / split conversations.
2. Retention defaults (null = forever is a liability).
3. Worker story: keep threads until you need multi-host; then one queue (RQ/Inngest/etc.) without rewriting business logic.
4. UAT against LAUNCH.md (Clerk prod, Stripe live, backup restore drill).

### Phase 4 — Only if Phase 2 still feels capped

Consider a **report-only** interactive island (Stimulus/HTMX/small React mount) — not a full SPA migration.

---

## Suggested near-term owners

| Workstream | Primary files |
|------------|---------------|
| Grader trust | `report_normalizer.py`, `prompt_builder.py`, `pipeline.py`, `tests/test_verdict.py` |
| Design unify | `static/overrides.css`, `DESIGN.md`, `templates/base.html`, `templates/dashboard.html` |
| Report polish | `templates/calls/_report_body.html`, `_report_scripts.html` |
| Docs/ops | `README.md`, `LAUNCH.md`, `.env.example` |

---

## What “revamp done” means (MVP definition of done)

- [ ] Incomplete grades never show as agent FAIL
- [ ] Non–debt-settlement orgs don’t get settlement-only prompt clauses
- [ ] One documented model id; variance script green enough for coaching use
- [ ] One visual system; Inter/indigo peer look replaced
- [ ] README matches production
- [ ] Upload → report happy path demoable on a fresh org in &lt;10 minutes

---

## Bottom line

You don’t need a whole new codebase. You need to **stop the grader from lying**, **stop the prompt from assuming debt settlement**, and **make the existing Flask UI look and feel like one intentional product**. That is a focused revamp of Qaboom — not a greenfield rewrite of Qahoot.
