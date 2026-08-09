"""The starter template library and switching between templates."""
import pytest
from templates_seed import TEMPLATE_LIBRARY, TEMPLATES_BY_KEY, get_template, template_stats


def test_every_template_matches_the_stored_checklist_shape():
    for t in TEMPLATE_LIBRARY:
        c = t["checklist"]
        assert set(c) == {"sections", "auto_fail_phrases"}, t["key"]
        for s in c["sections"]:
            assert {"name", "key", "items"} <= set(s), t["key"]
            for i in s["items"]:
                assert {"name", "required", "notes"} <= set(i), (t["key"], i)
        for p in c["auto_fail_phrases"]:
            assert {"phrase", "description"} <= set(p), t["key"]


def test_keys_are_unique_and_blank_is_last():
    keys = [t["key"] for t in TEMPLATE_LIBRARY]
    assert len(keys) == len(set(keys))
    assert keys[-1] == "blank", "the blank option should close the list"


def test_stats_avoid_the_jinja_items_collision():
    """`stats.items` would resolve to dict.items in a template and render a
    Python repr — the same bug that once shipped on the upload page."""
    s = template_stats(TEMPLATES_BY_KEY["debt_settlement"]["checklist"])
    assert "items" not in s and "item_count" in s
    assert s["item_count"] == 33 and s["phrases"] == 11


def test_blank_template_is_actually_empty():
    c = TEMPLATES_BY_KEY["blank"]["checklist"]
    assert c["sections"] == [] and c["auto_fail_phrases"] == []


def test_gallery_is_readable_by_any_member(tenants):
    r = tenants.a_member.get("/profile/templates")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Solar sales" in body and "Credit repair" in body
    assert "Use this template" not in body, "a member must not get apply controls"


def test_applying_a_template_is_admin_only(tenants, db):
    from models import ComplianceProfile
    before = db.query(ComplianceProfile).filter_by(org_id=tenants.a["org"]).count()
    r = tenants.a_member.post("/profile/templates/solar/apply")
    assert r.status_code == 403
    db.expire_all()
    assert db.query(ComplianceProfile).filter_by(org_id=tenants.a["org"]).count() == before


def test_applying_a_template_switches_the_active_profile(tenants, db):
    from models import ComplianceProfile
    r = tenants.a_admin.post("/profile/templates/solar/apply")
    assert r.status_code == 302

    db.expire_all()
    profiles = db.query(ComplianceProfile).filter_by(org_id=tenants.a["org"]).all()
    active = [p for p in profiles if p.is_active]
    assert len(active) == 1, "exactly one profile may be active"
    assert active[0].name == "Solar sales"
    assert any(s["key"] == "identification" for s in active[0].script_sections_json["sections"])


def test_applying_is_not_destructive(tenants, db):
    """The previous checklist is deactivated, never deleted — a misclick must be
    recoverable, and reports must stay explainable against what graded them."""
    from models import ComplianceProfile
    before = db.query(ComplianceProfile).filter_by(org_id=tenants.a["org"]).all()
    old_id, old_name = before[0].id, before[0].name

    tenants.a_admin.post("/profile/templates/tax_relief/apply")

    db.expire_all()
    old = db.query(ComplianceProfile).filter_by(id=old_id).first()
    assert old is not None, "the previous checklist was deleted"
    assert old.is_active is False
    assert old.name == old_name


def test_existing_reports_keep_pointing_at_the_profile_that_graded_them(tenants, db):
    from models import Call
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    original_profile_id = call.compliance_profile_id

    tenants.a_admin.post("/profile/templates/insurance/apply")

    db.expire_all()
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    assert call.compliance_profile_id == original_profile_id


def test_applying_does_not_touch_another_org(tenants, db):
    from models import ComplianceProfile
    b_before = db.query(ComplianceProfile).filter_by(org_id=tenants.b["org"], is_active=True).first()
    b_name = b_before.name

    tenants.a_admin.post("/profile/templates/solar/apply")

    db.expire_all()
    b_after = db.query(ComplianceProfile).filter_by(org_id=tenants.b["org"], is_active=True).all()
    assert len(b_after) == 1 and b_after[0].name == b_name


def test_unknown_template_key_is_404(tenants):
    assert tenants.a_admin.post("/profile/templates/not-a-template/apply").status_code == 404


def test_blank_leaves_an_editable_empty_checklist(tenants, db):
    from models import ComplianceProfile
    tenants.a_admin.post("/profile/templates/blank/apply")
    db.expire_all()
    active = db.query(ComplianceProfile).filter_by(org_id=tenants.a["org"], is_active=True).first()
    assert active.script_sections_json == {"sections": [], "auto_fail_phrases": []}
    body = tenants.a_admin.get("/profile/").get_data(as_text=True)
    assert "No sections yet" in body and "Browse templates" in body


def test_a_template_can_be_edited_after_applying(tenants, db):
    """Applying must not produce a read-only profile."""
    from models import ComplianceProfile
    tenants.a_admin.post("/profile/templates/solar/apply")
    r = tenants.a_admin.post("/profile/sections/add", data={"name": "Our own section"})
    assert r.status_code in (200, 302)
    db.expire_all()
    active = db.query(ComplianceProfile).filter_by(org_id=tenants.a["org"], is_active=True).first()
    assert any(s["name"] == "Our own section" for s in active.script_sections_json["sections"])
