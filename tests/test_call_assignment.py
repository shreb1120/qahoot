"""Attributing a call after upload, now that metadata is optional."""
import pytest


def test_an_unassigned_call_can_be_assigned(tenants, db):
    from models import Call
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    call.agent_id = None
    db.commit()

    r = tenants.a_admin.post(f"/calls/{tenants.a['call']}/agent",
                             data={"agent_id": tenants.a["agent"]})
    assert r.status_code == 302
    db.expire_all()
    assert db.query(Call).filter_by(id=tenants.a["call"]).first().agent_id == tenants.a["agent"]


def test_posting_an_empty_value_unassigns(tenants, db):
    from models import Call
    r = tenants.a_admin.post(f"/calls/{tenants.a['call']}/agent", data={"agent_id": ""})
    assert r.status_code == 302
    db.expire_all()
    assert db.query(Call).filter_by(id=tenants.a["call"]).first().agent_id is None


def test_an_agent_from_another_org_is_refused_and_the_call_is_unchanged(tenants, db):
    from models import Call
    before = db.query(Call).filter_by(id=tenants.a["call"]).first().agent_id
    r = tenants.a_admin.post(f"/calls/{tenants.a['call']}/agent",
                             data={"agent_id": tenants.b["agent"]})
    assert r.status_code == 302          # redirects with a flash, does not assign
    db.expire_all()
    assert db.query(Call).filter_by(id=tenants.a["call"]).first().agent_id == before


def test_a_member_can_assign(tenants, db):
    """Reviewers already write overrides; attribution is the same tier of work."""
    r = tenants.a_member.post(f"/calls/{tenants.a['call']}/agent",
                              data={"agent_id": tenants.a["agent"]})
    assert r.status_code == 302


def test_the_report_page_offers_assignment_when_unassigned(tenants, db):
    from models import Call
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    call.agent_id = None
    db.commit()
    body = tenants.a_admin.get(f"/calls/{tenants.a['call']}/report").get_data(as_text=True)
    assert "Assign an agent" in body
    assert "Unassigned" in body


def test_unassigned_calls_are_findable_in_history(tenants, db):
    from models import Call
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    call.agent_id = None
    db.commit()
    body = tenants.a_admin.get("/calls/?agent_id=unassigned").get_data(as_text=True)
    assert "acme-call.mp3" in body


def test_an_undated_call_still_appears_under_a_date_filter(tenants, db):
    """Regression: `Call.call_date >= x` drops NULL rows under SQL three-valued
    logic, so undated calls silently vanished once dates became optional."""
    from models import Call
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    call.call_date = None
    db.commit()
    body = tenants.a_admin.get("/calls/?date_from=2020-01-01&date_to=2100-01-01").get_data(as_text=True)
    assert "acme-call.mp3" in body, "an undated call disappeared from a date-filtered view"
