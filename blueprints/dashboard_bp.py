"""
Dashboard blueprint — the main authenticated area.

Phase 1: stub page confirming the user is in and their org is loaded.
Phase 2: call upload, analysis results, and history live here.
"""
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, g, render_template
from sqlalchemy import Date, case, cast, func

from auth import org_required
from models import Agent, Call, Report

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.get("/")
@org_required
def index():
    db = g.db
    org_id = g.org.id

    total_calls = db.query(func.count(Call.id)).filter(Call.org_id == org_id).scalar() or 0

    completed = db.query(func.count(Call.id)).filter(
        Call.org_id == org_id, Call.status == "complete"
    ).scalar() or 0

    pass_count = db.query(func.count(Report.id)).join(Call).filter(
        Call.org_id == org_id,
        Report.pass_fail_status.ilike("%PASS%"),
        ~Report.pass_fail_status.ilike("%FAIL%"),
    ).scalar() or 0

    fail_count = db.query(func.count(Report.id)).join(Call).filter(
        Call.org_id == org_id,
        Report.pass_fail_status.ilike("%FAIL%"),
        ~Report.pass_fail_status.ilike("%CRITICAL%"),
    ).scalar() or 0

    critical_count = db.query(func.count(Report.id)).join(Call).filter(
        Call.org_id == org_id,
        Report.pass_fail_status.ilike("%CRITICAL%"),
    ).scalar() or 0

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
        .filter(Call.org_id == org_id)
        .order_by(Call.upload_date.desc())
        .limit(6)
        .all()
    )

    # ── Needs review ──
    # A failed call nobody has ruled on yet is the only thing on this page that
    # is actually a task. Overrides are JSONB, so emptiness is settled in Python
    # rather than with a fragile SQL comparison; the query is bounded first.
    failing = (
        db.query(Call)
        .join(Report, Report.call_id == Call.id)
        .filter(Call.org_id == org_id, Report.pass_fail_status.ilike("%FAIL%"))
        .order_by(Call.upload_date.desc())
        .limit(40)
        .all()
    )
    unreviewed = [c for c in failing if not (c.report and c.report.overrides_json)]
    needs_review = unreviewed[:5]
    needs_review_total = len(unreviewed)

    # ── Per-agent performance ──
    # The comparison a QA manager actually opens this tool to make. Only agents
    # with a graded call appear; an agent with none has nothing to compare.
    is_pass = case(
        (Report.pass_fail_status.ilike("%PASS%") & ~Report.pass_fail_status.ilike("%FAIL%"), 1),
        else_=0,
    )
    is_critical = case((Report.pass_fail_status.ilike("%CRITICAL%"), 1), else_=0)
    agent_rows = (
        db.query(
            Agent.name.label("name"),
            func.count(Report.id).label("graded"),
            func.sum(is_pass).label("passed"),
            func.sum(is_critical).label("critical"),
        )
        .join(Call, Call.agent_id == Agent.id)
        .join(Report, Report.call_id == Call.id)
        .filter(Agent.org_id == org_id)
        .group_by(Agent.id, Agent.name)
        .all()
    )
    agent_stats = sorted(
        (
            {
                "name": r.name,
                "graded": r.graded,
                "passed": int(r.passed or 0),
                "critical": int(r.critical or 0),
                "pass_rate": round((r.passed or 0) / r.graded * 100) if r.graded else 0,
            }
            for r in agent_rows
        ),
        key=lambda a: (a["pass_rate"], a["graded"]),
    )

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
        needs_review_total=needs_review_total,
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
