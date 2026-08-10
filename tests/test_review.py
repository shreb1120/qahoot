"""Manager sign-off, and the queue it drives.

The bug this replaces is worth stating, because the fix is only meaningful
against it: a call left the review queue as soon as *any* per-item override
existed. So the ordinary case — a manager reads a call, agrees with the
grader, has nothing to override — could never be cleared, and the queue only
ever grew. Meanwhile correcting one item out of six silently cleared a call
somebody was halfway through.

The first test here is that exact scenario.
"""
import uuid

import pytest

import review
from models import Call, Report


def _report(db, tenants, which="a"):
    return (
        db.query(Report).join(Report.call)
        .filter_by(org_id=tenants.raw[which]["org"]).one()
    )


def _user(db, tenants, which="a", role="admin"):
    from models import User
    return db.get(User, tenants.raw[which][role])


# ───────────────────── the bug this exists to fix ─────────────────────

def test_a_call_can_be_cleared_without_touching_a_single_item(tenants, db):
    """The case the old logic made impossible. A manager reads the call, agrees
    with every requirement, and is done — no override to make."""
    call = db.get(Call, tenants.a["call"])
    assert call.report.overrides_json in (None, {}), "fixture should have no overrides"

    review.sign_off(db, call, _user(db, tenants), review.CONFIRMED)
    db.commit()

    assert review.pending_counts(db, tenants.a["org"])["total"] == 0


def test_correcting_one_item_does_not_clear_the_call(tenants, db):
    """The other half of the old bug: an override is a correction, not a
    decision, and must leave the call in the queue."""
    call = db.get(Call, tenants.a["call"])
    call.report.overrides_json = {"approval_script::Disclose fees": "approved"}
    db.commit()

    assert review.pending_counts(db, tenants.a["org"])["total"] == 1


# ─────────────────────────── sign-off ───────────────────────────

@pytest.mark.parametrize("outcome", ["confirmed", "dismissed"])
def test_sign_off_records_who_and_when(tenants, db, outcome):
    call = db.get(Call, tenants.a["call"])
    user = _user(db, tenants)
    review.sign_off(db, call, user, outcome, note="  looked at it  ")
    db.commit()
    db.expire_all()

    r = db.get(Call, tenants.a["call"]).report
    assert r.review_outcome == outcome
    assert r.reviewed_by_user_id == user.id
    assert r.reviewed_at is not None
    assert r.review_note == "looked at it", "note should be trimmed"


def test_an_unknown_outcome_is_refused(tenants, db):
    """The queue's meaning depends on the vocabulary being closed."""
    call = db.get(Call, tenants.a["call"])
    with pytest.raises(ValueError):
        review.sign_off(db, call, _user(db, tenants), "probably_fine")


def test_an_ungraded_call_cannot_be_signed_off(tenants, db):
    """There is nothing to have an opinion about yet."""
    call = Call(id=str(uuid.uuid4()), org_id=tenants.a["org"],
                uploaded_by_user_id=tenants.a["admin"],
                filename="x.mp3", status="transcribing")
    db.add(call); db.commit()
    with pytest.raises(ValueError):
        review.sign_off(db, call, _user(db, tenants), review.CONFIRMED)


def test_reopening_returns_it_to_the_queue_but_keeps_the_note(tenants, db):
    """Whoever reopens usually wants to know what the last person thought."""
    call = db.get(Call, tenants.a["call"])
    review.sign_off(db, call, _user(db, tenants), review.DISMISSED,
                    note="client confirmed verbally")
    db.commit()

    review.reopen(db, call)
    db.commit(); db.expire_all()

    r = db.get(Call, tenants.a["call"]).report
    assert r.reviewed_at is None and r.review_outcome is None
    assert r.review_note == "client confirmed verbally"
    assert review.pending_counts(db, tenants.a["org"])["total"] == 1


def test_re_signing_overwrites_rather_than_erroring(tenants, db):
    """Someone correcting their own mistake should not need an undo first."""
    call = db.get(Call, tenants.a["call"])
    review.sign_off(db, call, _user(db, tenants), review.DISMISSED)
    review.sign_off(db, call, _user(db, tenants), review.CONFIRMED)
    db.commit()
    assert db.get(Call, tenants.a["call"]).report.review_outcome == "confirmed"


# ─────────────────────────── the queue ───────────────────────────

def test_failures_and_passes_are_counted_separately(tenants, db):
    """Every graded call needs sign-off, which only works if the two kinds of
    waiting are distinguished — one needs judgment, the other a confirmation."""
    counts = review.pending_counts(db, tenants.a["org"])       # fixture is CRITICAL
    assert counts["needs_judgment"] == 1 and counts["passes"] == 0

    counts_b = review.pending_counts(db, tenants.b["org"])     # fixture is PASS
    assert counts_b["needs_judgment"] == 0 and counts_b["passes"] == 1


def test_the_queue_is_worst_first_then_oldest(tenants, db):
    """A compliance failure sitting for a week outranks one from this morning —
    the opposite of the newest-first ordering used everywhere else."""
    from datetime import datetime, timedelta, timezone
    from conftest import _mk_call
    from models import Agent, ComplianceProfile, Organization, User

    org = db.get(Organization, tenants.a["org"])
    admin = db.get(User, tenants.a["admin"])
    profile = db.query(ComplianceProfile).filter_by(org_id=org.id).one()
    agent = db.get(Agent, tenants.a["agent"])

    old_fail = _mk_call(db, org, profile, admin, agent, verdict="FAIL", internal_id="OLD")
    old_fail.upload_date = datetime.now(timezone.utc) - timedelta(days=9)
    _mk_call(db, org, profile, admin, agent, verdict="PASS", internal_id="P1")
    db.commit()

    order = [c.report.verdict for c in review.pending(db, org.id)]
    assert order[0] == "critical", "criticals come first"
    assert order[-1] == "pass", "passes come last"

    fails = [c for c in review.pending(db, org.id) if c.report.verdict == "fail"]
    assert fails[0].internal_id == "OLD", "older failures come first"


def test_one_orgs_queue_never_contains_anothers_calls(tenants, db):
    a_ids = {c.id for c in review.pending(db, tenants.a["org"])}
    assert tenants.b["call"] not in a_ids
    assert review.pending_counts(db, tenants.b["org"])["total"] == 1


# ─────────────────────────── the routes ───────────────────────────

def test_signing_off_moves_you_to_the_next_call(tenants, db):
    """A reviewer working a queue should not be bounced back to what they just
    finished."""
    from conftest import _mk_call
    from models import Agent, ComplianceProfile, Organization, User
    org = db.get(Organization, tenants.a["org"])
    _mk_call(db, org, db.query(ComplianceProfile).filter_by(org_id=org.id).one(),
             db.get(User, tenants.a["admin"]), db.get(Agent, tenants.a["agent"]),
             verdict="FAIL", internal_id="NEXT")
    db.commit()

    r = tenants.a_admin.post(f"/calls/{tenants.a['call']}/review",
                             data={"outcome": "confirmed"})
    assert r.status_code == 302
    assert "/report" in r.headers["Location"], "should land on the next call"
    assert tenants.a["call"] not in r.headers["Location"]


def test_the_last_call_returns_you_to_the_queue(tenants, db):
    r = tenants.a_admin.post(f"/calls/{tenants.a['call']}/review",
                             data={"outcome": "confirmed"})
    assert r.headers["Location"].endswith("/calls/review")


def test_you_cannot_sign_off_another_orgs_call(tenants, db):
    r = tenants.b_admin.post(f"/calls/{tenants.a['call']}/review",
                             data={"outcome": "confirmed"})
    assert r.status_code in (401, 403, 404)
    db.expire_all()
    assert db.get(Call, tenants.a["call"]).report.reviewed_at is None


def test_a_missing_outcome_is_rejected(tenants, db):
    tenants.a_admin.post(f"/calls/{tenants.a['call']}/review", data={})
    db.expire_all()
    assert db.get(Call, tenants.a["call"]).report.reviewed_at is None


# ─────────────────────────── bulk confirm ───────────────────────────

def test_bulk_confirm_clears_passing_calls(tenants, db):
    r = tenants.b_admin.post("/calls/review/bulk",
                             data={"call_id": tenants.b["call"]})
    assert r.status_code == 302
    db.expire_all()
    assert review.pending_counts(db, tenants.b["org"])["total"] == 0


def test_bulk_confirm_refuses_to_clear_a_failure(tenants, db):
    """A failure is a judgment somebody has to make having read it. Bulk is a
    confirmation path, and must not become a way to clear failures unread —
    including by editing the form."""
    r = tenants.a_admin.post("/calls/review/bulk",
                             data={"call_id": tenants.a["call"]})
    assert r.status_code == 302
    db.expire_all()
    assert db.get(Call, tenants.a["call"]).report.reviewed_at is None, \
        "a failing call was cleared without anyone reading it"


def test_bulk_confirm_cannot_reach_another_org(tenants, db):
    tenants.a_admin.post("/calls/review/bulk", data={"call_id": tenants.b["call"]})
    db.expire_all()
    assert db.get(Call, tenants.b["call"]).report.reviewed_at is None


# ─────────────────────── the trap in the UI ───────────────────────

def test_dismissing_says_plainly_that_it_does_not_change_the_score(tenants):
    """Everyone assumes dismissal fixes the agent's number. It does not — only
    item overrides do. If that sentence ever disappears, the product starts
    quietly misleading people about their own data."""
    body = tenants.a_admin.get(f"/calls/{tenants.a['call']}/report").get_data(as_text=True)
    assert "doesn't change" in body and "score" in body
    assert "approve that item" in body
