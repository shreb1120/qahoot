"""Usage metering — the ledger, the counter, and the entitlement question.

Three things happen here, and keeping them apart is the whole design:

* **Recording** what was consumed, exactly once, into an append-only ledger.
* **Counting** it into a per-period row so "how much have they used" is a
  primary-key read rather than a scan.
* **Answering** whether an org may upload — which is a question about *payment
  state*, not volume. Paid plans never block; the trial does, because a free
  plan with soft overage is just a free plan.

Everything is in seconds internally and rounded to minutes exactly once, at
aggregation. Rounding per call would bill 3,000 half-minute calls as 3,000
minutes instead of 1,500 — a revenue decision, not an implementation detail.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from models import Subscription, UsageEvent, UsagePeriod
from plans import TRIAL, get_plan

logger = logging.getLogger(__name__)

TRANSCRIPTION = "transcription"
ANALYSIS = "analysis"


def minutes_from_seconds(seconds: int) -> int:
    """Round up, once, at the point of aggregation."""
    return math.ceil(max(0, seconds) / 60)


# ─────────────────────────────── periods ───────────────────────────────

def _month_window(now: datetime, anchor_day: int) -> tuple[datetime, datetime]:
    """The billing month containing `now`, anchored to a day of the month.

    Used only for orgs without a Stripe subscription (trials). A paid org's
    window comes from Stripe so the ledger and the invoice always agree.
    Clamped because not every month has a 31st.
    """
    def _at(year: int, month: int) -> datetime:
        last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
        return datetime(year, month, min(anchor_day, last), tzinfo=timezone.utc)

    start = _at(now.year, now.month)
    if start > now:
        prev_y, prev_m = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        start = _at(prev_y, prev_m)
    nxt_y, nxt_m = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start, _at(nxt_y, nxt_m)


def current_period(db, org, *, now: datetime | None = None) -> UsagePeriod:
    """Return (creating if needed) the open usage period for an org.

    Created lazily with ON CONFLICT DO NOTHING so two concurrent uploads at a
    period boundary cannot both insert.
    """
    now = now or datetime.now(timezone.utc)
    sub = db.get(Subscription, org.id)
    plan = get_plan(sub.plan_code if sub else None)

    if sub and sub.current_period_start and sub.current_period_end:
        start, end = sub.current_period_start, sub.current_period_end
    else:
        start, end = _month_window(now, org.created_at.day if org.created_at else 1)

    # `or plan.included_minutes` is load-bearing, not defensive noise.
    #
    # Subscription.included_minutes defaults to 0, and checkout() writes a row
    # carrying only the Stripe customer id before the customer has paid for
    # anything — so an abandoned Checkout leaves a trial org with a zero
    # allowance. A period opened from that snapshots included_seconds = 0, and
    # `over` is `used >= included > 0`, which is False forever at zero. The
    # trial hard-stop switches itself off and the org uploads free of charge
    # indefinitely. One such row already existed in production.
    #
    # A subscription that does not state an allowance means "whatever the plan
    # says", never "nothing".
    included = ((sub.included_minutes if sub else 0) or plan.included_minutes) * 60

    db.execute(
        pg_insert(UsagePeriod)
        .values(org_id=org.id, period_start=start, period_end=end,
                included_seconds=included, billable_seconds=0)
        .on_conflict_do_nothing(index_elements=["org_id", "period_start"])
    )
    db.flush()
    return db.get(UsagePeriod, (org.id, start))


# ─────────────────────────────── recording ───────────────────────────────

def record(db, *, org_id: str, call_id: str | None, resource_type: str,
           idempotency_key: str, billable_seconds: int = 0,
           vendor_cost_micros: int = 0, meta: dict | None = None,
           period_start: datetime | None = None,
           now: datetime | None = None) -> bool:
    """Append one usage event and advance the counter. Returns True if new.

    The insert and the counter increment share the caller's transaction, so the
    counter can never drift from the ledger. If the unique key already exists
    the insert is a no-op *and the counter is not touched* — that is what makes
    a re-queued call safe to re-run without billing twice.

    The caller commits. Where that commit sits is load-bearing: see pipeline.py.
    """
    now = now or datetime.now(timezone.utc)

    org = _org(db, org_id)
    period = current_period(db, org, now=now)
    period_start = period_start or period.period_start

    inserted = db.execute(
        pg_insert(UsageEvent)
        .values(
            id=_uuid(), org_id=org_id, call_id=call_id,
            resource_type=resource_type,
            billable_seconds=max(0, billable_seconds),
            vendor_cost_micros=max(0, vendor_cost_micros),
            meta=meta, period_start=period_start,
            idempotency_key=idempotency_key, created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(UsageEvent.id)
    ).first()

    if inserted is None:
        logger.info("Usage already recorded for %s — not billing twice",
                    idempotency_key)
        return False

    if billable_seconds:
        # Atomic increment in SQL, NOT a Python read-modify-write.
        #
        # `current_period` reads the row with a plain SELECT and
        # ON CONFLICT DO NOTHING takes no lock on a conflicting row, so four
        # pipeline workers metering the same period would each read the same
        # value and write back their own total — the last writer wins and the
        # rest of the minutes are never billed. The ledger kept every event;
        # the counter, which is what the invoice reads, silently lost them.
        db.execute(
            update(UsagePeriod)
            .where(UsagePeriod.org_id == org_id,
                   UsagePeriod.period_start == period_start)
            .values(billable_seconds=UsagePeriod.billable_seconds
                                     + max(0, billable_seconds))
        )
        # The ORM object still holds the pre-increment value; drop it so any
        # later read in this session sees what the database actually has.
        db.expire(period, ["billable_seconds"])
    return True


def _uuid() -> str:
    import uuid
    return str(uuid.uuid4())


def _org(db, org_id: str):
    from models import Organization
    org = db.get(Organization, org_id)
    if org is None:
        raise ValueError(f"Unknown org {org_id!r} — cannot meter usage")
    return org


# ─────────────────────────────── reading ───────────────────────────────

class UsageSummary:
    """What every usage surface renders from. One primary-key read."""

    def __init__(self, plan, period: UsagePeriod | None, subscription, in_flight: int = 0):
        self.plan = plan
        self.subscription = subscription
        self.in_flight = in_flight
        self.used_seconds = (period.billable_seconds if period else 0) or 0
        self.included_seconds = (
            period.included_seconds if period else plan.included_minutes * 60
        ) or 0
        self.period_end = period.period_end if period else None

    @property
    def used_minutes(self) -> int:
        return minutes_from_seconds(self.used_seconds)

    @property
    def included_minutes(self) -> int:
        return minutes_from_seconds(self.included_seconds)

    @property
    def percent(self) -> int:
        if not self.included_seconds:
            return 0
        return min(999, round(self.used_seconds / self.included_seconds * 100))

    @property
    def remaining_minutes(self) -> int:
        return max(0, self.included_minutes - self.used_minutes)

    @property
    def over(self) -> bool:
        return self.used_seconds >= self.included_seconds > 0

    @property
    def overage_minutes(self) -> int:
        return max(0, self.used_minutes - self.included_minutes)

    @property
    def is_trial(self) -> bool:
        return self.plan.hard_stop


def summary_for(db, org) -> UsageSummary:
    from models import Call
    sub = db.get(Subscription, org.id)
    plan = get_plan(sub.plan_code if sub else None)
    period = current_period(db, org)
    in_flight = db.execute(
        select(func.count(Call.id)).where(
            Call.org_id == org.id,
            Call.status.in_(("pending", "transcribing", "analyzing")),
        )
    ).scalar() or 0
    return UsageSummary(plan, period, sub, in_flight)


# ────────────────────────────── entitlement ──────────────────────────────

class Blocked(Exception):
    """Raised when an org may not upload. Carries copy the route can render."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.message = message
        self.reason = reason


def check_can_upload(db, org) -> UsageSummary:
    """May this org upload? Returns the summary; raises Blocked if not.

    This is a question about *payment state*, deliberately not about volume:

    * a paid plan over its allowance is never blocked — that is what soft
      overage means, and blocking a QA manager mid-audit is how a B2B account
      gets churned;
    * a trial out of minutes *is* blocked, because that is the conversion
      moment and the alternative is a free product;
    * a subscription Stripe says is canceled or unpaid is blocked regardless of
      remaining minutes.
    """
    s = summary_for(db, org)
    status = s.subscription.status if s.subscription else "trialing"

    if status in ("canceled", "unpaid", "incomplete_expired"):
        raise Blocked(
            "This organization's subscription is no longer active. "
            "Update your billing details to start uploading again.",
            reason="subscription_inactive",
        )

    if s.plan.hard_stop and s.over:
        raise Blocked(
            f"You've used all {s.included_minutes} minutes of your free trial. "
            "Choose a plan to keep uploading — your existing reports stay put.",
            reason="trial_exhausted",
        )

    return s


# ──────────────────────────── warning thresholds ────────────────────────────

def thresholds_crossed(db, org, *, now: datetime | None = None) -> list[str]:
    """Which usage warnings became true just now, marking them sent.

    Returns at most one of "80"/"100" per call, and marks it so a customer is
    told once rather than on every upload. Called after usage is recorded —
    including on the failure path, since a failed grade still consumed minutes.
    """
    now = now or datetime.now(timezone.utc)
    period = current_period(db, org, now=now)
    if not period.included_seconds:
        return []

    ratio = period.billable_seconds / period.included_seconds
    fired = []
    if ratio >= 1 and period.warn_100_sent_at is None:
        period.warn_100_sent_at = now
        fired.append("100")
    elif ratio >= 0.8 and period.warn_80_sent_at is None:
        period.warn_80_sent_at = now
        fired.append("80")
    return fired
