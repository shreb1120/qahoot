"""The checklist is the source of truth; the model response is evidence.

A customer edits their checklist and uploads a call. They should never have to
wonder whether grading "worked". These tests pin that guarantee: whatever the
model returns, the stored report describes exactly the checklist that was in
force, with counts and a verdict derived from it.
"""
import pytest
from report_normalizer import normalize_report, COVERED, NOT_COVERED, NOT_ASSESSED

CHECKLIST = {
    "sections": [
        {"name": "Opening", "key": "opening", "items": [
            {"name": "States their name", "required": True},
            {"name": "States the company", "required": True},
            {"name": "Mentions the weather", "required": False},
        ]},
        {"name": "Disclosures", "key": "disclosures", "items": [
            {"name": "Says the call is recorded", "required": True},
        ]},
    ],
    "auto_fail_phrases": [{"phrase": "guaranteed approval", "description": "no"}],
}


def _sec(out, key):
    return next(s for s in out["sections"] if s["key"] == key)


def _item(out, key, name):
    return next(i for i in _sec(out, key)["items"] if i["name"] == name)


def test_every_checklist_item_appears_even_when_the_model_returns_nothing():
    out = normalize_report({}, CHECKLIST)
    assert [s["key"] for s in out["sections"]] == ["opening", "disclosures"]
    assert len(_sec(out, "opening")["items"]) == 3
    assert all(i["status"] == NOT_ASSESSED for s in out["sections"] for i in s["items"])


def test_a_dropped_requirement_becomes_not_assessed_rather_than_vanishing():
    """The dangerous case: a silently missing requirement looks like a clean call."""
    out = normalize_report({"sections": [{"key": "opening", "items": [
        {"name": "States their name", "status": "covered"}]}]}, CHECKLIST)
    assert _item(out, "opening", "States the company")["status"] == NOT_ASSESSED
    assert _item(out, "disclosures", "Says the call is recorded")["status"] == NOT_ASSESSED
    assert out["verdict"] == "incomplete"
    assert out["final_determination"].startswith("INCOMPLETE")


def test_not_assessed_alone_never_fails_the_call():
    """Model dropout must not look like the agent skipped a disclosure."""
    out = normalize_report({"sections": [
        {"key": "opening", "items": [
            {"name": "States their name", "status": "covered"},
            {"name": "States the company", "status": "not_assessed"}]},
        {"key": "disclosures", "items": [
            {"name": "Says the call is recorded", "status": "covered"}]}]}, CHECKLIST)
    assert out["verdict"] == "incomplete"
    assert "FAIL" not in out["final_determination"]


def test_a_real_miss_still_fails_even_if_other_items_were_not_assessed():
    out = normalize_report({"sections": [
        {"key": "opening", "items": [
            {"name": "States their name", "status": "covered"},
            {"name": "States the company", "status": "not_assessed"}]},
        {"key": "disclosures", "items": [
            {"name": "Says the call is recorded", "status": "not_covered"}]}]}, CHECKLIST)
    assert out["verdict"] == "fail"
    assert out["final_determination"] == "FAIL — Disclosures"


def test_counts_are_recomputed_not_taken_from_the_model():
    out = normalize_report({"sections": [
        {"key": "opening", "covered_count": 99, "total_count": 99, "items": [
            {"name": "States their name", "status": "covered"},
            {"name": "States the company", "status": "not_covered"},
        ]}]}, CHECKLIST)
    s = _sec(out, "opening")
    assert s["covered_count"] == 1
    assert s["total_count"] == 2, "optional items must not inflate the denominator"


def test_the_verdict_cannot_disagree_with_the_rows():
    """The model claiming PASS while a requirement is missing is the failure a
    compliance report must never show."""
    out = normalize_report({
        "final_determination": "PASS",
        "sections": [{"key": "opening", "items": [
            {"name": "States their name", "status": "covered"},
            {"name": "States the company", "status": "not_covered"}]}],
    }, CHECKLIST)
    assert out["final_determination"].startswith("FAIL")


def test_a_genuinely_clean_call_passes():
    out = normalize_report({"sections": [
        {"key": "opening", "items": [
            {"name": "States their name", "status": "covered"},
            {"name": "States the company", "status": "covered"},
            {"name": "Mentions the weather", "status": "not_covered"}]},
        {"key": "disclosures", "items": [
            {"name": "Says the call is recorded", "status": "covered"}]}],
        "auto_fail_phrases": {"detected": False, "phrases": []}}, CHECKLIST)
    assert out["final_determination"] == "PASS", "an unmet OPTIONAL item must not fail a call"


def test_one_failing_section_names_that_section():
    out = normalize_report({"sections": [
        {"key": "opening", "items": [
            {"name": "States their name", "status": "covered"},
            {"name": "States the company", "status": "covered"}]},
        {"key": "disclosures", "items": [
            {"name": "Says the call is recorded", "status": "not_covered"}]}]}, CHECKLIST)
    assert out["final_determination"] == "FAIL — Disclosures"


def test_a_section_the_model_invented_is_dropped():
    out = normalize_report({"sections": [
        {"key": "totally_invented", "name": "Invented", "items": [
            {"name": "Fabricated", "status": "covered"}]}]}, CHECKLIST)
    assert [s["key"] for s in out["sections"]] == ["opening", "disclosures"]


def test_an_unconfigured_auto_fail_phrase_cannot_fail_a_call():
    """A hallucinated violation must never produce a CRITICAL FAIL."""
    out = normalize_report({"auto_fail_phrases": {"detected": True, "phrases": [
        {"phrase": "something nobody configured", "quote": "..."}]}}, CHECKLIST)
    assert out["auto_fail_phrases"]["detected"] is False
    assert "CRITICAL" not in out["final_determination"]


def test_a_configured_auto_fail_phrase_still_wins():
    out = normalize_report({"auto_fail_phrases": {"detected": True, "phrases": [
        {"phrase": "guaranteed approval", "quote": "you have guaranteed approval"}]},
        "sections": [{"key": "opening", "items": [
            {"name": "States their name", "status": "covered"},
            {"name": "States the company", "status": "covered"}]},
        {"key": "disclosures", "items": [
            {"name": "Says the call is recorded", "status": "covered"}]}]}, CHECKLIST)
    assert out["final_determination"] == "CRITICAL FAIL"


@pytest.mark.parametrize("raw_status,expected", [
    ("covered", COVERED), ("Covered", COVERED), ("COVERED", COVERED),
    ("not_covered", NOT_COVERED), ("not covered", NOT_COVERED), ("missing", NOT_COVERED),
    ("", NOT_ASSESSED), (None, NOT_ASSESSED), ("banana", NOT_ASSESSED),
])
def test_status_vocabulary_is_normalised(raw_status, expected):
    out = normalize_report({"sections": [{"key": "opening", "items": [
        {"name": "States their name", "status": raw_status}]}]}, CHECKLIST)
    assert _item(out, "opening", "States their name")["status"] == expected


def test_items_match_by_name_despite_punctuation_drift():
    """Models re-type names. Matching must not be brittle."""
    out = normalize_report({"sections": [{"key": "opening", "items": [
        {"name": "states their NAME.", "status": "covered"}]}]}, CHECKLIST)
    assert _item(out, "opening", "States their name")["status"] == COVERED


def test_sections_match_by_name_when_the_key_is_missing():
    out = normalize_report({"sections": [{"name": "Opening", "items": [
        {"name": "States their name", "status": "covered"}]}]}, CHECKLIST)
    assert _item(out, "opening", "States their name")["status"] == COVERED


@pytest.mark.parametrize("garbage", [None, {}, [], "a string", {"sections": "not a list"},
                                     {"sections": [None, 5]}, {"auto_fail_phrases": []}])
def test_garbage_in_never_raises(garbage):
    out = normalize_report(garbage, CHECKLIST)
    assert len(out["sections"]) == 2


def test_an_empty_checklist_does_not_explode():
    out = normalize_report({"sections": [{"key": "x", "items": []}]},
                           {"sections": [], "auto_fail_phrases": []})
    assert out["sections"] == []


def test_reconciliation_is_reported_so_the_reviewer_is_told():
    out = normalize_report({"sections": [{"key": "opening", "items": [
        {"name": "States their name", "status": "covered"}]}]}, CHECKLIST)
    assert out["_reconciliation"]["items_not_assessed"] == 3


def test_the_report_page_shows_the_not_assessed_state(tenants, db):
    from models import Call
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    call.report.report_json = normalize_report({"sections": [{"key": "opening", "items": [
        {"name": "States their name", "status": "covered"}]}]}, CHECKLIST)
    call.report.pass_fail_status = call.report.report_json["final_determination"]
    db.commit()
    body = tenants.a_admin.get(f"/calls/{tenants.a['call']}/report").get_data(as_text=True)
    assert "Not assessed" in body
    assert "came back without a verdict" in body, "the reviewer must be told, not left to spot it"
