# Acceptance test — the two-person walkthrough

Full walkthrough (interactive, with progress tracking):
**https://claude.ai/code/artifact/24157999-8fd5-465d-b72f-e8e10e93b3e3**

Covers signup → org setup → checklist → a real call through the pipeline →
report and overrides → review queue → team invite and permissions → tenant
isolation → the full purchase flow on Stripe test cards → portal upgrade and
cancellation. About 60–90 minutes for two people.

## The two things to know before anyone starts

**Card payments are Stripe test mode. Transcription and grading are not.**
Every uploaded call really goes to AssemblyAI and Anthropic and is really
billed there — about $0.14 per audio hour to transcribe, about $0.07 per call
to grade. Test with a handful of short recordings.

**It runs against production.** Prefix every organization created during a test
with `TEST — ` so the rows can be found and removed afterwards. Audio retention
is currently NULL (keep forever), so nothing uploaded is cleaned up on its own.

## Cleaning up afterwards

Test orgs and everything hanging off them:

```sql
-- inspect first
SELECT id, name, created_at FROM organizations WHERE name LIKE 'TEST — %';
```

Delete in FK order: `usage_events`, `usage_periods`, `subscriptions`,
`org_invites`, `reports`, `transcripts`, `calls`, `agents`,
`compliance_profiles`, `users`, then `organizations`. Leave `stripe_events`
alone — it is the audit trail of real webhook deliveries.

Stripe test-mode customers and subscriptions should be cancelled and deleted
through the Stripe dashboard, and any Clerk test users removed from the Clerk
dashboard.
