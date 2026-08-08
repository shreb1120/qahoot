"""
Dashboard blueprint — the main authenticated area.

Phase 1: stub page confirming the user is in and their org is loaded.
Phase 2: call upload, analysis results, and history live here.
"""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, render_template
from sqlalchemy import func

from auth import org_required
from models import Call, Report

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

    return render_template(
        "dashboard.html",
        user=g.user,
        org=g.org,
        recent_calls=recent_calls,
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
