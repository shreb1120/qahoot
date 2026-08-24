"""The verdict column, and the legacy rows it replaces.

`Report.verdict` is the indexed form of a determination. The risk in adding it
is not the new path — it is the old one: a report written before the column
existed carries NULL, and if the shared predicates in stats.py silently scored
those as "not a pass" the dashboard would under-report every long-standing
customer's history. So both paths are pinned here.
"""
import uuid

import pytest

from report_normalizer import normalize_report
from stats import agent_performance_rows


CHECKLIST = {"sections": [{
    "name": "Approval Script", "key": "approval_script",
    "items": [{"name": "Disclose fees", "required": True}],
}], "auto_fail_phrases": [{"phrase": "guaranteed settlement"}]}


def _model_response(status, phrases=None):
    return {
        "summary": "s",
        "sections": [{"key": "approval_script", "items": [
            {"name": "Disclose fees", "status": status, "evidence": ""}]}],
        "auto_fail_phrases": {"phrases": phrases or []},
    }


# ───────────────────── the normalizer derives both forms ─────────────────────

@pytest.mark.parametrize("status,phrases,determination,verdict", [
    ("covered",     None, "PASS", "pass"),
    ("not_covered", None, "FAIL — Approval Script", "fail"),
    ("not_assessed", None, "INCOMPLETE — grading gap", "incomplete"),
    ("covered", [{"phrase": "guaranteed settlement"}], "CRITICAL FAIL", "critical"),
])
def test_verdict_always_agrees_with_the_prose(status, phrases, determination, verdict):
    """Two representations of one decision. If they can disagree, one of the
    two pages reading them is lying."""
    out = normalize_report(_model_response(status, phrases), CHECKLIST)
    assert out["final_determination"] == determination
    assert out["verdict"] == verdict


def test_an_unconfigured_phrase_cannot_produce_a_critical_verdict():
    """The phrase filter already protects the determination; the verdict is
    derived from the same place, so it inherits that protection."""
    out = normalize_report(
        _model_response("covered", [{"phrase": "something invented"}]), CHECKLIST)
    assert out["verdict"] == "pass"


# ─────────────────────── legacy rows still score right ───────────────────────

def test_a_report_written_before_the_column_existed_still_counts(tenants, db):
    """Production holds reports with verdict NULL until the migration runs, and
    a report saved by an older process mid-deploy would too. Scoring those as
    non-passes would silently deflate every pass rate on the dashboard."""
    from models import Report
    report = (
        db.query(Report)
        .join(Report.call)
        .filter_by(org_id=tenants.b["org"])
        .one()
    )
    assert report.pass_fail_status == "PASS"

    report.verdict = None          # the pre-migration state
    db.commit()

    rows = agent_performance_rows(db, tenants.b["org"])
    assert rows[0]["passed"] == 1, "a legacy PASS row was not counted as a pass"
    assert rows[0]["pass_rate"] == 100


def test_an_org_cannot_hold_two_active_checklists(tenants, db):
    """Which checklist graded a call is the whole basis of the report. Two
    active profiles makes that ambiguous, and the app-level rule (deactivate,
    then insert) does not survive two concurrent switches."""
    import sqlalchemy.exc
    from models import ComplianceProfile

    db.add(ComplianceProfile(
        id=str(uuid.uuid4()), org_id=tenants.a["org"],
        name="second active", is_active=True, script_sections_json={},
    ))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db.commit()
    db.rollback()


def test_deactivated_checklists_are_unconstrained(tenants, db):
    """Switching templates keeps every previous checklist, so an org
    accumulates many inactive rows. Only `is_active` is unique."""
    from models import ComplianceProfile
    for i in range(3):
        db.add(ComplianceProfile(
            id=str(uuid.uuid4()), org_id=tenants.a["org"],
            name=f"old {i}", is_active=False, script_sections_json={},
        ))
    db.commit()   # no error
    assert db.query(ComplianceProfile).filter_by(
        org_id=tenants.a["org"], is_active=False).count() == 3


def test_a_legacy_critical_row_is_not_double_counted_as_a_pass(tenants, db):
    """'CRITICAL FAIL' contains neither the word PASS nor a verdict code — the
    ordering of the fallback branches is what keeps it out of the pass count."""
    from models import Report
    report = (
        db.query(Report).join(Report.call)
        .filter_by(org_id=tenants.a["org"]).one()
    )
    report.verdict = None
    db.commit()

    rows = agent_performance_rows(db, tenants.a["org"])
    assert rows[0]["passed"] == 0
    assert rows[0]["critical"] == 1
