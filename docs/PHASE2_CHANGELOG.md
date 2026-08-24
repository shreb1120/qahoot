# Phase 2 — UI unify (2026-08-24)

Visual / systems pass on the Flask + Jinja surfaces. No SPA rewrite.

## Changes

1. **One radius/elevation language** — `--radius` aligned to `--c-radius` (6px); qb cards drop soft indigo shadows in favour of hairlines like `.rp-panel`.
2. **Action colour** — steel-teal (`#0e7490`) replaces indigo/purple brand action. Speaker indigo/teal kept as *data* colours on the report timeline.
3. **Busy / analysing** — amber instead of violet so status never reads as “AI purple.”
4. **Typography** — Plus Jakarta Sans for display / marketing / page titles; Inter remains for dense body UI. Self-hosted woff2.
5. **Sidebar** — slate gradient only (no `#312e81` purple end-stop).
6. **Dashboard** — risk-first metrics (Needs review → Pass rate → Processing → Total); humanized Admin/Member chips.
7. **Marketing hero** — `Qaboom` as brand-level display signal above the headline.
8. **Bugfix** — `--c-track: var(--c-track)` self-reference replaced with `#eef1f4`.
9. **Watermark** — demoted to decorative (lower contrast, non-interactive).

Rebuild CSS after pull:

```bash
npm install
npm run build:css
```
