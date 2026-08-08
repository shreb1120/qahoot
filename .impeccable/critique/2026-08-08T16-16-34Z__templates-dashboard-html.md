---
target: dashboard
total_score: 26
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 2
timestamp: 2026-08-08T16-16-34Z
slug: templates-dashboard-html
---
Method: dual-agent (A: design-review · B: detector+evidence)
Surface mode: Operate (dashboard — users complete a task)

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Activity spinners are live, but KPI cards are static — "Processing: 3" never ticks while History polls every 3s; no "last updated". |
| 2 | Match System / Real World | 2 | Headline metric is "Total Calls" (vanity), not risk/exposure; "Critical" demoted; role pill prints raw `member`/`admin`. |
| 3 | User Control and Freedom | 3 | Read-only overview, nothing to undo; but no way to scope the dashboard (e.g. by agent). |
| 4 | Consistency and Standards | 3 | Dashboard uses `qb-page-title`; history/report use `text-2xl font-bold`; PASS/FAIL/CRITICAL badge markup hand-duplicated in 3 templates. |
| 5 | Error Prevention | 3 | Low error surface; ordered onboarding prevents "upload before checklist". |
| 6 | Recognition Rather Than Recall | 3 | Good icons, but breakdown bars show raw counts while the *width* encodes %, forcing mental math. |
| 7 | Flexibility and Efficiency | 2 | No keyboard path to upload, no scoping, no saved views, no "jump to critical"; every drill-down is a full page nav. |
| 8 | Aesthetic and Minimalist | 3 | Clean and restrained; mild redundancy (History reachable 3 ways). |
| 9 | Error Recovery | 2 | `error` calls show a bare red "ERROR" badge — no reason, no retry, no link. Dead end. |
| 10 | Help and Documentation | 2 | Zero inline help; nothing defines Critical vs Fail or what Pass Rate counts — a trust gap for a cold compliance buyer. |
| **Total** | | **26/40** | **Acceptable — working but generic; not yet trustworthy-by-design.** |

## Design Specificity Verdict

**LLM assessment:** ~65% category-interchangeable. The four-KPI-row-over-a-2/3+1/3-split is the exact skeleton of Stripe/Linear/Vercel/analytics admin templates; the indigo/slate/emerald system is textbook enterprise SaaS. It earns real product character in spots — the threshold-colored Pass Rate, the "Processing → 0 ✓ all done" resolve, and a Results breakdown that gives Critical its own dark-red bar. But it **misses the domain where it counts**: the compliance manager's real first question — "do I have open regulatory exposure right now?" — is unanswered. Critical/auto-fail exposure is demoted to the 3rd bar of a side card as an all-time cumulative number with no urgency and no link, while two of four hero cards (Total Calls, This Week) are vanity/volume metrics. There's no trend/delta on Pass Rate, no per-agent outlier, and no "needs my review" surface even though manager overrides are a headline feature. Tellingly, the **empty/first-run state is the most authored screen** — the product loses its compliance identity the moment it has data.

**Deterministic scan:** `detect.mjs --json templates/dashboard.html` → **exit 0, 0 findings** (clean). Jinja `{% %}`/`{{ }}` constructs correctly did not trip any rule. No detector-vs-LLM disagreements and no false positives to flag — the detector confirms the markup is technically clean, which is consistent with the design review: the issues here are strategic/IA and semantic, not code-hygiene defects a linter would catch.

**Visual overlays:** None. No browser automation is exposed and the app is Clerk-auth-gated, so no live render or user-visible overlay was produced (fallback signal recorded). All findings are source- and product-truth-derived.

## Overall Impression

A clean, coherent, shippable dashboard that is optimized for the wrong reader. It looks like a competent generic SaaS overview; it does not yet look like a compliance-risk console. The single biggest opportunity: **lead with risk, not volume** — turn the above-the-fold from "how many calls" into "what needs a human's attention right now."

## What's Working

1. **Threshold-colored Pass Rate KPI** (`dashboard.html:41`) — the value itself flips emerald/amber/red at 80/60, so health is read pre-attentively before parsing the number. Right instinct, right metric.
2. **The Processing card's resolved state** (`:63–69`) — collapsing "0" into "0 ✓ all done" answers the reviewer's live-queue anxiety instead of showing a lonely zero. Small, human, domain-aware.
3. **Intentional, ordered empty state** (`:73–98`) — the 3-step card (agents → checklist → upload) encodes the real workflow and links each step, turning a blank dashboard into guided first-run. Strong for a pre-launch tool that must earn trust.

## Priority Issues

**[P0] The dashboard answers the wrong first question (risk is invisible).**
- *Why it matters:* The buyer cares about audit defensibility and regulatory risk (PRODUCT.md). Leading with "Total Calls" and hiding critical/auto-fail exposure in the 3rd side-card bar optimizes for the wrong reader and undercuts trust in a credibility-by-clarity, pre-launch product.
- *Fix:* Replace a vanity hero card (Total Calls or This Week) with a **"Needs Attention"** card — count of critical/failed calls not yet manager-reviewed — red when >0, linking to filtered History. Make critical failures clickable everywhere they appear. Add an affirmative "0 open critical — you're clean" reassurance state.
- *Command:* /impeccable layout

**[P1] Dead-end error state with no recovery.** (`:130–131`)
- *Why it matters:* A failed call shows a bare "ERROR" badge with no cause, retry, or link — the reviewer is stuck with no path forward (Nielsen #9).
- *Fix:* Make the error row link to the call with a short cause tooltip ("Transcription failed — re-upload") and a Retry affordance, mirroring `processing.html`.
- *Command:* /impeccable clarify

**[P1] Static KPIs on a live-polling product.**
- *Why it matters:* The Processing KPI is server-rendered once and never updates while History polls every 3s — the number users most want to watch tick down is stale (Nielsen #1/#4).
- *Fix:* Poll the active-calls endpoint on the dashboard too; live-update the Processing card and activity spinners; add an "Updated just now" stamp.
- *Command:* /impeccable animate

**[P2] No time context / trend on Pass Rate.**
- *Why it matters:* Compliance management runs on direction (improving vs degrading) and outliers (which agent). An all-time % can't drive a decision — and it's the top missed chance for product character.
- *Fix:* Add a delta chip ("+4 pts vs last week") and a worst-performing-agent callout or sparkline.
- *Command:* /impeccable layout

**[P2] Cross-surface inconsistency (title style + badge markup).**
- *Why it matters:* Dashboard `qb-page-title` vs history/report `text-2xl font-bold`; PASS/FAIL/CRITICAL badges re-hand-rolled in 3 templates. Compliance buyers read consistency as rigor (Nielsen #4).
- *Fix:* Promote `qb-page-title` everywhere; extract a shared `qb-verdict-badge` macro used across dashboard, history, and report.
- *Command:* /impeccable extract

## Persona Red Flags

**Rosa — QA/Compliance Manager (buyer):** Open regulatory risk is never shown above the fold (Critical is an all-time bar, not a "needs review" count). No audit/consistency signal (no "X reviewed this week", no override activity). "Critical" is undefined on this surface. Raw `member`/`admin` slug reads unfinished to someone judging polish.

**Alex — Power User (high-volume reviewer):** No keyboard path to upload; `overrides.css` has no `:focus-visible` for links/buttons, so keyboard focus is nearly invisible. Every drill-down is a full page nav; no scoping. The stale Processing count actively annoys the person who lives in the queue.

**Sam — Accessibility:** Color-only encoding in the breakdown bars — Fail vs Critical are emerald/red/dark-red with no label or pattern, indistinguishable for red-green colorblind users. `text-slate-400` captions and `text-slate-300` "—" fall below WCAG AA on white. Decorative SVGs lack `aria-hidden`; leading status circles in activity rows have no text alternative. Input focus ring exists but no visible focus for `<a>`/`<button>`.

## Minor Observations

- Role pill prints raw enum `{{ user.role }}` — humanize ("Admin"/"Member").
- Breakdown bars label counts but encode percentages in width — mismatch; add % or a shared scale.
- `round(0,'floor')` can visually under-represent near-100% values.
- Activity row shows upload time; History leads with call date — which date is primary drifts across surfaces.
- Footer "Qaboom" watermark reads like a template artifact on an authed surface.
- History reachable three ways (side button, "View all →", sidebar) — mild redundancy.
- Empty-state step links are `text-xs` — small tap targets for the most important first-run actions.

## Questions to Consider

1. If you deleted "Total Calls" and "This Week", would any compliance manager notice — or would the dashboard get better by leading with risk?
2. What is the affirmative "you are clean" state? A zero-risk and a high-risk org look nearly identical above the fold today.
3. Should critical/auto-fail calls ever be more than one click from login?
4. The empty state is your most authored screen — why does the product lose its compliance identity the moment it has data?
5. Manager overrides are a headline feature — why does the dashboard show zero "what still needs my judgment"?
6. Would a cold buyer with no definition of Pass/Fail/Critical trust the grader, or read the silence as a black box?
