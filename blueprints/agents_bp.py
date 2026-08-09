"""
Agents blueprint — manage call agents (reps) for the org.

GET  /agents/            — list agents with pass rates (all members)
GET  /agents/<id>        — one agent's performance and weak spots (all members)
POST /agents/add         — add agent (admin only)
POST /agents/import      — bulk add from CSV (admin only)
POST /agents/<id>/delete — delete agent (admin only)
"""

from sqlalchemy.orm import joinedload

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from auth import admin_required, org_required
from agent_csv import MAX_NAME, dedupe_key, parse_agent_csv
from models import Agent, Call, ComplianceProfile
from stats import MISS_SAMPLE_LIMIT, agent_performance_rows, call_score, most_missed

agents_bp = Blueprint("agents", __name__, url_prefix="/agents")


def _escape_like(term: str) -> str:
    """Neutralise LIKE wildcards so a search for "50%" doesn't match everything."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@agents_bp.get("/")
@org_required
def list_agents():
    q = (request.args.get("q") or "").strip()
    query = g.db.query(Agent).filter_by(org_id=g.org.id)
    if q:
        # escape() so a customer searching for a literal % or _ gets what they
        # typed rather than a wildcard.
        query = query.filter(Agent.name.ilike(f"%{_escape_like(q)}%", escape="\\"))
    agents = query.order_by(Agent.name).all()

    # Attach each agent's numbers from the shared helper, so this table and the
    # dashboard's agent panel cannot report different pass rates.
    perf = {row["id"]: row for row in agent_performance_rows(g.db, g.org.id)}
    rows = [{"agent": a, "perf": perf.get(a.id)} for a in agents]

    return render_template("agents/list.html", agents=agents, rows=rows, q=q,
                           total=g.db.query(Agent).filter_by(org_id=g.org.id).count())


@agents_bp.get("/<agent_id>")
@org_required
def agent_profile(agent_id: str):
    """One agent's scorecard: headline numbers, weak spots, and their calls."""
    db, org_id = g.db, g.org.id

    # Scoped lookup first — same shape as delete_agent. Everything below reads
    # from this agent, so an out-of-org id can never reach a query.
    agent = db.query(Agent).filter_by(id=agent_id, org_id=org_id).first()
    if not agent:
        abort(404)

    # Headline counters come from SQL and stay exact regardless of the sample cap.
    perf = agent_performance_rows(db, org_id, agent_id=agent_id)
    stats = perf[0] if perf else {"graded": 0, "passed": 0, "critical": 0, "pass_rate": None}

    total_calls = db.query(Call).filter_by(org_id=org_id, agent_id=agent_id).count()

    # Bounded read for the per-requirement breakdown; joinedload avoids N+1.
    calls = (
        db.query(Call)
        .options(joinedload(Call.report))
        .filter(Call.org_id == org_id, Call.agent_id == agent_id)
        .order_by(Call.upload_date.desc())
        .limit(MISS_SAMPLE_LIMIT)
        .all()
    )
    graded_calls = [c for c in calls if c.report is not None]

    active = (
        db.query(ComplianceProfile)
        .filter_by(org_id=org_id, is_active=True)
        .first()
    )
    misses = most_missed(graded_calls, active.script_sections_json if active else None)

    recent = [
        {"call": c, "score": call_score(c.report.report_json) if c.report else None}
        for c in calls[:10]
    ]

    return render_template(
        "agents/profile.html",
        agent=agent,
        stats=stats,
        total_calls=total_calls,
        misses=misses,
        recent=recent,
        sampled=len(graded_calls),
        capped=total_calls > MISS_SAMPLE_LIMIT,
        sample_limit=MISS_SAMPLE_LIMIT,
        checklist_name=active.name if active else None,
    )


@agents_bp.post("/add")
@admin_required
def add_agent():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Agent name is required.", "error")
        return redirect(url_for("agents.list_agents"))

    if len(name) > MAX_NAME:
        flash(f"That name is too long ({MAX_NAME} characters maximum).", "error")
        return redirect(url_for("agents.list_agents"))

    existing = {dedupe_key(a.name) for a in
                g.db.query(Agent).filter_by(org_id=g.org.id).all()}
    if dedupe_key(name) in existing:
        flash(f"{name} is already on your agent list.", "error")
        return redirect(url_for("agents.list_agents"))

    agent = Agent(org_id=g.org.id, name=name)
    g.db.add(agent)
    g.db.commit()
    flash(f'Agent "{name}" added.', "success")
    return redirect(url_for("agents.list_agents"))


@agents_bp.post("/<agent_id>/delete")
@admin_required
def delete_agent(agent_id: str):
    agent = g.db.query(Agent).filter_by(id=agent_id, org_id=g.org.id).first()
    if not agent:
        abort(404)
    name = agent.name
    g.db.delete(agent)
    g.db.commit()
    flash(f'Agent "{name}" deleted.', "success")
    return redirect(url_for("agents.list_agents"))


@agents_bp.post("/import")
@admin_required
def import_agents():
    """Bulk-add agents from a CSV of names.

    Duplicates are skipped rather than rejected so the same file can be
    re-uploaded safely after a partial roster change. A fatal parse problem
    imports nothing at all.
    """
    f = request.files.get("file")
    if f is None or not f.filename:
        flash("Choose a CSV file to import.", "error")
        return redirect(url_for("agents.list_agents"))

    parsed = parse_agent_csv(f.read())
    if parsed["error"]:
        flash(parsed["error"], "error")
        return redirect(url_for("agents.list_agents"))

    # Existing names, plus names seen earlier in this same file — a file that
    # lists someone twice should add them once.
    seen = {dedupe_key(a.name) for a in
            g.db.query(Agent).filter_by(org_id=g.org.id).all()}

    added, skipped = [], 0
    for name in parsed["names"]:
        key = dedupe_key(name)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        added.append(Agent(org_id=g.org.id, name=name))

    if added:
        g.db.add_all(added)
        g.db.commit()          # one commit: no half-imported file

    invalid = parsed["invalid"]
    parts = [f"Added {len(added)}"]
    if skipped:
        parts.append(f"skipped {skipped} already on your list")
    if invalid:
        parts.append(f"{invalid} invalid row{'s' if invalid != 1 else ''}")
    message = ", ".join(parts) + "."
    if parsed["invalid_rows"]:
        rows = ", ".join(str(n) for n in parsed["invalid_rows"])
        message += f" Row{'s' if invalid != 1 else ''} {rows} had no usable name."

    flash(message, "success" if added else "error")
    return redirect(url_for("agents.list_agents"))
