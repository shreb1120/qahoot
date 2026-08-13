"""Client roll-up: every call with one person, in one view.

Two calls for the same client produced two unrelated reports, which is correct
for grading and useless for the question a reviewer actually has — was this
person told what they were owed, at any point, and by whom.

Nothing here re-grades. Each call keeps its own report and verdict; this reads
what is already there and adds the axis a single report cannot have.
"""
import uuid
from datetime import date, timedelta

import pytest

import clients
from models import Agent, Call, ComplianceProfile, Organization, Report, User


def _call(db, tenants, *, phone, day, statuses, name=None, which="a"):
    """A graded call whose checklist items carry the given statuses."""
    org = db.get(Organization, getattr(tenants, which)["org"])
    profile = db.query(ComplianceProfile).filter_by(org_id=org.id).one()
    c = Call(id=str(uuid.uuid4()), org_id=org.id, compliance_profile_id=profile.id,
             uploaded_by_user_id=getattr(tenants, which)["admin"], agent_id=getattr(tenants, which).get("agent"),
             filename=f"{day}.mp3", status="complete", duration=600,
             call_date=day, client_phone=phone, client_name=name)
    db.add(c); db.flush()
    db.add(Report(id=str(uuid.uuid4()), call_id=c.id, verdict="fail",
                  pass_fail_status="FAIL", report_json={
        "sections": [{"name": "Approval Script", "key": "approval_script", "items": [
            {"name": n, "status": s, "required": True, "timestamp": "1:00"}
            for n, s in statuses.items()]}],
        "auto_fail_phrases": {"detected": False, "phrases": []},
    }))
    db.commit()
    return c


def test_two_calls_for_one_client_become_one_view(tenants, db):
    _call(db, tenants, phone="5550001111", day=date(2026, 8, 11),
          statuses={"Disclose fees": "covered", "Cancellation policy": "missed"})
    _call(db, tenants, phone="5550001111", day=date(2026, 8, 12),
          statuses={"Disclose fees": "covered", "Cancellation policy": "covered"})

    roll = clients.roll_up(db, tenants.a["org"], "5550001111")
    assert len(roll["calls"]) == 2
    assert len(roll["rows"]) == 2


def test_a_requirement_missed_then_covered_reads_as_fixed(tenants, db):
    """The single most useful thing here, and the one neither call's own report
    can show: a problem that got put right."""
    _call(db, tenants, phone="5550002222", day=date(2026, 8, 11),
          statuses={"Cancellation policy": "missed"})
    _call(db, tenants, phone="5550002222", day=date(2026, 8, 12),
          statuses={"Cancellation policy": "covered"})

    roll = clients.roll_up(db, tenants.a["org"], "5550002222")
    row = roll["rows"][0]
    assert row["fixed"] is True
    assert row["outstanding"] is False
    assert [c["status"] for c in row["cells"]] == ["missed", "covered"]


def test_covered_then_missed_is_outstanding_not_fixed(tenants, db):
    """A regression is not a fix. Order matters, and reading it backwards would
    quietly report a live problem as resolved."""
    _call(db, tenants, phone="5550003333", day=date(2026, 8, 11),
          statuses={"Disclose fees": "covered"})
    _call(db, tenants, phone="5550003333", day=date(2026, 8, 12),
          statuses={"Disclose fees": "missed"})

    row = clients.roll_up(db, tenants.a["org"], "5550003333")["rows"][0]
    assert row["fixed"] is False and row["outstanding"] is True


def test_calls_are_ordered_oldest_first(tenants, db):
    """The whole page is a trajectory. Uploaded out of order, it must still read
    in the order the calls happened."""
    _call(db, tenants, phone="5550004444", day=date(2026, 8, 12),
          statuses={"Disclose fees": "covered"})
    _call(db, tenants, phone="5550004444", day=date(2026, 8, 10),
          statuses={"Disclose fees": "missed"})

    roll = clients.roll_up(db, tenants.a["org"], "5550004444")
    assert [c.call_date for c in roll["calls"]] == [date(2026, 8, 10), date(2026, 8, 12)]
    assert [c["status"] for c in roll["rows"][0]["cells"]] == ["missed", "covered"]


def test_a_manager_override_is_honoured(tenants, db):
    """Overrides are what the agent's scorecard counts. A roll-up ignoring them
    would contradict the agent's own numbers."""
    c = _call(db, tenants, phone="5550005555", day=date(2026, 8, 11),
              statuses={"Disclose fees": "missed"})
    c.report.overrides_json = {"approval_script::Disclose fees": "approved"}
    db.commit()

    row = clients.roll_up(db, tenants.a["org"], "5550005555")["rows"][0]
    assert row["cells"][0]["status"] == "covered"


def test_formatting_does_not_split_a_client_in_two(tenants, db):
    _call(db, tenants, phone="(555) 000-6666", day=date(2026, 8, 11),
          statuses={"Disclose fees": "covered"})
    _call(db, tenants, phone="5550006666", day=date(2026, 8, 12),
          statuses={"Disclose fees": "covered"})

    assert len(clients.roll_up(db, tenants.a["org"], "5550006666")["calls"]) == 2


def test_the_name_is_shown_and_the_number_still_groups(tenants, db):
    """Names get spelled differently across calls; the number stays the key."""
    _call(db, tenants, phone="5550007777", day=date(2026, 8, 11),
          statuses={"Disclose fees": "covered"}, name="Ray Ortiz")
    _call(db, tenants, phone="5550007777", day=date(2026, 8, 12),
          statuses={"Disclose fees": "covered"}, name="Raymond Ortiz")

    roll = clients.roll_up(db, tenants.a["org"], "5550007777")
    assert len(roll["calls"]) == 2
    assert roll["name"] == "Raymond Ortiz", "the most recent spelling should win"


def test_one_org_cannot_read_anothers_client(tenants, db):
    _call(db, tenants, phone="5550008888", day=date(2026, 8, 11),
          statuses={"Disclose fees": "covered"}, which="b")
    assert clients.roll_up(db, tenants.a["org"], "5550008888") is None


def test_the_client_page_is_gated(anon, tenants, db):
    _call(db, tenants, phone="5550009999", day=date(2026, 8, 11),
          statuses={"Disclose fees": "covered"})
    assert anon.get("/clients/5550009999").status_code in (302, 401, 403)
    assert tenants.a_admin.get("/clients/5550009999").status_code == 200


def test_history_shows_when_a_client_has_other_calls(tenants, db):
    """The gap that made the roll-up feel like it did not exist.

    History listed two calls for one client as two unrelated rows, with nothing
    to say they were related or that a combined view existed. The client page
    was unreachable from the screen people actually start on, so from the
    outside it looked as though nothing had been built.
    """
    from datetime import date
    _call(db, tenants, phone="5551230000", day=date(2026, 8, 11),
          statuses={"Disclose fees": "covered"}, name="Ray Ortiz")
    _call(db, tenants, phone="5551230000", day=date(2026, 8, 12),
          statuses={"Disclose fees": "missed"}, name="Ray Ortiz")

    body = tenants.a_admin.get("/calls/").get_data(as_text=True)
    assert "/clients/5551230000" in body, "history does not link to the client roll-up"
    assert "2 calls with this client" in body
    assert "Ray Ortiz" in body, "the client's name should be the label, not the number"


def test_a_lone_call_is_not_labelled_as_a_group(tenants, db):
    """"1 calls with this client" on every row would be noise that people learn
    to stop reading."""
    from datetime import date
    _call(db, tenants, phone="5554560000", day=date(2026, 8, 11),
          statuses={"Disclose fees": "covered"})
    body = tenants.a_admin.get("/calls/").get_data(as_text=True)
    assert "/clients/5554560000" in body
    assert "1 calls with this client" not in body
