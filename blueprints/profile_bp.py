"""
Compliance profile editor blueprint.

One active profile per org.  Admin can add/delete sections, items, and
auto-fail phrases.  All edits are read-modify-write on the JSONB blob.
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
from models import ComplianceProfile

profile_bp = Blueprint("profile", __name__, url_prefix="/profile")


def _get_active_profile(db, org_id: str) -> ComplianceProfile | None:
    return db.query(ComplianceProfile).filter_by(
        org_id=org_id, is_active=True
    ).first()


def _save_profile(db, profile: ComplianceProfile) -> None:
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(profile, "script_sections_json")
    db.commit()


# ── View ──────────────────────────────────────────────────────────────────────

@profile_bp.get("/")
@org_required
def view():
    profile = _get_active_profile(g.db, g.org.id)
    return render_template("profile/edit.html", profile=profile)


# ── Templates ─────────────────────────────────────────────────────────────────

@profile_bp.get("/templates")
@org_required
def templates():
    """The starter library. Readable by anyone in the org; only an admin can apply."""
    import copy
    from templates_seed import TEMPLATE_LIBRARY, template_stats

    profile = _get_active_profile(g.db, g.org.id)
    current = (profile.script_sections_json or {}) if profile else {}
    # A profile applied from the library carries that template's name, so the
    # card in use can be marked. A hand-built or heavily edited checklist
    # matches nothing, which is the honest answer.
    active_name = profile.name if profile else None
    library = [
        {**t, "stats": template_stats(t["checklist"]),
         "preview": _preview_items(t["checklist"]),
         "in_use": bool(active_name) and t["name"] == active_name}
        for t in TEMPLATE_LIBRARY
    ]
    return render_template(
        "profile/templates.html",
        library=library,
        profile=profile,
        current_stats=template_stats(current) if profile else None,
        matched=any(t["in_use"] for t in library),
    )


def _preview_items(checklist: dict, limit: int = 4) -> list[str]:
    """A few real requirement names, so a card shows the template rather than
    describing it."""
    out = []
    for section in checklist.get("sections", []) or []:
        for item in section.get("items", []) or []:
            out.append(item.get("name", ""))
            if len(out) >= limit:
                return out
    return out


@profile_bp.post("/templates/<key>/apply")
@admin_required
def apply_template(key: str):
    """Switch the org onto a starter template.

    Applying creates a NEW active profile and deactivates the previous one
    rather than overwriting it. Two reasons: the old checklist stays recoverable
    after a misclick, and every existing Report keeps pointing at the profile
    that actually graded it — a report must always be explainable against the
    checklist in force at the time.
    """
    import copy
    from templates_seed import get_template

    template = get_template(key)
    if template is None:
        abort(404)

    db = g.db
    previous = _get_active_profile(db, g.org.id)
    if previous is not None:
        previous.is_active = False

    profile = ComplianceProfile(
        org_id=g.org.id,
        name=template["name"] if key != "blank" else "Custom checklist",
        script_sections_json=copy.deepcopy(template["checklist"]),
        is_active=True,
    )
    db.add(profile)
    db.commit()

    if key == "blank":
        flash("Started a blank checklist. Add your first section below.", "success")
    else:
        flash(f"Switched to the {template['name']} template. Edit it to match "
              f"your own requirements.", "success")
    return redirect(url_for("profile.view"))


# ── Sections ──────────────────────────────────────────────────────────────────

@profile_bp.post("/sections/add")
@admin_required
def add_section():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Section name is required.", "error")
        return redirect(url_for("profile.view"))

    profile = _get_active_profile(g.db, g.org.id)
    if not profile:
        abort(404)

    data = profile.script_sections_json
    key = name.lower().replace(" ", "_").replace("-", "_")
    data["sections"].append({"name": name, "key": key, "items": []})
    profile.script_sections_json = data
    _save_profile(g.db, profile)
    return redirect(url_for("profile.view"))


@profile_bp.post("/sections/<int:section_idx>/delete")
@admin_required
def delete_section(section_idx: int):
    profile = _get_active_profile(g.db, g.org.id)
    if not profile:
        abort(404)

    data = profile.script_sections_json
    sections = data.get("sections", [])
    if section_idx < 0 or section_idx >= len(sections):
        abort(400)

    sections.pop(section_idx)
    data["sections"] = sections
    profile.script_sections_json = data
    _save_profile(g.db, profile)
    return redirect(url_for("profile.view"))


# ── Items ─────────────────────────────────────────────────────────────────────

@profile_bp.post("/sections/<int:section_idx>/items/add")
@admin_required
def add_item(section_idx: int):
    name = request.form.get("name", "").strip()
    if not name:
        flash("Item name is required.", "error")
        return redirect(url_for("profile.view"))

    required = request.form.get("required", "true") != "false"
    notes = request.form.get("notes", "").strip()

    profile = _get_active_profile(g.db, g.org.id)
    if not profile:
        abort(404)

    data = profile.script_sections_json
    sections = data.get("sections", [])
    if section_idx < 0 or section_idx >= len(sections):
        abort(400)

    sections[section_idx]["items"].append({
        "name": name,
        "required": required,
        "notes": notes,
    })
    data["sections"] = sections
    profile.script_sections_json = data
    _save_profile(g.db, profile)
    return redirect(url_for("profile.view"))


@profile_bp.post("/sections/<int:section_idx>/items/<int:item_idx>/edit")
@admin_required
def edit_item(section_idx: int, item_idx: int):
    profile = _get_active_profile(g.db, g.org.id)
    if not profile:
        abort(404)

    name = request.form.get("name", "").strip()
    if not name:
        flash("Item name is required.", "error")
        return redirect(url_for("profile.view"))

    required = request.form.get("required") == "true"
    notes = request.form.get("notes", "").strip()

    data = profile.script_sections_json
    sections = data.get("sections", [])
    if section_idx < 0 or section_idx >= len(sections):
        abort(400)
    items = sections[section_idx].get("items", [])
    if item_idx < 0 or item_idx >= len(items):
        abort(400)

    items[item_idx]["name"] = name
    items[item_idx]["required"] = required
    items[item_idx]["notes"] = notes
    sections[section_idx]["items"] = items
    data["sections"] = sections
    profile.script_sections_json = data
    _save_profile(g.db, profile)
    return redirect(url_for("profile.view"))


@profile_bp.post("/sections/<int:section_idx>/items/<int:item_idx>/delete")
@admin_required
def delete_item(section_idx: int, item_idx: int):
    profile = _get_active_profile(g.db, g.org.id)
    if not profile:
        abort(404)

    data = profile.script_sections_json
    sections = data.get("sections", [])
    if section_idx < 0 or section_idx >= len(sections):
        abort(400)
    items = sections[section_idx].get("items", [])
    if item_idx < 0 or item_idx >= len(items):
        abort(400)

    items.pop(item_idx)
    sections[section_idx]["items"] = items
    data["sections"] = sections
    profile.script_sections_json = data
    _save_profile(g.db, profile)
    return redirect(url_for("profile.view"))


# ── Auto-fail phrases ─────────────────────────────────────────────────────────

@profile_bp.post("/phrases/add")
@admin_required
def add_phrase():
    phrase = request.form.get("phrase", "").strip()
    if not phrase:
        flash("Phrase is required.", "error")
        return redirect(url_for("profile.view"))

    description = request.form.get("description", "").strip()

    profile = _get_active_profile(g.db, g.org.id)
    if not profile:
        abort(404)

    data = profile.script_sections_json
    data.setdefault("auto_fail_phrases", []).append({
        "phrase": phrase,
        "description": description,
    })
    profile.script_sections_json = data
    _save_profile(g.db, profile)
    return redirect(url_for("profile.view"))


@profile_bp.post("/phrases/<int:phrase_idx>/delete")
@admin_required
def delete_phrase(phrase_idx: int):
    profile = _get_active_profile(g.db, g.org.id)
    if not profile:
        abort(404)

    data = profile.script_sections_json
    phrases = data.get("auto_fail_phrases", [])
    if phrase_idx < 0 or phrase_idx >= len(phrases):
        abort(400)

    phrases.pop(phrase_idx)
    data["auto_fail_phrases"] = phrases
    profile.script_sections_json = data
    _save_profile(g.db, profile)
    return redirect(url_for("profile.view"))
