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

## A custom checklist grades correctly by construction

The customer must never have to wonder whether the checklist they wrote will
work. That is not achieved by giving them a way to check it; it is achieved by
making a broken grading run impossible.

**The checklist is the source of truth; the model response is evidence.**
`report_normalizer.py` reconciles every response against the checklist that was
in force before anything is stored:

- every section and every requirement appears, in checklist order, whether or
  not the model returned it
- a requirement the model omitted becomes `not_assessed` — visibly distinct from
  "the agent did not say it", counted as not covered, and surfaced in a banner
  so a reviewer is told rather than left to notice
- counts are recomputed, never taken from the response
- the determination is derived from the reconciled rows, so a verdict can never
  disagree with the table beneath it
- sections the model invented are dropped; auto-fail phrases are filtered to the
  ones the org configured, so a hallucinated violation cannot fail a call

Two structural collisions are also prevented at the point of edit: section keys
are unique (they are half of the manager-override key), and a requirement name
cannot repeat inside one section.

`test_report_normalizer.py` pins all of this, including garbage input.

### And the prompt itself

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
