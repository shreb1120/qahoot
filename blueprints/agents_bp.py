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
