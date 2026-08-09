"""
Org blueprint — org creation, member management, and invite links.

Design notes:
- Invite links are token-based (no email sending in Phase 1).  The admin
  generates a link and shares it however they like.
- Only one org per user for now.  If a user tries to accept an invite for a
  different org than the one they already belong to, they get a conflict page.
- Org creation immediately promotes the creator to admin.
"""
import secrets
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from auth import admin_required, login_required, org_required
from models import ComplianceProfile, OrgInvite, Organization, User
from templates_seed import TEMPLATE_LIBRARY, get_template

org_bp = Blueprint("org", __name__, url_prefix="/org")

_INVITE_TTL_DAYS = 7


@org_bp.get("/setup")
@login_required
def setup():
    if g.org:
        return redirect(url_for("dashboard.index"))
    return render_template("org/setup.html", library=TEMPLATE_LIBRARY)


@org_bp.post("/setup")
@login_required
def setup_post():
    if g.org:
        return redirect(url_for("dashboard.index"))

    name = request.form.get("org_name", "").strip()
    if not name:
        return render_template("org/setup.html", library=TEMPLATE_LIBRARY,
                               error="Organization name is required."), 400

    template_choice = request.form.get("template", "blank")

    db = g.db
    try:
        org = Organization(name=name)
        db.add(org)
        db.flush()  # Populate org.id before using it below.

        # Creator becomes admin of the new org.
        user = db.merge(g.user)
        user.org_id = org.id
        user.role = "admin"

        # Seed from the shared library so signup and the in-app switcher
        # cannot drift apart.
        import copy
        chosen = get_template(template_choice) or get_template("blank")
        seed_data = copy.deepcopy(chosen["checklist"])
        profile_name = chosen["name"] if chosen["key"] != "blank" else "Custom checklist"
        profile = ComplianceProfile(
            org_id=org.id,
            name=profile_name,
            script_sections_json=seed_data,
            is_active=True,
        )
        db.add(profile)
        db.commit()
        return redirect(url_for("dashboard.index"))
    except Exception:
        db.rollback()
        raise


@org_bp.get("/settings")
@admin_required
def settings():
    db = g.db
    members = db.query(User).filter_by(org_id=g.org.id).order_by(User.created_at).all()
    now = datetime.now(timezone.utc)
    invites = (
        db.query(OrgInvite)
        .filter_by(org_id=g.org.id, accepted=False)
        .filter(OrgInvite.expires_at > now)
        .order_by(OrgInvite.created_at.desc())
        .all()
    )
    return render_template("org/settings.html", members=members, invites=invites)


@org_bp.post("/invite")
@admin_required
def invite():
    """Generate an invite link valid for 7 days. Returns JSON {invite_url}."""
    invited_email = request.form.get("email", "").strip() or None
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=_INVITE_TTL_DAYS)

    db = g.db
    try:
        invite_record = OrgInvite(
            org_id=g.org.id,
            invited_email=invited_email,
            token=token,
            role="member",
            expires_at=expires_at,
        )
        db.add(invite_record)
        db.commit()
    except Exception:
        db.rollback()
        raise

    invite_url = url_for("org.accept_invite", token=token, _external=True)
    return jsonify({"invite_url": invite_url})


@org_bp.get("/join/<token>")
@login_required
def accept_invite(token: str):
    """User follows invite link → joins the org as a member."""
    db = g.db
    now = datetime.now(timezone.utc)
    invite_record = (
        db.query(OrgInvite)
        .filter_by(token=token, accepted=False)
        .filter(OrgInvite.expires_at > now)
        .first()
    )

    if not invite_record:
        return render_template("org/invite_invalid.html"), 410

    # Don't let someone join a second org.
    if g.user.org_id and g.user.org_id != invite_record.org_id:
        return render_template("org/invite_conflict.html", org=g.org), 409

    try:
        user = db.merge(g.user)
        user.org_id = invite_record.org_id
        user.role = invite_record.role
        invite_record.accepted = True
        db.commit()
    except Exception:
        db.rollback()
        raise

    return redirect(url_for("dashboard.index"))
