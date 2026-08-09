"""The agent profile page and the shared stats it is built on.

`most_missed` decides what a manager says to an employee. The tests that matter
most here are not the rendering ones — they are the three that keep the number
honest: overrides win, `not_assessed` is not a miss, and a rate never appears
without its denominator.
"""
import uuid

import pytest

from stats import call_score, most_missed


# ─────────────────────────── most_missed, pure ───────────────────────────

class FakeReport:
    def __init__(self, report_json, overrides_json=None):
        self.report_json = report_json
        self.overrides_json = overrides_json


class FakeCall:
    def __init__(self, report):
        self.report = report


def _item(name, status, required=True):
    return {"name": name, "status": status, "required": required}


def _call(*items, overrides=None, section="Approval Script", key="approval_script"):
    return FakeCall(FakeReport(
        {"sections": [{"name": section, "key": key, "items": list(items)}]},
        overrides,
    ))


CHECKLIST = {"sections": [{
    "name": "Approval Script", "key": "approval_script",
    "items": [{"name": "Disclose fees"}, {"name": "State cancellation policy"}],
}]}


def test_a_missed_item_is_counted_with_its_denominator():
    calls = [_call(_item("Disclose fees", "not_covered")),
             _call(_item("Disclose fees", "covered"))]
    ranked = most_missed(calls, CHECKLIST)["ranked"]
    assert len(ranked) == 1
    assert (ranked[0]["missed"], ranked[0]["occurrences"], ranked[0]["rate"]) == (1, 2, 50)


def test_an_approved_override_clears_the_miss():
    """A manager who has already ruled on an item outranks the model. Counting
    it against the agent anyway would make the coaching list dishonest."""
    key = "approval_script::Disclose fees"
    calls = [_call(_item("Disclose fees", "not_covered"), overrides={key: "approved"}),
             _call(_item("Disclose fees", "not_covered"), overrides={key: "approved"})]
    result = most_missed(calls, CHECKLIST)
    assert result["ranked"] == []
    assert [e["name"] for e in result["clean"]] == ["Disclose fees"]


def test_a_failed_override_creates_a_miss_the_model_did_not_report():
    key = "approval_script::Disclose fees"
    calls = [_call(_item("Disclose fees", "covered"), overrides={key: "failed"}),
             _call(_item("Disclose fees", "covered"), overrides={key: "failed"})]
    ranked = most_missed(calls, CHECKLIST)["ranked"]
    assert ranked[0]["missed"] == 2 and ranked[0]["rate"] == 100


def test_not_assessed_is_reported_separately_and_never_as_a_miss():
    """The grader returned no verdict. That is a gap in the analysis, not
    something the agent failed to say."""
    calls = [_call(_item("Disclose fees", "not_assessed")),
             _call(_item("Disclose fees", "not_assessed"))]
    result = most_missed(calls, CHECKLIST)
    assert result["ranked"] == []
    entry = result["clean"][0]
    assert entry["not_assessed"] == 2 and entry["missed"] == 0


def test_optional_items_are_excluded():
    calls = [_call(_item("Offer a callback", "not_covered", required=False)),
             _call(_item("Offer a callback", "not_covered", required=False))]
    assert most_missed(calls, None)["ranked"] == []


def test_the_same_name_in_two_sections_stays_two_rows():
    """Grouping by name alone would silently merge two different obligations."""
    a = _call(_item("Verify identity", "not_covered"), section="Opening", key="opening")
    b = _call(_item("Verify identity", "not_covered"), section="Closing", key="closing")
    checklist = {"sections": [
        {"name": "Opening", "key": "opening", "items": [{"name": "Verify identity"}]},
        {"name": "Closing", "key": "closing", "items": [{"name": "Verify identity"}]},
    ]}
    result = most_missed([a, b, a, b], checklist)
    assert len(result["ranked"]) == 2
    assert {e["section"] for e in result["ranked"]} == {"Opening", "Closing"}


def test_an_item_absent_from_the_active_checklist_is_retired_not_ranked():
    calls = [_call(_item("Mention the old promo", "not_covered")),
             _call(_item("Mention the old promo", "not_covered"))]
    result = most_missed(calls, CHECKLIST)
    assert result["ranked"] == []
    assert [e["name"] for e in result["retired"]] == ["Mention the old promo"]


def test_a_single_occurrence_is_held_back_from_the_ranked_list():
    """One call producing "100% missed" would misdirect a coaching conversation."""
    result = most_missed([_call(_item("Disclose fees", "not_covered"))], CHECKLIST)
    assert result["ranked"] == []
    assert [e["name"] for e in result["seen_once"]] == ["Disclose fees"]


@pytest.mark.parametrize("report_json", [
    None, "not a dict", {}, {"sections": None}, {"sections": ["junk"]},
    {"sections": [{"items": [None, {"no_name": 1}]}]},
])
def test_malformed_report_json_contributes_nothing_and_does_not_raise(report_json):
    result = most_missed([FakeCall(FakeReport(report_json))], CHECKLIST)
    assert result["ranked"] == [] and result["retired"] == []


def test_a_call_with_no_report_is_skipped():
    assert most_missed([FakeCall(None)], CHECKLIST)["ranked"] == []


def test_call_score_matches_the_report_pages_covered_over_total():
    assert call_score({"sections": [{"covered_count": 1, "total_count": 2},
                                    {"covered_count": 2, "total_count": 2}]}) == 75
    assert call_score({"sections": [{"covered_count": 0, "total_count": 0}]}) is None
    assert call_score(None) is None


# ─────────────────────────────── the route ───────────────────────────────

def test_the_profile_renders_for_an_agent_in_your_org(tenants):
    r = tenants.a_admin.get(f"/agents/{tenants.a['agent']}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Acme Agent" in body
    assert "Most missed requirements" in body


def test_a_member_can_read_a_profile(tenants):
    """Reading performance is not an admin action; only mutations are."""
    assert tenants.a_member.get(f"/agents/{tenants.a['agent']}").status_code == 200


def test_another_orgs_profile_is_404_and_leaks_no_evidence(tenants):
    r = tenants.b_admin.get(f"/agents/{tenants.a['agent']}")
    assert r.status_code == 404
    body = r.get_data(as_text=True)
    assert "Acme" not in body
    assert "Acme evidence quote" not in body


def test_an_unknown_agent_id_is_404(tenants):
    assert tenants.a_admin.get(f"/agents/{uuid.uuid4()}").status_code == 404


def test_the_profile_the_dashboard_and_the_list_report_the_same_pass_rate(tenants, db):
    """Pins the shared-semantics requirement: three pages, one definition.

    The fixture agent gives a round 0%, which any broken page could produce by
    accident. A third graded call makes the rate 33% — a number that only
    appears if the same predicate ran."""
    from conftest import _mk_call
    from models import ComplianceProfile, Organization, User
    org = db.query(Organization).filter_by(id=tenants.a["org"]).one()
    admin = db.query(User).filter_by(id=tenants.a["admin"]).one()
    profile = db.query(ComplianceProfile).filter_by(org_id=org.id).one()
    from models import Agent
    agent = db.query(Agent).filter_by(id=tenants.a["agent"]).one()

    _mk_call(db, org, profile, admin, agent, verdict="PASS", internal_id="2001")
    _mk_call(db, org, profile, admin, agent, verdict="FAIL", internal_id="2002")

    # A second agent, so the org-wide pass rate (50%) differs from this agent's
    # (33%). Without that they coincide and the dashboard assertion would pass
    # off the headline metric even with the agent panel gone.
    other = Agent(id=str(uuid.uuid4()), org_id=org.id, name="Zeta Other")
    db.add(other)
    db.flush()
    _mk_call(db, org, profile, admin, other, verdict="PASS", internal_id="2003")
    db.commit()   # agent: 1 PASS of 3 graded → 33%;  org: 2 of 4 → 50%

    dash = tenants.a_admin.get("/").get_data(as_text=True)
    prof = tenants.a_admin.get(f"/agents/{agent.id}").get_data(as_text=True)
    lst = tenants.a_admin.get("/agents/").get_data(as_text=True)

    for label, body in [("dashboard", dash), ("profile", prof), ("agents list", lst)]:
        assert "33%" in body, f"{label} disagrees on the pass rate"
    assert "1 of 3 passed" in prof


def test_the_profile_counts_only_this_orgs_calls(tenants, db):
    """The same reach-through leak the dashboard had: Report carries no org_id,
    so scoping the agent alone is not enough.

    The stray call needs a *Report*, or the inner join hides the leak and this
    test passes whether or not the scoping exists."""
    from models import Call, Report
    stray = Call(
        id=str(uuid.uuid4()), org_id=tenants.b["org"],
        compliance_profile_id=None, uploaded_by_user_id=tenants.b["admin"],
        agent_id=tenants.a["agent"],          # A's agent, B's call
        filename="stray.mp3", status="complete",
    )
    db.add(stray)
    db.flush()
    db.add(Report(id=str(uuid.uuid4()), call_id=stray.id, pass_fail_status="PASS",
                  report_json={"sections": []}))
    db.commit()

    from stats import agent_performance_rows
    rows = agent_performance_rows(db, tenants.a["org"], agent_id=tenants.a["agent"])
    assert rows[0]["graded"] == 1, "another org's graded call reached A's agent"
    assert rows[0]["pass_rate"] == 0, "another org's PASS inflated A's pass rate"

    body = tenants.a_admin.get(f"/agents/{tenants.a['agent']}").get_data(as_text=True)
    assert "0 of 1 passed" in body


def test_an_agent_with_no_calls_gets_an_empty_state_not_a_crash(tenants, db):
    from models import Agent
    a = Agent(id=str(uuid.uuid4()), org_id=tenants.a["org"], name="Brand New")
    db.add(a)
    db.commit()
    r = tenants.a_admin.get(f"/agents/{a.id}")
    assert r.status_code == 200
    assert "No calls attributed to Brand yet" in r.get_data(as_text=True)


# ────────────────────────── the list: search ──────────────────────────

def test_search_filters_the_agent_list(tenants, db):
    from models import Agent
    db.add(Agent(id=str(uuid.uuid4()), org_id=tenants.a["org"], name="Dana Whitfield"))
    db.commit()

    body = tenants.a_admin.get("/agents/?q=dana").get_data(as_text=True)
    assert "Dana Whitfield" in body
    assert "Acme Agent" not in body


def test_search_never_reaches_another_org(tenants):
    body = tenants.b_admin.get("/agents/?q=Acme").get_data(as_text=True)
    assert "Acme Agent" not in body


def test_a_search_with_no_match_explains_itself(tenants):
    body = tenants.a_admin.get("/agents/?q=zzzz").get_data(as_text=True)
    assert "No agent matches" in body


def test_like_wildcards_are_searched_literally(tenants, db):
    """A customer typing "%" wants agents whose name contains "%", not all of
    them — an unescaped LIKE would match everything and look broken."""
    from models import Agent
    db.add(Agent(id=str(uuid.uuid4()), org_id=tenants.a["org"], name="Percent % Person"))
    db.commit()

    body = tenants.a_admin.get("/agents/?q=%25").get_data(as_text=True)
    assert "Percent % Person" in body
    assert "Acme Agent" not in body, "the % was treated as a wildcard"


def test_the_list_links_each_agent_to_their_profile(tenants):
    body = tenants.a_admin.get("/agents/").get_data(as_text=True)
    assert f"/agents/{tenants.a['agent']}" in body
