"""Abuse limits for endpoints that spend money.

Deliberately **not** a billing feature. Soft overage means the usage quota
never blocks an upload, which leaves no ceiling at all on a compromised
account: a stolen session could queue ten thousand files overnight and every
one of them would be transcribed and graded at our expense. These limits are
that ceiling.

They are therefore set far above any real customer's burst — a QA manager
importing a week of calls must never see one. If a legitimate user hits a
limit here, the limit is wrong, not the user.

Postgres-backed because there is no redis, and incremented with a single
atomic statement so two concurrent uploads cannot both read the same count.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from models import Call, RateLimitCounter

logger = logging.getLogger(__name__)

# Generous by design. At a ~31-minute average call, 120 uploads/hour is over
# 60 hours of audio in an hour — far past any human workflow.
UPLOADS_PER_HOUR_PER_ORG = 120
UPLOADS_PER_HOUR_PER_USER = 60
# In-flight cap. Bounds our own worker pool and vendor concurrency as much as
# it bounds the customer.
MAX_CONCURRENT_PER_ORG = 25
# Written warnings send a whole transcript to the model each time. Far above
# any real day's coaching, far below a bill worth noticing.
WRITEUPS_PER_HOUR_PER_ORG = 40


class RateLimited(Exception):
    """Raised when a limit is hit. Carries copy a route can render."""

    def __init__(self, message: str, *, retry_after: int = 3600):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


def _bump(db, scope_key: str, window: timedelta, *, amount: int = 1,
          now: datetime | None = None) -> int:
    """Increment a fixed-window counter and return the new count.

    One statement, so the read-modify-write race cannot happen. Windows are
    fixed rather than sliding: cruder, but it needs no extra state and the
    limits are set loosely enough that the boundary effect does not matter.
    """
    now = now or datetime.now(timezone.utc)
    seconds = int(window.total_seconds())
    epoch = int(now.timestamp())
    window_start = datetime.fromtimestamp(epoch - (epoch % seconds), tz=timezone.utc)

    row = db.execute(
        pg_insert(RateLimitCounter)
        .values(scope_key=scope_key, window_start=window_start, count=amount)
        .on_conflict_do_update(
            index_elements=["scope_key", "window_start"],
            set_={"count": RateLimitCounter.__table__.c.count + amount},
        )
        .returning(RateLimitCounter.count)
    ).scalar_one()
    return row


def check_upload(db, org, user, *, count: int = 1,
                 now: datetime | None = None) -> None:
    """Raise RateLimited if this upload should be refused.

    Called before any row is created or any file written, so a refusal costs
    nothing. The caller commits — the counters ride along with whatever
    transaction the request is already in.
    """
    in_flight = db.execute(
        select(func.count(Call.id)).where(
            Call.org_id == org.id,
            Call.status.in_(("pending", "transcribing", "analyzing")),
        )
    ).scalar() or 0
    if in_flight >= MAX_CONCURRENT_PER_ORG:
        raise RateLimited(
            f"{in_flight} calls are still processing. Wait for some to finish "
            "before uploading more — they'll clear on their own.",
            retry_after=300,
        )

    # `count` is the size of the batch, not the number of requests.
    #
    # Bulk upload made one request carry many recordings, and a limiter that
    # counted requests would have let a hundred calls through on a single tick —
    # the cap exists to bound vendor spend, and spend follows recordings.
    hour = timedelta(hours=1)
    org_count = _bump(db, f"org:{org.id}:upload", hour, amount=count, now=now)
    user_count = _bump(db, f"user:{user.id}:upload", hour, amount=count, now=now)

    if org_count > UPLOADS_PER_HOUR_PER_ORG or user_count > UPLOADS_PER_HOUR_PER_USER:
        logger.warning(
            "Rate limit hit: org=%s (%d/h) user=%s (%d/h)",
            org.id, org_count, user.id, user_count,
        )
        raise RateLimited(
            "That's a lot of uploads in a short time, so we've paused them "
            "briefly. Try again in an hour, or get in touch if you're doing a "
            "bulk import — we'll lift the limit.",
        )


def check_spend(db, org, user, action: str, *, per_hour: int,
               now: datetime | None = None) -> None:
    """Cap any endpoint that spends vendor money on demand.

    Upload is not the only one. Generating a written warning sends the whole
    transcript to Anthropic on every request, and it sat behind nothing but a
    session — so a stolen cookie, or a member in a loop, could run up a bill
    with no ceiling and no trace in the usage ledger.
    """
    n = _bump(db, f"org:{org.id}:{action}", timedelta(hours=1), now=now)
    if n > per_hour:
        logger.warning("Spend limit hit: org=%s action=%s (%d/h)", org.id, action, n)
        raise RateLimited(
            "That's a lot of requests in a short time, so we've paused them "
            "briefly. Try again in an hour, or get in touch if you need more."
        )


def purge_old_windows(db, *, older_than: timedelta = timedelta(days=2)) -> int:
    """Delete expired counters. Called by the nightly job; nothing reads them."""
    cutoff = datetime.now(timezone.utc) - older_than
    deleted = (
        db.query(RateLimitCounter)
        .filter(RateLimitCounter.window_start < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted
