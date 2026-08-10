"""Manager sign-off on graded calls.

Two things used to be one thing, and the conflation is what made the dashboard
unusable: a call left the review queue as soon as *any* per-item override
existed. So a manager who read a call and agreed with every line had nothing to
override and could never clear it — the ordinary case — while correcting a
single item out of six silently cleared a call they were halfway through.

They are now separate, and the distinction is worth holding onto:

    override   "the grader was wrong about this requirement"
               Occasional. Factual correction. Changes the score, and
               therefore the agent's scorecard.

    sign-off   "I have dealt with this call"
               Every reviewed call. A judgment about consequence, not about
               what was said. Clears the queue and changes no score.

That second sentence is the one users get wrong, so the UI says it out loud
where a manager dismisses a call.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from models import Call, Report

CONFIRMED = "confirmed"
DISMISSED = "dismissed"
OUTCOMES = (CONFIRMED, DISMISSED)

# Copy lives here rather than in the template so the queue, the report page and
# any future digest all describe an outcome the same way.
OUTCOME_LABEL = {
    CONFIRMED: "Confirmed",
    DISMISSED: "Dismissed",
}
OUTCOME_BLURB = {
    CONFIRMED: "The failure is real and has been acted on.",
    DISMISSED: "Not worth acting on. The agent's score is unchanged.",
}

MAX_NOTE = 2000


def sign_off(db, call: Call, user, outcome: str, note: str | None = None) -> None:
    """Record that a person has dealt with this call.

    Idempotent in effect: re-signing overwrites, which is what someone
    correcting their own mistake expects. The previous reviewer is not
    preserved — a call has one current owner of the decision, and an audit
    trail of every keystroke is not what this is for.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown review outcome {outcome!r}")
    if call.report is None:
        raise ValueError("A call cannot be reviewed before it has been graded")

    call.report.reviewed_at = datetime.now(timezone.utc)
    call.report.reviewed_by_user_id = user.id
    call.report.review_outcome = outcome
    call.report.review_note = (note or "").strip()[:MAX_NOTE] or None


def reopen(db, call: Call) -> None:
    """Put a call back in the queue.

    The note is deliberately kept. Someone reopening a call usually wants to
    see what the last reviewer thought, and throwing it away to make the row
    tidy loses the only context there was.
    """
    if call.report is None:
        return
    call.report.reviewed_at = None
    call.report.reviewed_by_user_id = None
    call.report.review_outcome = None


def _base_query(db, org_id: str):
    return (
        db.query(Call)
        .join(Report, Report.call_id == Call.id)
        .filter(Call.org_id == org_id, Report.reviewed_at.is_(None))
    )


def pending_counts(db, org_id: str) -> dict:
    """How much is waiting, split by whether it needs judgment.

    Every graded call needs sign-off here, which is a deliberate choice — an
    audit trail where a person looked at all of it. That only stays workable if
    the two kinds of waiting are counted separately: failures need a human
    decision one at a time, passes need confirming and can go in bulk. Lumping
    them into one number produces a queue people stop opening.
    """
    rows = db.execute(
        select(Report.verdict, func.count(Report.id))
        .join(Call, Call.id == Report.call_id)
        .where(Call.org_id == org_id, Report.reviewed_at.is_(None))
        .group_by(Report.verdict)
    ).all()
    by_verdict = {v or "unknown": n for v, n in rows}
    needs_judgment = by_verdict.get("fail", 0) + by_verdict.get("critical", 0)
    passes = by_verdict.get("pass", 0) + by_verdict.get("unknown", 0)
    return {
        "needs_judgment": needs_judgment,
        "passes": passes,
        "total": needs_judgment + passes,
    }


def pending(db, org_id: str, *, verdicts: tuple[str, ...] | None = None,
            limit: int | None = None) -> list[Call]:
    """Calls awaiting sign-off, worst first.

    Ordered critical → fail → pass, then oldest first: a compliance failure
    that has been sitting for a week is more urgent than one from this morning,
    which is the opposite of the newest-first ordering used everywhere else in
    the app.
    """
    from sqlalchemy import case
    from sqlalchemy.orm import joinedload

    q = _base_query(db, org_id).options(
        joinedload(Call.agent), joinedload(Call.report)
    )
    if verdicts:
        q = q.filter(Report.verdict.in_(verdicts))

    severity = case(
        (Report.verdict == "critical", 0),
        (Report.verdict == "fail", 1),
        else_=2,
    )
    q = q.order_by(severity, Call.upload_date.asc())
    if limit:
        q = q.limit(limit)
    return q.all()
