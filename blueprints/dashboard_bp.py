"""
Dashboard blueprint — the main authenticated area.

Headline counts come from `stats.verdict_counts`, the one place that decides
what a pass, a failure and a critical failure are. Everything user-facing that
counts calls has to go through it, or two pages start reporting different
numbers for the same data.
"""
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, g, render_template
from sqlalchemy import Date, cast, func
from sqlalchemy.orm import joinedload

import review
import stats
from auth import org_required
from stats import agent_performance_rows
from models import Agent, Call, Report

dashboard_bp = Blueprint("dashboard", __name__)


# `/` now belongs to the marketing page, which has to be reachable by someone
# who has never signed in. Every reference to this view goes through
# url_for("dashboard.index"), so moving the path is transparent.
@dashboard_bp.get("/dashboard")
@org_required
def index():
    db = g.db
    org_id = g.org.id

    total_calls = db.query(func.count(Call.id)).filter(Call.org_id == org_id).scalar() or 0

    completed = db.query(func.count(Call.id)).filter(
        Call.org_id == org_id, Call.status == "complete"
    ).scalar() or 0

    # One aggregate, one definition. These were three separate queries, each
    # pattern-matching `pass_fail_status` with ILIKE — which can never use an
    # index, and ignored the `verdict` column the normalizer writes. That made
    # this the third copy of a predicate that also lived in stats.py and in the
    # history view, and the copies disagreed for any report saved before the
    # normalizer existed.
    verdicts = stats.verdict_counts(
        db.query(Call).join(Report, Report.call_id == Call.id)
        .filter(Call.org_id == org_id)
    )
    pass_count = verdicts["passed"]
    fail_count = verdicts["failed"]
    critical_count = verdicts["critical"]

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    this_week = db.query(func.count(Call.id)).filter(
        Call.org_id == org_id, Call.upload_date >= week_ago
    ).scalar() or 0

    processing = db.query(func.count(Call.id)).filter(
        Call.org_id == org_id, Call.status.in_(["pending", "transcribing", "analyzing"])
    ).scalar() or 0

    pass_rate = round(pass_count / completed * 100) if completed > 0 else None

    # Recent activity feed — latest calls with their agent + report (if any).
    recent_calls = (
        db.query(Call)
        .options(joinedload(Call.agent), joinedload(Call.report))
        .filter(Call.org_id == org_id)
        .order_by(Call.upload_date.desc())
        .limit(6)
        .all()
    )

    # ── Needs review ──
    # The only thing on this page that is actually a task.
    #
    # This used to read "a failed call with no per-item override", which meant
    # a manager who read a call and agreed with every line could never clear
    # it — there was nothing to override — while correcting one item silently
    # cleared a call they were halfway through. Sign-off is now its own thing;
    # see review.py.
    review_counts = review.pending_counts(db, org_id)
    needs_review = review.pending(db, org_id, verdicts=("critical", "fail"), limit=5)

    # ── Per-agent performance ──
    # The comparison a QA manager actually opens this tool to make. Shared with
    # the agents list and the agent profile so the three cannot disagree.
    agent_stats = agent_performance_rows(db, org_id)

    # ── 14-day upload volume ──
    since = datetime.now(timezone.utc) - timedelta(days=13)
    volume_rows = dict(
        db.query(cast(Call.upload_date, Date), func.count(Call.id))
        .filter(Call.org_id == org_id, Call.upload_date >= since)
        .group_by(cast(Call.upload_date, Date))
        .all()
    )
    today = date.today()
    volume = [
        {"day": today - timedelta(days=13 - i),
         "count": volume_rows.get(today - timedelta(days=13 - i), 0)}
        for i in range(14)
    ]
    volume_peak = max((v["count"] for v in volume), default=0)

    return render_template(
        "dashboard.html",
        user=g.user,
        org=g.org,
        recent_calls=recent_calls,
        needs_review=needs_review,
        review_counts=review_counts,
        agent_stats=agent_stats,
        volume=volume,
        volume_peak=volume_peak,
        stats={
            "total": total_calls,
            "completed": completed,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "critical_count": critical_count,
            "pass_rate": pass_rate,
            "this_week": this_week,
            "processing": processing,
        },
    )
