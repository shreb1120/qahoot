# Testing

```bash
pytest
```

## One-time setup

The suite needs its own database — it drops and recreates the schema, so it
must never point at the app's. It refuses to run if the URL matches
`DATABASE_URL` or does not end in `_test`.

```bash
sudo -u postgres psql -c "CREATE DATABASE qahoot_test OWNER qahoot;"
pip install pytest
```

Override the default with `QABOOM_TEST_DATABASE_URL` if you use a different name.

## What is covered

| File | Covers |
|---|---|
| `test_tenant_isolation.py` | Cross-org access on every id-bearing route, response bodies, aggregates, checklist mutations |
| `test_authorization.py` | Anonymous / member / admin gates on every mutation |
| `test_upload.py` | Field validation, input preservation on error, cross-org agent, non-Latin filenames, pipeline failure |
| `test_report_view_model.py` | Timeline derivation, speaker slots, marker placement, talk share |
| `test_history_and_errors.py` | Filters, pagination against hostile input, branded error pages |
| `test_templates.py` | The starter library, switching, non-destructive apply |
| `test_prompt_builder.py` | A custom checklist survives into the Claude prompt intact |
| `test_claude_contract.py` | A real API call against a hand-built checklist (opt-in) |

## Testing that a custom checklist works with the Claude API

This is the one part of the product where "it renders" is not the question. A
customer edits their checklist, and that JSON becomes the system prompt. Three
layers, in increasing cost:

**1. The prompt contract — `test_prompt_builder.py`, no API, runs always.**
Every requirement, note and auto-fail phrase must survive into the prompt
verbatim; the output schema must ask for exactly the customer's section keys;
those keys must be unique, because they are half of the manager-override key
(`key::item_name`). Includes smart quotes, accents, embedded double quotes and
300-character requirement names, because customers type what they type.

**2. The round trip — same file.** A report shaped like a custom checklist's
keys must render on the real report page, and an override written against a
custom key must persist under that key.

**3. The live model — `test_claude_contract.py`, opt-in.**

```bash
ANTHROPIC_API_KEY=sk-... pytest --live -m live
```

One short transcript and a deliberately non-default checklist. Asserts the model
returns exactly the section keys asked for, every requirement comes back, the
status vocabulary is `covered` / `not_covered`, a planted auto-fail phrase is
caught, and the response drives the actual report page. Skipped by default: it
costs money and needs network.

What this layering buys: a checklist change that would break grading fails in
layer 1 in milliseconds, and only a genuine model-behaviour question needs
layer 3.

## What is deliberately not covered

**Clerk token verification.** The tests replace the *identity* a request arrives
with, then let the real `login_required` / `org_required` / `admin_required`
decorators and the real route queries run untouched. That proves this
application's authorization and scoping; it does not prove Clerk's session
handling, which is Clerk's to prove.

**AssemblyAI and Anthropic calls.** `pipeline.spawn` is stubbed. The grading
prompt's output quality is not a unit-testable property.

## The rule that matters

`test_tenant_isolation.py` derives its route list from the app's own `url_map`,
so a new route taking `<call_id>` or `<agent_id>` is covered the moment it is
added — nobody has to remember to write a test for it. If you add an id-bearing
route and the isolation test starts failing, the route is leaking; fix the
route, not the test.
