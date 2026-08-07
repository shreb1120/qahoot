"""
Dashboard blueprint — the main authenticated area.

Phase 1: stub page confirming the user is in and their org is loaded.
Phase 2: call upload, analysis results, and history live here.
"""
from flask import Blueprint, g, render_template

from auth import org_required

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.get("/")
@org_required
def index():
    return render_template("dashboard.html", user=g.user, org=g.org)
