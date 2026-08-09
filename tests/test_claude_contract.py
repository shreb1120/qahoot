"""Does a hand-built checklist actually work against the Claude API?

Everything else about the checklist is provable offline. This is not: only a
real call shows whether the model returns the section keys we asked for, in the
shape the report page reads.

    pytest --live -m live

Off by default — it costs money and needs network. Keep it small: one short
transcript, one deliberately non-default checklist.
"""
import json
import os
import re
import pytest

from prompt_builder import build_system_prompt, build_user_message

pytestmark = pytest.mark.live

# Deliberately NOT one of the shipped templates — the point is to prove an
# arbitrary customer-authored checklist works.
CUSTOM_CHECKLIST = {
    "sections": [
        {"name": "Greeting", "key": "greeting", "items": [
            {"name": "Agent states their own first name", "required": True, "notes": ""},
            {"name": "Agent states the company name", "required": True, "notes": ""},
        ]},
        {"name": "Disclosures", "key": "disclosures", "items": [
            {"name": "Agent says the call is recorded", "required": True, "notes": ""},
            {"name": "Agent states the monthly price in dollars", "required": True, "notes": ""},
        ]},
    ],
    "auto_fail_phrases": [
        {"phrase": "guaranteed approval", "description": "Cannot promise approval."},
    ],
}

TRANSCRIPT = """[0:01] A: Hi there, my name is Dana calling from Northwind Services.
[0:06] B: Oh, hello.
[0:08] A: Just so you know, this call is being recorded for quality assurance.
[0:13] B: That's fine.
[0:15] A: The plan runs forty nine dollars a month, and you get guaranteed approval today.
[0:22] B: Sounds good to me.
"""


@pytest.fixture(scope="module")
def response_json():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=os.environ.get("QABOOM_TEST_MODEL", "claude-sonnet-4-5"),
        max_tokens=2000,
        system=build_system_prompt(CUSTOM_CHECKLIST),
        messages=[{"role": "user", "content": build_user_message(TRANSCRIPT)}],
    )
    text = msg.content[0].text.strip()
    # The prompt forbids code fences; strip them anyway so a formatting slip
    # fails on content rather than on parsing.
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    return json.loads(text)


def test_the_model_returns_parseable_json(response_json):
    assert isinstance(response_json, dict)


def test_it_returns_exactly_our_section_keys(response_json):
    """If keys drift, the report page renders sections the overrides cannot address."""
    keys = [s.get("key") for s in response_json.get("sections", [])]
    assert keys == ["greeting", "disclosures"], keys


def test_every_requirement_comes_back(response_json):
    wanted = {i["name"] for s in CUSTOM_CHECKLIST["sections"] for i in s["items"]}
    got = {i.get("name") for s in response_json.get("sections", []) for i in s.get("items", [])}
    assert wanted <= got, f"missing from the response: {wanted - got}"


def test_item_status_uses_the_documented_vocabulary(response_json):
    for s in response_json.get("sections", []):
        for i in s.get("items", []):
            assert i.get("status") in ("covered", "not_covered"), i


def test_the_planted_auto_fail_phrase_is_detected(response_json):
    """The transcript says 'guaranteed approval', which the checklist bans."""
    af = response_json.get("auto_fail_phrases", {})
    assert af.get("detected") is True
    assert af.get("phrases"), "detected=true but no phrase was reported"


def test_the_determination_is_critical_fail(response_json):
    assert "CRITICAL" in response_json.get("final_determination", "").upper()


def test_the_response_renders_through_the_real_report_view(response_json, app, tenants, db):
    """The end of the chain: a live response must drive the actual report page."""
    from models import Call
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    call.report.report_json = response_json
    call.report.pass_fail_status = response_json.get("final_determination", "")
    db.commit()
    r = tenants.a_admin.get(f"/calls/{tenants.a['call']}/report")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Greeting" in body and "Disclosures" in body
