"""Does an edited or hand-built checklist produce a usable Claude prompt?

These run with no API key and no network. They prove the deterministic half of
the contract: whatever the customer types must survive into the prompt intact,
and the output schema must ask for exactly their sections. The half that needs
a real model lives in test_claude_contract.py.
"""
import json
import re
import pytest
from prompt_builder import build_system_prompt, build_user_message

CUSTOM = {
    "sections": [
        {"name": "Opening", "key": "opening", "items": [
            {"name": "Agent states their own name", "required": True, "notes": "First 30 seconds."},
            {"name": "Mentions the weather", "required": False, "notes": ""},
        ]},
        {"name": "Solar specifics", "key": "solar_specifics", "items": [
            {"name": 'Says the words "loan" or "lease" explicitly', "required": True, "notes": ""},
            {"name": "Explains the 25-year warranty — including exclusions", "required": True,
             "notes": "Exclusions matter; a bare mention is not coverage."},
        ]},
    ],
    "auto_fail_phrases": [
        {"phrase": "free solar", "description": "Nothing is free."},
        {"phrase": "government program", "description": ""},
    ],
}


def test_every_requirement_reaches_the_prompt():
    p = build_system_prompt(CUSTOM)
    for section in CUSTOM["sections"]:
        assert section["name"].upper() in p
        for item in section["items"]:
            assert item["name"] in p, f"lost from the prompt: {item['name']}"


def test_notes_reach_the_prompt():
    p = build_system_prompt(CUSTOM)
    assert "Exclusions matter" in p and "First 30 seconds" in p


def test_optional_items_are_marked_optional():
    p = build_system_prompt(CUSTOM)
    line = next(l for l in p.splitlines() if "Mentions the weather" in l)
    assert "OPTIONAL" in line
    other = next(l for l in p.splitlines() if "Agent states their own name" in l)
    assert "OPTIONAL" not in other


def test_auto_fail_phrases_reach_the_prompt():
    p = build_system_prompt(CUSTOM)
    assert '"free solar"' in p and '"government program"' in p
    assert "Nothing is free." in p


def test_output_schema_asks_for_exactly_the_customers_sections():
    """If the schema keys drift from the checklist, the report renders empty."""
    p = build_system_prompt(CUSTOM)
    schema = p.split("OUTPUT FORMAT")[1]
    keys = re.findall(r'"key":\s*"([^"]+)"', schema)
    assert keys == ["opening", "solar_specifics"]


def test_section_keys_in_the_schema_are_unique():
    """Duplicate keys make the model's output ambiguous and collide manager
    overrides, which are stored as key::item_name."""
    p = build_system_prompt(CUSTOM)
    keys = re.findall(r'"key":\s*"([^"]+)"', p.split("OUTPUT FORMAT")[1])
    assert len(keys) == len(set(keys))


def test_a_key_is_derived_when_the_checklist_has_none():
    """Older hand-edited profiles may have sections with no key at all."""
    p = build_system_prompt({"sections": [{"name": "No Key Here", "items": []}],
                             "auto_fail_phrases": []})
    assert '"key": "no_key_here"' in p


def test_blank_checklist_still_produces_a_coherent_prompt():
    p = build_system_prompt({"sections": [], "auto_fail_phrases": []})
    assert "OUTPUT FORMAT" in p and "final_determination" in p
    assert "AUTO-FAIL PHRASES" not in p, "an empty phrase list should not print a header"


@pytest.mark.parametrize("name", [
    "Discloses “smart quotes” and — em dashes",
    "Ünïcödé requirement with áccents",
    "Item with a \"double quote\" inside",
    "Item with a {curly brace} and a $dollar",
    "A" * 300,
])
def test_awkward_requirement_text_survives_intact(name):
    """Customers type whatever they type. Nothing may be silently mangled."""
    p = build_system_prompt({"sections": [{"name": "S", "key": "s", "items": [
        {"name": name, "required": True, "notes": ""}]}], "auto_fail_phrases": []})
    assert name in p


def test_the_prompt_still_demands_json_only():
    """The pipeline parses the response as JSON; a drifted instruction breaks
    every call at once."""
    p = build_system_prompt(CUSTOM)
    assert "return ONLY valid JSON" in p
    assert "no markdown" in p and "no code fences" in p


def test_user_message_carries_the_transcript_verbatim():
    t = "[0:03] A: hello\n[0:07] B: hi"
    m = build_user_message(t)
    assert t in m


def test_a_report_matching_the_custom_checklist_renders(app, tenants, db):
    """The round trip: a report shaped like this checklist's keys must render,
    and its override keys must line up with what the report page writes."""
    from models import Call, Report
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    call.report.report_json = {
        "final_determination": "FAIL — Solar specifics",
        "summary": "Missed the loan/lease disclosure.",
        "sections": [
            {"name": "Opening", "key": "opening", "covered_count": 1, "total_count": 1,
             "items": [{"name": "Agent states their own name", "status": "covered",
                        "timestamp": "0:05", "evidence": "My name is Dana."}]},
            {"name": "Solar specifics", "key": "solar_specifics", "covered_count": 0, "total_count": 2,
             "items": [{"name": 'Says the words "loan" or "lease" explicitly',
                        "status": "not_covered", "timestamp": "—", "evidence": ""}]},
        ],
        "auto_fail_phrases": {"detected": False, "phrases": []},
    }
    db.commit()

    r = tenants.a_admin.get(f"/calls/{tenants.a['call']}/report")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Solar specifics" in body and "Agent states their own name" in body

    # An override written against a custom section key must persist under that key.
    ov = tenants.a_admin.post(
        f"/calls/{tenants.a['call']}/override",
        json={"section_key": "solar_specifics",
              "item_name": 'Says the words "loan" or "lease" explicitly',
              "status": "approved"})
    assert ov.status_code == 200
    db.expire_all()
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    assert call.report.overrides_json == {
        'solar_specifics::Says the words "loan" or "lease" explicitly': "approved"}


def test_new_sections_never_collide_on_key(tenants, db):
    """Two sections whose names differ only by case or punctuation used to share
    a key, which merged their manager overrides."""
    from models import ComplianceProfile
    for name in ["Approval Script", "approval script", "Approval-Script!"]:
        tenants.a_admin.post("/profile/sections/add", data={"name": name})
    db.expire_all()
    p = db.query(ComplianceProfile).filter_by(org_id=tenants.a["org"], is_active=True).first()
    keys = [s["key"] for s in p.script_sections_json["sections"]]
    assert len(keys) == len(set(keys)), f"colliding section keys: {keys}"
