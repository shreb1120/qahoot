# Phase 1 — grader trust (2026-08-24)

Shipped in this working tree (port back to `shreb1120/qahoot` when ready):

1. **`not_assessed` no longer FAILs the call** — required items the model skipped yield verdict `incomplete` / `INCOMPLETE — grading gap`. Real `not_covered` misses still FAIL. Critical auto-fails unchanged.
2. **Debt-relief extras are opt-in** — `grader_extensions: ["program_flip", "ineligible_accounts"]` on the debt-settlement template only. Solar / tax / credit / insurance / blank prompts no longer carry settlement program-flip language.
3. **`ANTHROPIC_MODEL` env** — defaults to `claude-opus-5`; override without code edits.
4. **Safer Claude JSON parse** — fences + brace fallback; truncated JSON raises cleanly (`pipeline._parse_model_json`).
5. **Report UI** — INCOMPLETE badge; banner no longer says not-assessed is “counted as not covered.”

Existing orgs that already applied the debt-settlement template before this change will not get `grader_extensions` until they re-apply the template (or add the key to their checklist JSON). Re-apply is non-destructive (new active profile).
