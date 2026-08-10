"""Usage metering.

These tests defend an invoice. The failure modes are not crashes — they are
numbers that are quietly wrong, which is worse, because a wrong bill is
discovered by the customer rather than by us.

Four properties carry the weight:

* **Exactly once.** A re-queued or re-graded call must not re-bill minutes.
* **The counter never drifts from the ledger.** They are written in one
  transaction; if they can disagree, neither can be trusted.
* **Money already spent is still owed.** Grading failing after transcription
  succeeded must leave the minutes recorded.
* **One org's usage is never another's.** The same reach-through shape that
  produced a real leak in agent_performance_rows applies here.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import usage
from models import Subscription, UsageEvent, UsagePeriod
from plans import PLANS_BY_CODE


def _org(db, tenants, which="a"):
    from models import Organization
    return db.get(Organization, tenants.raw[which]["org"])


def _meter(db, org, seconds, *, key, kind=usage.TRANSCRIPTION, call_id=None):
    ok = usage.record(db, org_id=org.id, call_id=call_id, resource_type=kind,
                      idempotency_key=key, billable_seconds=seconds)
    db.commit()
    return ok


# ───────────────────────────── exactly once ─────────────────────────────

def test_the_same_key_never_bills_twice(tenants, db):
    """recover_stranded re-runs calls on purpose. Without this the customer
    pays twice for one call every time we rescue their work."""
    org = _org(db, tenants)
    assert _meter(db, org, 1800, key="call-1:transcription:1") is True
    assert _meter(db, org, 1800, key="call-1:transcription:1") is False

    period = usage.current_period(db, org)
    assert period.billable_seconds == 1800
    assert db.query(UsageEvent).filter_by(org_id=org.id).count() == 1


def test_distinct_calls_each_bill(tenants, db):
    org = _org(db, tenants)
    _meter(db, org, 600, key="call-1:transcription:1")
    _meter(db, org, 900, key="call-2:transcription:1")
    assert usage.current_period(db, org).billable_seconds == 1500


def test_the_counter_always_equals_the_ledger(tenants, db):
    """The counter exists so reads are cheap. It is only safe because it is
    written in the same transaction as the row it counts."""
    from sqlalchemy import func
    org = _org(db, tenants)
    for i in range(7):
        _meter(db, org, 100 + i, key=f"c{i}:transcription:1")
    _meter(db, org, 100, key="c0:transcription:1")           # duplicate, ignored

    ledger = db.query(func.sum(UsageEvent.billable_seconds)).filter_by(
        org_id=org.id).scalar()
    assert usage.current_period(db, org).billable_seconds == ledger


def test_concurrent_workers_do_not_lose_minutes(tenants, db):
    """The counter must be incremented in SQL, not read-modify-written.

    The pipeline runs four workers, each with its own session. `current_period`
    reads with a plain SELECT and ON CONFLICT DO NOTHING takes no lock, so a
    Python `x = x + n` let every worker write back its own total — last writer
    wins. Measured before the fix: eight concurrent workers metering 600s each
    recorded all eight events in the ledger and 1,800s on the counter. The
    invoice reads the counter, so 50 of 80 minutes were never billed.
    """
    import threading
    from sqlalchemy import func
    from db import get_db
    from models import Organization

    org_id = tenants.a["org"]
    N = 6
    gate = threading.Barrier(N, timeout=20)

    def worker(i):
        s = get_db()
        try:
            usage.current_period(s, s.get(Organization, org_id)); s.commit()
            gate.wait()                       # everyone now holds a stale read
            usage.record(s, org_id=org_id, call_id=None,
                         resource_type=usage.TRANSCRIPTION,
                         idempotency_key=f"race{i}:transcription:1",
                         billable_seconds=600)
            s.commit()
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]

    db.expire_all()
    ledger = db.query(func.sum(UsageEvent.billable_seconds)).filter_by(
        org_id=org_id).scalar() or 0
    counter = usage.current_period(db, db.get(Organization, org_id)).billable_seconds
    assert counter == ledger == N * 600, (
        f"counter {counter} != ledger {ledger}: minutes were lost to a race"
    )


# ────────────────────── money spent is still owed ──────────────────────

def test_a_failed_grade_still_leaves_the_minutes_recorded(tenants, db, monkeypatch):
    """AssemblyAI is paid before Claude is called. If grading then explodes,
    the org still owes the minutes — that is why the events are split and why
    the transcription commit happens before the analysis call."""
    import pipeline
    org = _org(db, tenants)
    call_id = tenants.a["call"]

    seen_at_call_time = {}

    class Boom:
        def create(self, **kw):
            # Read from a *separate* session: the point is that the minutes are
            # durably committed by the time Anthropic is invoked, not merely
            # pending in the pipeline's own transaction.
            from db import get_db
            other = get_db()
            try:
                seen_at_call_time["rows"] = other.query(UsageEvent).filter_by(
                    call_id=call_id, resource_type="transcription").count()
            finally:
                other.close()
            raise RuntimeError("Anthropic is down")

    class FakeClient:
        def __init__(self, **_): self.messages = Boom()

    class FakeUtt: speaker, start, end, text = "A", 0, 1000, "hi"

    class FakeResult:
        id = "tr_fake"
        status, error, text = "completed", None, "hi"
        utterances = [FakeUtt()]
        audio_duration = 1860           # 31 minutes

    monkeypatch.setattr(pipeline.anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(pipeline.aai, "Transcriber",
                        lambda: type("T", (), {"submit": lambda s, *a, **k: FakeResult()})())
    monkeypatch.setattr(pipeline.aai, "settings", type("S", (), {"api_key": ""})())

    pipeline.run_pipeline(call_id, "/tmp/x.mp3", "aai", "anth")

    assert seen_at_call_time.get("rows") == 1, (
        "the transcription must be committed before Claude is called — "
        "otherwise a grading failure rolls back minutes AssemblyAI was paid for"
    )

    db.expire_all()
    events = db.query(UsageEvent).filter_by(call_id=call_id).all()
    assert [e.resource_type for e in events] == ["transcription"], \
        "transcription must survive the rollback; analysis must not exist"
    assert events[0].billable_seconds == 1860
    assert usage.current_period(db, org).billable_seconds == 1860

    from models import Call
    assert db.get(Call, call_id).status == "error"


# ─────────────────────────── tenant isolation ───────────────────────────

def test_one_orgs_usage_never_reaches_another(tenants, db):
    a, b = _org(db, tenants, "a"), _org(db, tenants, "b")
    _meter(db, a, 6000, key="a1:transcription:1")

    assert usage.current_period(db, b).billable_seconds == 0
    assert usage.summary_for(db, b).used_minutes == 0
    assert db.query(UsageEvent).filter_by(org_id=b.id).count() == 0


# ─────────────────────────────── rounding ───────────────────────────────

def test_seconds_are_rounded_to_minutes_once_not_per_call():
    """3,000 half-minute calls are 1,500 minutes, not 3,000. Rounding each
    call up first would bill double — a revenue decision, so it is pinned."""
    assert usage.minutes_from_seconds(30 * 3000) == 1500
    assert sum(usage.minutes_from_seconds(30) for _ in range(3000)) == 3000


@pytest.mark.parametrize("seconds,minutes", [
    (0, 0), (1, 1), (59, 1), (60, 1), (61, 2), (1860, 31),
])
def test_partial_minutes_round_up(seconds, minutes):
    assert usage.minutes_from_seconds(seconds) == minutes


# ────────────────────────────── entitlement ──────────────────────────────

def test_a_trial_that_runs_out_is_blocked(tenants, db):
    """The trial hard-stops. That is the conversion moment — a free plan with
    soft overage is just a free plan.

    Derived from plans.py rather than hardcoded: the allowance is a pricing
    decision that will move again, and a test that pins it just breaks every
    time someone changes it."""
    from plans import TRIAL
    org = _org(db, tenants)
    _meter(db, org, (TRIAL.included_minutes + 1) * 60, key="t:transcription:1")

    with pytest.raises(usage.Blocked) as e:
        usage.check_can_upload(db, org)
    assert e.value.reason == "trial_exhausted"


def test_a_paid_plan_over_its_allowance_is_never_blocked(tenants, db):
    """Soft overage. Blocking a QA manager mid-audit is how B2B accounts get
    churned; the overage bills instead."""
    org = _org(db, tenants)
    db.add(Subscription(org_id=org.id, plan_code="starter", status="active",
                        included_minutes=2000, overage_micros_per_minute=180_000))
    db.commit()
    _meter(db, org, 5000 * 60, key="over:transcription:1")   # 2.5x the allowance

    s = usage.check_can_upload(db, org)         # must not raise
    assert s.over and s.overage_minutes == 3000


@pytest.mark.parametrize("status", ["canceled", "unpaid", "incomplete_expired"])
def test_an_inactive_subscription_is_blocked_regardless_of_minutes(tenants, db, status):
    org = _org(db, tenants)
    db.add(Subscription(org_id=org.id, plan_code="growth", status=status,
                        included_minutes=8000))
    db.commit()

    with pytest.raises(usage.Blocked) as e:
        usage.check_can_upload(db, org)
    assert e.value.reason == "subscription_inactive"


def test_an_unknown_plan_code_does_not_break_the_billing_page(tenants, db):
    """A grandfathered or renamed plan must still load, not 500."""
    org = _org(db, tenants)
    db.add(Subscription(org_id=org.id, plan_code="legacy_2024", status="active",
                        included_minutes=500))
    db.commit()
    assert usage.summary_for(db, org).plan.code == "trial"


# ───────────────────────────── warnings ─────────────────────────────

def test_each_threshold_fires_once(tenants, db):
    """Told once, not on every upload."""
    from plans import TRIAL
    allowance = TRIAL.included_minutes
    org = _org(db, tenants)
    _meter(db, org, int(allowance * 0.8) * 60, key="w1:transcription:1")
    assert usage.thresholds_crossed(db, org) == ["80"]
    db.commit()
    assert usage.thresholds_crossed(db, org) == []

    _meter(db, org, int(allowance * 0.25) * 60, key="w2:transcription:1")
    assert usage.thresholds_crossed(db, org) == ["100"]
    db.commit()
    assert usage.thresholds_crossed(db, org) == []


def test_no_warning_below_eighty_percent(tenants, db):
    from plans import TRIAL
    org = _org(db, tenants)
    _meter(db, org, int(TRIAL.included_minutes * 0.79) * 60, key="w:transcription:1")
    assert usage.thresholds_crossed(db, org) == []


# ───────────────────────────── period windows ─────────────────────────────

def test_a_paid_org_uses_stripes_period_not_the_calendar(tenants, db):
    """The ledger and the invoice have to agree about what "this period" is."""
    org = _org(db, tenants)
    start = datetime(2026, 8, 3, tzinfo=timezone.utc)
    db.add(Subscription(
        org_id=org.id, plan_code="growth", status="active", included_minutes=8000,
        current_period_start=start,
        current_period_end=datetime(2026, 9, 3, tzinfo=timezone.utc),
    ))
    db.commit()
    assert usage.current_period(db, org).period_start == start


def test_the_allowance_is_snapshotted_when_the_period_opens(tenants, db):
    """Changing plan mid-period must not retroactively rewrite what was
    included in a period already being billed."""
    org = _org(db, tenants)
    db.add(Subscription(org_id=org.id, plan_code="starter", status="active",
                        included_minutes=2000))
    db.commit()
    period = usage.current_period(db, org)
    assert period.included_seconds == 2000 * 60

    sub = db.get(Subscription, org.id)
    sub.included_minutes = 8000
    db.commit()
    assert usage.current_period(db, org).included_seconds == 2000 * 60


# ─────────── a subscription with no allowance means "the plan's", not zero ───────────

def test_a_zero_allowance_subscription_does_not_disable_the_trial_gate(tenants, db):
    """The trial hard-stop switched itself off, and a row like this was already
    in production.

    Subscription.included_minutes defaults to 0, and checkout() wrote a row
    carrying only the Stripe customer id before the customer paid — so an
    abandoned Checkout left a trial org at zero. A period opened from that
    snapshots included_seconds = 0, and `over` is `used >= included > 0`, which
    is False forever at zero: unlimited free usage, silently.

    Every other entitlement test sets included_minutes by hand, so the default
    was never exercised.
    """
    from models import Organization, Subscription
    import usage

    org = db.get(Organization, tenants.a["org"])
    db.add(Subscription(org_id=org.id, plan_code="trial", status="trialing"))
    db.commit()

    period = usage.current_period(db, org)
    assert period.included_seconds > 0, \
        "a zero allowance was snapshotted — the trial gate can never fire"
    assert period.included_seconds == usage.TRIAL_MINUTES * 60 if hasattr(usage, "TRIAL_MINUTES") \
        else period.included_seconds > 0


def test_the_gate_actually_fires_for_such_an_org(tenants, db):
    """The consequence, end to end: the org must be blocked once it is over."""
    from models import Organization, Subscription
    import usage
    from plans import TRIAL_MINUTES

    org = db.get(Organization, tenants.a["org"])
    db.add(Subscription(org_id=org.id, plan_code="trial", status="trialing"))
    db.commit()

    period = usage.current_period(db, org)
    period.billable_seconds = (TRIAL_MINUTES * 60) + 1
    db.commit()

    with pytest.raises(usage.Blocked):
        usage.check_can_upload(db, org)
