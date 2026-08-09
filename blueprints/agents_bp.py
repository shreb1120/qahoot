"""
Agents blueprint — manage call agents (reps) for the org.

GET  /agents/          — list agents (all members see; admins get add/delete)
POST /agents/add       — add agent (admin only)
POST /agents/<id>/delete — delete agent (admin only)
"""

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
from models import Agent

agents_bp = Blueprint("agents", __name__, url_prefix="/agents")


@agents_bp.get("/")
@org_required
def list_agents():
    agents = (
        g.db.query(Agent)
        .filter_by(org_id=g.org.id)
        .order_by(Agent.name)
        .all()
    )
    return render_template("agents/list.html", agents=agents)


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
