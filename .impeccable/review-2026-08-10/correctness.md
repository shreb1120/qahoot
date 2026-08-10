# Qaboom — correctness & data-integrity review, 2026-08-10

Scope: usage metering, billing, entitlement, pipeline commit boundaries, schema
drift, money arithmetic. Read-only; production DB queried with SELECT only.

Grounded against the live database (`qahoot_dev`, alembic head `a1c6d0f4e839`),
which is how findings **H2** and **M5** were confirmed as *currently true in
production* rather than hypothetical.

## Summary

| # | Sev | File:line | One line |
|---|-----|-----------|----------|
| H1 | High | `blueprints/billing_bp.py:336`, `:199-208` | Overage invoice item has no Stripe idempotency key, and the webhook error path deletes the replay guard on purpose — a timed-out request Stripe actually applied gets billed twice. |
| H2 | High | `blueprints/billing_bp.py:117` → `usage.py:76` | Abandoned Checkout leaves `subscriptions.included_minutes = 0`; from the next period the trial hard-stop silently switches off. **Live in production today.** |
| H3 | High | `blueprints/billing_bp.py:323` | `_attach_overage` resolves the period from the local subscription row, not the invoice. Whether a period's overage is billed at all depends on Stripe webhook arrival order. |
| H4 | High | `pipeline.py:499-506`, `:459-468` | `recover_stranded` only skips calls that have *started*, not calls queued on the pool. A bulk import re-queues its own backlog every 10 min and pays both vendors again each time. |
| M5 | Medium | `usage.py:41-58`, `:71-74` | The trial allowance resets every calendar month, forever. Marketing says it does not. **Live in production today.** |
| M6 | Medium | `blueprints/billing_bp.py:327-332` | Allowance is snapshotted at period open; the overage *rate* is read live. A mid-period plan change bills the new rate against the old allowance. `plans.overage_micros()` computes a third number and is called by nothing. |
| M7 | Medium | `blueprints/billing_bp.py:277`, `templates/billing/plan.html:74` | The rate is frozen into `subscriptions` at webhook time. Editing a rate in `plans.py` shows the new price to the customer and invoices the old one. |
| M8 | Medium | `usage.py:235`, `blueprints/calls_bp.py:310` | No reservation between the entitlement check and metering. A trial org can burst 25 uploads past a 300-minute trial in one go. |
| M9 | Medium | `scripts/backfill_usage.py:73` | `now=call.upload_date` is silently ignored for any org with a Stripe subscription — backfilled history lands on the current invoice. |
| L10 | Low | `blueprints/billing_bp.py:332` | Float division + banker's rounding in the micros→cents conversion. Exact for today's three rates; wrong for any rate not a multiple of 10,000 micros. |
| L11 | Low | `pipeline.py:375` | `_attempt` is a read-then-write; two concurrent grades of one call collapse into one ledger row, hiding the second vendor charge. Reachable via H4. |

**Schema drift: none found.** See "Verified correct" below.

---

## H1 — Overage can be invoiced twice (High)

`blueprints/billing_bp.py:336`

```python
stripe.InvoiceItem.create(
    customer=invoice["customer"],
    invoice=invoice["id"],
    amount=amount_cents,
    currency="usd",
    description=f"{over:,} additional minutes",
)
period.stripe_invoice_id = invoice["id"]
```

This is the only call in the codebase that creates money at Stripe, and it
carries no `idempotency_key`. The in-app guard against re-billing is
`period.stripe_invoice_id` (`:323-324`), which is a *database* value.

Now look at the webhook error path, `:199-208`:

```python
try:
    _handle(event)
except Exception:
    g.db.rollback()
    g.db.query(StripeEvent).filter_by(id=event["id"]).delete()
    g.db.commit()
    abort(500)
```

The guard row is deleted deliberately — the docstring says "so the retry is not
mistaken for a duplicate". So the design is: on any exception in `_handle`,
throw away all local state and let Stripe re-deliver. That is correct for every
handler *except* this one, because this one has an external side effect that the
rollback cannot undo.

**Scenario with numbers.** Growth org (8,000 min included, $0.12/min overage),
12,000 minutes used this period → `over = 4,000`, `amount_cents = 48000`.

1. `invoice.created` arrives. `InvoiceItem.create` is sent. Stripe creates the
   item ($480) and begins writing the response.
2. The HTTP response is lost — connection reset, or our read timeout fires.
   The SDK raises `APIConnectionError`.
3. `except` fires: `period.stripe_invoice_id` is rolled back to NULL, the
   `stripe_events` row is deleted, we return 500.
4. Stripe re-delivers the same event. The replay guard is gone. `_attach_overage`
   runs again, `period.stripe_invoice_id` is still NULL, and it creates a
   **second** $480 item.

The customer's invoice is $799 + $480 + $480 = **$1,759 instead of $1,279**.
They are overcharged $480 and there is no local record that it happened —
`stripe_invoice_id` ends up set exactly once either way.

This is precisely the failure mode Stripe's `idempotency_key` parameter exists
to prevent, and it is the documented reason it exists. A key derived from
`(org_id, period_start, invoice_id)` would make the retry a no-op at Stripe.

**Test coverage: none.** `tests/test_billing.py` never posts an `invoice.created`
or `invoice.paid` event. `_attach_overage` and `_close_period` have zero tests.
Production has zero rows in `usage_periods` with a `stripe_invoice_id` and zero
subscriptions with a `stripe_subscription_id`, so this path has also never run
against live Stripe. This is the same shape as the two bugs found today: the
first time it runs will be against a real invoice.

---

## H2 — Abandoned Checkout disables the trial hard-stop (High, live now)

`blueprints/billing_bp.py:115-120`:

```python
if sub is None:
    sub = Subscription(org_id=g.org.id)
    g.db.add(sub)
sub.stripe_customer_id = customer_id
g.db.commit()
```

That row is committed with model defaults: `plan_code='trial'`, `status='trialing'`,
and — the problem — `included_minutes = 0` (`models.py:480`).

`usage.py:76` then reads it in preference to the plan:

```python
included = (sub.included_minutes if sub else plan.included_minutes) * 60
```

So the *next* `usage_periods` row for that org opens with `included_seconds = 0`.
And `UsageSummary.over` (`usage.py:199`) is:

```python
return self.used_seconds >= self.included_seconds > 0
```

With `included_seconds = 0` the chained comparison short-circuits to `False`
forever. `check_can_upload` (`usage.py:258`) never raises, and the trial becomes
unlimited.

**This is not hypothetical — it is the current state of production.**

```
org_id                                | plan_code | status   | included_minutes | stripe_subscription_id
667ad7e8-0a28-4b6b-9c16-17cd8371420d  | trial     | trialing | 0                | (null)
```

That org clicked "Choose plan", got a Stripe customer created, and never
completed Checkout. Its current period row is
`included_seconds = 6000, billable_seconds = 20096` — 335 minutes used against a
100-minute snapshot, so it is correctly blocked *right now*.

Its period ends **2026-09-07 00:00 UTC**. On that date `current_period()` opens
a new row with `included_seconds = 0 * 60 = 0`, `over` becomes `False`, and the
org can upload without limit and without a card. The sidebar meter and the plan
page will read "0 of 0 minutes — 0%".

Cost if unnoticed: at the measured ~31-minute average call and the
`MAX_CONCURRENT_PER_ORG = 25` / `UPLOADS_PER_HOUR_PER_ORG = 120` ceilings, that
is up to 3,720 minutes of audio per hour transcribed and graded at our expense
with nothing owed.

**Why the tests miss it:** every entitlement test constructs the subscription by
hand with `included_minutes` explicitly set —
`tests/test_usage.py:229, :241, :253, :303`, `tests/test_upload_gates.py:75, :85`,
`tests/test_billing.py:48`. The `included_minutes=0` default that `checkout()`
actually writes is never exercised. The fixture encodes an assumption the code
does not hold.

---

## H3 — Which period gets its overage billed depends on webhook ordering (High)

`blueprints/billing_bp.py:323`:

```python
period = usage.current_period(g.db, org)
```

`usage.current_period` (`usage.py:71-72`) resolves "current" from the local
subscription row:

```python
if sub and sub.current_period_start and sub.current_period_end:
    start, end = sub.current_period_start, sub.current_period_end
```

Note it ignores `now` entirely once a subscription has dates. So which period
`_attach_overage` bills is decided purely by whether the
`customer.subscription.updated` event carrying the *new* period was processed
before or after the `invoice.created` event. Stripe explicitly does not guarantee
event ordering.

**Scenario with numbers.** Growth org, period Aug 3 – Sep 3, 12,000 minutes used
against 8,000 included → $480 owed.

*Order A (`invoice.created` first):* `sub.current_period_start` is still Aug 3.
`current_period` returns the Aug period, `billable_seconds = 720000`,
`over = 4000`, a $480 item is attached, `stripe_invoice_id` is set. Correct.

*Order B (`customer.subscription.updated` first):* `sub.current_period_start` has
advanced to Sep 3. `current_period` opens a **new, empty** Sep period,
`billable_seconds = 0`, `over = 0`, and `_attach_overage` returns at `:329`
**before** setting `stripe_invoice_id`. The August overage is never attached to
the August invoice — and because the guard was never set, nothing marks it as
outstanding either. **$480 of revenue is silently dropped.** Next month the same
coin is flipped again.

The invoice payload carries everything needed to make this deterministic —
`invoice.period_start` / `period_end`, and `lines.data[].period` — and none of
it is read. The period should be looked up by the invoice's own period, not by
the local subscription's mutable pointer.

**Same root cause, smaller blast radius:** `_close_period` (`:356`) sets
`closed_at` on `usage.current_period(...)` too. By the time `invoice.paid`
arrives (Stripe finalizes subscription invoices ~1h after creation) the
subscription has almost certainly advanced, so `closed_at` lands on the freshly
opened period rather than the one the invoice settled. Nothing reads `closed_at`
today, so there is no money outcome — but it is the same wrong lookup.

Secondary effect of the same design: `UsageEvent.period_start` is documented
(`models.py:418`) as attributing a boundary-straddling call "to the period the
usage occurred in, deterministically". It actually attributes it to whatever the
local subscription row says at that instant. If the `customer.subscription.updated`
webhook is delayed 10 minutes at a period boundary, a 45-minute call that
completes in that window is booked to the *closed* August period whose invoice
already exists — 45 min × $0.12 = **$5.40 that is never billed in any period**.

---

## H4 — `recover_stranded` re-queues work that is still in the pool queue (High)

`pipeline.py:459-468`:

```python
def spawn(call_id, file_path, assemblyai_key, anthropic_key):
    _get_pool().submit(run_pipeline, call_id, file_path, ...)
```

`_running` is only populated once a task **starts executing** (`pipeline.py:228-232`).
A task sitting in the `ThreadPoolExecutor`'s unbounded queue is not in `_running`.

`recover_stranded` selects on the row's age, not on whether it has started
(`:486-489`):

```python
cutoff = datetime.now(timezone.utc) - STRANDED_AFTER      # 30 min
stranded = db.query(Call).filter(
    Call.status.in_(ACTIVE_STATUSES), Call.upload_date < cutoff).all()
```

`ACTIVE_STATUSES` includes `"pending"`, and `upload_date` is set when the row is
created — i.e. when the work was *queued*, not when it started. The `_running`
filter at `:499-506` is the only guard, and it does not cover queued work. Note
also that `recover_stranded` does not touch `upload_date`, so a re-queued call
stays eligible on every subsequent sweep.

**Scenario with numbers.** A customer bulk-imports 25 calls (the
`MAX_CONCURRENT_PER_ORG` ceiling) at the measured ~31-minute average.
`MAX_WORKERS = 4`. At roughly 6 minutes per call end-to-end, draining 25 calls
takes ~38 minutes, so the tail of the queue crosses `STRANDED_AFTER = 30 min`
while still in `pending`.

- t=30 min: the sweeper finds ~8 calls still `pending`, none of them in
  `_running`, and submits all 8 again. The queue now holds 8 duplicates behind
  the real work.
- t=40 min: the backlog is longer because of the duplicates, so more calls are
  still `pending` past the cutoff. Another batch is re-submitted.
- The loop is positively fed: duplicates lengthen the queue, a longer queue
  strands more calls, more strandings create more duplicates.

Each duplicate pays AssemblyAI for a full re-transcription (~$0.19 for a 31-min
call at a typical $0.37/hr) and Anthropic for a full re-grade (~$0.10 at the
~1,650-token report size). A 25-call import that should cost ~$7 in vendor spend
can plausibly run 2–3x that before the queue drains, and the customer's import
takes hours instead of 40 minutes.

The customer is billed once — the `transcription:1` idempotency key holds
(`pipeline.py:319`), and I confirmed that separately. The loss is entirely ours,
and it does not appear in `usage_events.vendor_cost_micros` either (see L11).

**Test coverage:** `tests/test_pipeline_durability.py:29` monkeypatches
`pipeline.spawn` to a list append, so the pool never exists in any test. The
distinction between "queued" and "running" — the entire defect — cannot be
observed by the current suite.

The fix is small: mark intent at submit time (add to `_running` inside `spawn`,
or track a `queued` set) so the sweeper can tell "queued here" from "abandoned
by a dead process". `_running`'s in-process scope is still correct; it is the
population point that is wrong.

---

## M5 — The trial renews every month, forever (Medium, live now)

`usage.py:41-58` — `_month_window` is used for any org without Stripe dates, and
`current_period` opens a fresh `usage_periods` row on each anniversary rollover
with a full `included_seconds`. Nothing carries prior trial consumption forward
and nothing marks a trial as spent.

**Scenario with numbers.** An org signs up on 7 August, burns all 300 trial
minutes, and is correctly blocked. On 7 September `current_period` opens a new
row with `included_seconds = 18000`, `over` returns `False`, and they get another
300 free minutes. Over twelve months: **3,600 free minutes with no card**, which
is more audio than a $99/mo Solo customer's annual allowance (600 × 12 = 7,200
— so roughly half a paid year, given away, per abandoned trial).

This directly contradicts the copy the customer was shown.
`templates/marketing/pricing.html:74`:

> "No card, and nothing to cancel. When the 300 free minutes run out, uploads
> pause until you pick a plan."

Production org `1114f769-11ef-48e3-9edc-61009209fa11` currently sits at
`included_seconds = 6000, billable_seconds = 4157`. On 2026-09-07 it will be
handed a fresh 300 minutes (18,000s, since it has no `subscriptions` row and so
picks up the current `TRIAL_MINUTES`).

`tests/test_usage.py` covers the trial hard-stop within a period
(`test_a_trial_that_runs_out_is_blocked`) but no test crosses a period boundary
on a trial org.

If a renewing trial is the intended product, `plans.py:22-27` and the pricing
copy both need to say so. If not, the trial allowance needs to be lifetime, not
per-period.

---

## M6 — Allowance is snapshotted, the rate is not (Medium)

`blueprints/billing_bp.py:327-332`:

```python
over = max(0, usage.minutes_from_seconds(period.billable_seconds)
              - usage.minutes_from_seconds(period.included_seconds))   # snapshot
amount_cents = round(over * sub.overage_micros_per_minute / 10_000)    # live
```

`period.included_seconds` is frozen when the period opens — deliberately, and
`tests/test_usage.py:298` (`test_the_allowance_is_snapshotted_when_the_period_opens`)
pins it. But `sub.overage_micros_per_minute` is read live, rewritten on every
`customer.subscription.updated` (`:277`). The two halves of one calculation come
from different points in time.

Stripe does not reset the billing cycle on a plan change unless
`billing_cycle_anchor` is passed, so the `usage_periods` row survives the switch
with the old allowance while the rate moves.

**Scenario with numbers.** Org on Solo (600 min included, $0.22/min). On day 10
they upgrade to Growth (8,000 min, $0.12/min) in the Stripe portal. They finish
the month at 2,000 minutes.

- Period row still says `included_seconds = 36000` (600 min).
- `over = 2000 - 600 = 1400`.
- `amount_cents = round(1400 * 120000 / 10000) = 168000` → **$1,680.00**… no:
  16,800 cents = **$168.00** of overage.
- On Growth's own 8,000-minute allowance the correct overage is **$0**.

The customer paid for Growth, prorated, and is invoiced $168 of overage on
minutes their new plan includes. The mirror case is ours: a Growth → Solo
downgrade keeps the 8,000-minute snapshot, so 3,000 minutes on Solo bills $0
overage instead of $528.

Related, and worth calling out because it is the shape flagged in the brief:
`plans.overage_micros()` (`plans.py:163-166`) computes overage from
`plan.included_minutes` — a *third* source, the live plan definition. It is
called by **no production code** (grep confirms: only `tests/test_billing.py:261`).
`test_overage_is_computed_from_our_ledger` is named as though it covers
invoicing and asserts against a function the invoice never touches. For the
Solo→Growth case above it returns `0` while the real path bills $168.

---

## M7 — A price change shows the new rate and bills the old one (Medium)

`blueprints/billing_bp.py:276-277` copies the plan's numbers into the
subscription row at webhook time:

```python
sub.included_minutes = plan_obj.included_minutes
sub.overage_micros_per_minute = plan_obj.overage_micros_per_minute
```

Nothing re-syncs it. `customer.subscription.updated` only fires when something
changes at Stripe, and editing `plans.py` changes nothing at Stripe.

Meanwhile the customer-facing rate is read from the live plan.
`templates/billing/plan.html:74`:

```jinja
Those bill at ${{ '%.2f' | format(summary.plan.overage_dollars_per_minute) }} a minute
```

`summary.plan` is `get_plan(sub.plan_code)` (`usage.py:213`) — the live
definition, not the cached rate.

**Scenario with numbers.** We drop Growth's overage from $0.12 to $0.10 in
`plans.py` and deploy. Every existing Growth subscription still carries
`overage_micros_per_minute = 120_000`. A customer 4,000 minutes over:

- Plan page and pricing page both say **$0.10 a minute** → they expect $400.
- `_attach_overage` computes `round(4000 * 120000 / 10000)` = 48,000 cents →
  they are invoiced **$480**.

An $80 overcharge against a price they were shown on the page, which is the
exact failure `plans.py:4-6` says the module exists to prevent ("Keep two copies
and you will eventually charge someone a price they never saw on the page").

The inverse is worse for us on a price rise: the page says $0.14, the invoice
says $0.12.

---

## M8 — No reservation between the entitlement check and metering (Medium)

`blueprints/calls_bp.py:310` calls `usage.check_can_upload` at upload time.
Minutes are not metered until transcription completes, in
`pipeline.py:312-324`. Nothing is held in between.

`UsageSummary` computes `in_flight` (`usage.py:215-220`) and the plan page
renders it, but `check_can_upload` (`usage.py:248-265`) never consults it.

**Scenario with numbers.** A trial org at 0 minutes used queues 25 uploads in
one submit burst — 25 is exactly `ratelimit.MAX_CONCURRENT_PER_ORG`
(`ratelimit.py:33`), and `UPLOADS_PER_HOUR_PER_ORG = 120` is not reached. Every
one passes `check_can_upload`, because at check time `billable_seconds` is still
0 for all of them.

At the measured ~31-minute average: **775 minutes metered against a 300-minute
trial — 2.6x.** With 60-minute calls it is 1,500 minutes, 5x. The org is then
blocked, having already consumed the audio and the vendor spend, and owing
nothing.

A reservation is not required to close this; charging the in-flight estimate
against the allowance, or simply refusing when
`used_seconds + in_flight * avg_seconds >= included_seconds` on a hard-stop
plan, bounds it to one call's overshoot.

---

## M9 — `backfill_usage.py` misattributes history for any paid org (Medium)

`scripts/backfill_usage.py:63-74`:

```python
# Attribute to the period the call actually happened in, not
# today's — otherwise a year of history lands on this month's invoice.
usage.record(db, ..., now=call.upload_date)
```

The comment states the requirement exactly. The code does not meet it.
`usage.record` passes `now` to `current_period` (`usage.py:107`), and
`current_period` **ignores `now` completely** whenever the org has a
subscription with dates (`usage.py:71-72`) — `now` is only consulted in the
`_month_window` fallback branch.

So for a trial org the script is correct; for any org with a Stripe
subscription every backfilled event is stamped with the *current* period and
increments the *current* counter.

**Scenario with numbers.** A Growth org ($799, 8,000 min) has six months of
history — 30,000 minutes — whose pipeline metering was lost (or predates
metering). Someone re-runs the backfill to repair the ledger. All 30,000 minutes
land on the open period. `_attach_overage` then computes
`over = 30000 + (this month's usage) - 8000` ≈ 22,000 → `round(22000 * 120000 / 10000)`
= 264,000 cents. The customer's next invoice carries **$2,640 of overage** for
calls that happened months ago.

The script is explicitly documented as re-runnable ("Idempotent — the same
unique keys the pipeline uses"), and the idempotency check at `:53` guards
against re-writing rows that exist — but any call *without* a ledger row is
exactly what a repair run targets, and those are the ones that get misfiled.

Fix is in `usage.current_period`: when `now` is passed explicitly it should
select the period containing `now`, not the subscription's pointer.

---

## L10 — Float division and banker's rounding in the cents conversion (Low)

`blueprints/billing_bp.py:332`:

```python
amount_cents = round(over * sub.overage_micros_per_minute / 10_000)
```

The `/` is float division and `round()` is banker's rounding — the only float
arithmetic on customer-facing money in the codebase, in the last step before
Stripe.

For today's three rates it is exact: 220,000 / 10,000 = 22.0, 180,000 → 18.0,
120,000 → 12.0. **No current customer is affected**, which is why this is Low.

It breaks the moment a rate is not a multiple of 10,000 micros. At $0.225/min
(225,000 micros): `over = 1` → `round(22.5)` → **22 cents**, rounding *down*
(banker's rounding goes to even) against us; `over = 3` → `round(67.5)` → **68**,
rounding up against the customer. The direction is not chosen, it depends on
the parity of the result — which is not a defensible answer to a customer who
asks how a line item was computed.

`(over * rate + 5_000) // 10_000` keeps it in integers with a stated direction.

For completeness, the float arithmetic in `pipeline.py:101-121`
(`_transcription_cost_micros`, `_analysis_cost_micros`) is on
`vendor_cost_micros`, which is margin reporting only and never reaches an
invoice. Not a finding.

---

## L11 — Concurrent grades of one call collapse into one ledger row (Low)

`pipeline.py:375`:

```python
idempotency_key=f"{call_id}:analysis:{_attempt(db, call_id)}",
```

`_attempt` (`:136-147`) is a `COUNT(*)` of prior analysis events plus one — a
read-then-write with no lock, evaluated *after* Anthropic has already been
charged.

**Scenario with numbers.** Two workers grade the same call concurrently — which
H4 makes routine during a bulk import. Both `_attempt` calls return `1`, both
build the key `{call_id}:analysis:1`. The first insert wins; the second hits
`uq_usage_events_idempotency_key`, `record()` returns `False` (`usage.py:124-127`)
and logs "Usage already recorded — not billing twice".

`billable_seconds` is 0 for analysis, so the customer is unaffected. But the
second grade's `vendor_cost_micros` — roughly $0.10 at the ~1,650-token report
size — is discarded. Across a 25-call import with ~15 duplicate grades that is
~$1.50 of real Anthropic spend that never appears in the ledger, and the log
line says the opposite of what happened: it reports a *prevented* double-bill
while a genuine double *spend* goes unrecorded. Margin reporting understates
COGS and gives no signal that H4 is happening.

Deriving the attempt from a column on `calls` incremented in SQL, or including
the response id in the key, removes the race.

---

## Verified correct

Places I checked specifically and found no defect. Recording them so the next
review does not re-derive them.

**Ledger/counter divergence — cannot happen.** `usage.record` (`usage.py:110-147`)
performs the `INSERT … ON CONFLICT DO NOTHING … RETURNING` and the
`UPDATE usage_periods SET billable_seconds = billable_seconds + n` in the
caller's transaction, and increments only when the insert returned a row. The
increment is a SQL-side expression, not a Python read-modify-write, so
concurrent workers cannot lose updates; `db.expire(period, ["billable_seconds"])`
at `:147` correctly drops the stale ORM value.
`tests/test_usage.py:74` (`test_concurrent_workers_do_not_lose_minutes`) exercises
this with six real threads and real sessions. **Confirmed against production
data**: `usage_events` sums to 24,253 billable seconds; the two `usage_periods`
rows sum to 4,157 + 20,096 = 24,253. Exact.

**Transcription billed once across re-runs — holds.** The key is pinned at
`f"{call_id}:transcription:1"` (`pipeline.py:319`) regardless of attempt, so
`recover_stranded` re-runs, manual re-queues, and the backfill script all
converge on one row. Grading uses a per-attempt key and is billed per attempt as
specified. Traced every write path into `usage_events`: `pipeline.py:312`,
`pipeline.py:370`, `scripts/backfill_usage.py:67`. There is no other writer, and
no code path anywhere updates or deletes a ledger row.

**Rounding applied once at aggregation — holds.** Seconds are stored on every
event (`models.py:412`); `math.ceil` appears exactly once, in
`minutes_from_seconds` (`usage.py:34-36`). No per-call rounding anywhere:
`pipeline.py:320` passes raw seconds, `backfill_usage.py:71` passes raw seconds,
and `_attach_overage` converts both sides of the subtraction from the aggregated
totals (`billing_bp.py:327-328`). `tests/test_usage.py:192` pins the 3,000
half-minute-calls case.

**Pipeline commit boundaries around paid vendor calls — correct.** The
transcription usage row is committed at `pipeline.py:324`, *before* the
Anthropic client is constructed at `:333`. The analysis usage row is committed
at `:380`, *before* the JSON extraction at `:382-387` and `normalize_report` at
`:394`, both of which can raise. The `except` at `:422` does `db.rollback()`
first, so only uncommitted work is lost — and by construction nothing already
paid for is uncommitted. `tests/test_usage.py:121` verifies this from a
*separate* session, which is the right way to prove durability rather than
pending state.

**Transcript/report overwrite on re-run — correct.** `transcripts.call_id` and
`reports.call_id` are both UNIQUE (confirmed in the live DB). `pipeline.py:291-295`
and `:403-416` both check-then-update rather than insert, which is exactly the
case `recover_stranded` produces. Manager overrides are preserved across a
re-grade.

**Webhook replay guard — correct for state-only handlers.** Insert-first with
`ON CONFLICT DO NOTHING … RETURNING` (`billing_bp.py:187-196`), committed before
`_handle` runs. `tests/test_billing.py:197` proves a replayed event with mutated
contents does not move the subscription. Signature verification has no bypass
and reads the same bytes it authenticated (`:168-179`) — and the tests sign
genuinely rather than mocking `construct_event`, which is right. The guard is
sound; H1 is about the one handler with an external side effect, not about the
guard.

**Plan resolution from price over metadata — correct.** `plan_for_price`
(`plans.py:141-155`) is tried first, metadata only as fallback, and an
unrecognised price falls back rather than 500ing (`billing_bp.py:261-270`).
Covered by `tests/test_billing.py:159` and `:183`. Note this only works when the
`STRIPE_PRICE_*` env vars are set — `is_purchasable` and the warning log at
`:266` both make an unset var loud rather than silent.

**Entitlement, both directions — correct** (given a well-formed subscription
row; H2 is about a malformed one). Paid plans have `hard_stop = False` and are
never blocked on volume; the trial is. `canceled` / `unpaid` /
`incomplete_expired` block regardless of remaining minutes. Covered by
`tests/test_usage.py:224, :237` and `tests/test_upload_gates.py:70, :82`.
`past_due` deliberately does not block — that is a dunning-grace policy
decision, not a defect, and I am flagging it only so it is a decision on the
record rather than an oversight.

**Money is integers end to end** except L10. `MICROS = 1_000_000`, all plan
fields are `int` (pinned by `tests/test_billing.py:272`), `overage_micros`
returns an int, and `usage_events.vendor_cost_micros` / `usage_periods.*` are
integer columns. `Plan.price_dollars` and `overage_dollars_per_minute` return
floats but are display-only.

**Integer column widths are safe.** `vendor_cost_micros` is `int4`
(max 2,147,483,647 micros = $2,147 per event) — a single grading call at 16,000
output tokens costs ~400,000 micros. `billable_seconds` as `int4` tops out at
68 years of audio per period.

**Vendor-side loss on `TranscriptionTimeout` is deliberate.** If
`_transcribe_with_deadline` (`pipeline.py:182-205`) times out, AssemblyAI has
been paid and nothing is metered — the customer is not billed for audio we paid
for. This favours the customer and the transcript id is logged first (`:191`) so
the work can be retrieved by hand. Documented at `:79-84`. Correct call.

**Recovery sweeper lifecycle — correct.** `start_recovery_sweeper` is called
from `serve.py`, never from `create_app()`, so importing the package starts no
threads; the loop uses `Event.wait` so a stop signal is immediate; a raising
sweep is caught so a transient DB error cannot silently disable recovery for the
process lifetime (`pipeline.py:563-573`). The defect in H4 is in what
`recover_stranded` selects, not in how it is scheduled.

**Rate limiting — correct.** `_bump` (`ratelimit.py:49-70`) is a single
`INSERT … ON CONFLICT DO UPDATE … RETURNING count`, so concurrent uploads cannot
both read the same value. Counters are per-org and per-user and the in-flight
cap is checked before any row or file is written.

### Schema drift: none

Compared `models.py` against all seven migrations *and* against the live
production schema:

- Migration chain is linear and unbranched:
  `a01b05647a81 → be9630ffec9b → c3f1a2b4d5e6 → d7e4c9a1b2f3 → e8b3f6c2a71d → f4a91c8e0d27 → a1c6d0f4e839`.
  `alembic_version` in production is `a1c6d0f4e839` — the head. No pending
  migration.
- **97 columns** across 14 tables: every column in `models.py` exists in
  production with the matching type, length, nullability and default. Nothing in
  production that `models.py` does not declare.
- **30 indexes** all present, including the three declared in `__table_args__`
  that `create_all` builds and the migrations build by raw SQL — verified
  identical definitions:
  `ix_profiles_one_active` (partial UNIQUE on `org_id WHERE is_active`),
  `ix_reports_unreviewed` (partial on `reviewed_at WHERE reviewed_at IS NULL`),
  `ix_usage_events_org_period`.
- **22 constraints** match, including the two the ORM cannot enforce and the
  ones that carry billing semantics:
  `usage_events.org_id` is `ON DELETE RESTRICT` (protects invoice evidence from
  the org cascade), `usage_events.call_id` is `ON DELETE SET NULL`,
  `uq_usage_events_idempotency_key` UNIQUE (the exactly-once guarantee itself),
  `ck_users_role`, `ck_calls_status`.
- `on_conflict_do_nothing(index_elements=["org_id", "period_start"])` in
  `current_period` correctly infers `usage_periods_pkey`; the composite PK
  exists in production.

The stated risk — tests use `create_all`, production uses Alembic — is real in
principle but has not materialised. The two are in sync today.
