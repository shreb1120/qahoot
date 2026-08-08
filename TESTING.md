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
