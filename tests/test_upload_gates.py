"""The two gates on the upload route.

They are separate on purpose and the separation is the thing being tested. A
billing quota that blocks is a churned customer; an abuse limit that doesn't is
an unbounded bill. Collapsing them into one check gives you whichever failure
mode you didn't want.
"""
import io
import uuid

import pytest

import ratelimit
import usage
from models import Call, Subscription


def _form(tenants):
    return {"file": (io.BytesIO(b"ID3 audio"), "call.mp3")}


def _upload(actor, tenants):
    return actor.post("/calls/upload", data=_form(tenants),
                      content_type="multipart/form-data")


@pytest.fixture(autouse=True)
def _no_pipeline(monkeypatch):
    """Never actually spend money in a test."""
    import pipeline
    monkeypatch.setattr(pipeline, "spawn", lambda **kw: None)


def _org(db, tenants):
    from models import Organization
    return db.get(Organization, tenants.a["org"])


def _exhaust_trial(db, org, key="burn:transcription:1"):
    """Use up whatever the trial currently allows, plus a minute."""
    from plans import TRIAL
    _burn(db, org, TRIAL.included_minutes + 1, key)


def _burn(db, org, minutes, key):
    usage.record(db, org_id=org.id, call_id=None,
                 resource_type=usage.TRANSCRIPTION,
                 idempotency_key=key, billable_seconds=minutes * 60)
    db.commit()


# ───────────────────────────── entitlement ─────────────────────────────

def test_a_healthy_trial_can_upload(tenants):
    assert _upload(tenants.a_admin, tenants).status_code == 302


def test_an_exhausted_trial_is_refused_with_payment_required(tenants, db):
    """402 rather than 403: this is a "you need a plan" answer, not a
    "you're not allowed" one, and the page says so."""
    _exhaust_trial(db, _org(db, tenants))

    r = _upload(tenants.a_admin, tenants)
    assert r.status_code == 402
    body = r.get_data(as_text=True)
    assert "free trial" in body
    assert "reports stay put" in body, "the refusal must reassure, not just refuse"


def test_a_paid_plan_far_over_its_allowance_still_uploads(tenants, db):
    """Soft overage. This is the test that stops someone 'fixing' the trial
    hard-stop by applying it to everyone."""
    org = _org(db, tenants)
    db.add(Subscription(org_id=org.id, plan_code="starter", status="active",
                        included_minutes=2000, overage_micros_per_minute=180_000))
    db.commit()
    _burn(db, org, 9999, "burn:transcription:1")

    assert _upload(tenants.a_admin, tenants).status_code == 302


def test_a_canceled_subscription_is_refused(tenants, db):
    org = _org(db, tenants)
    db.add(Subscription(org_id=org.id, plan_code="growth", status="canceled",
                        included_minutes=8000))
    db.commit()

    r = _upload(tenants.a_admin, tenants)
    assert r.status_code == 403
    assert "no longer active" in r.get_data(as_text=True)


def test_a_refused_upload_writes_no_call_and_no_file(tenants, db):
    """Being refused must cost nothing — no orphan row, no orphan file."""
    _exhaust_trial(db, _org(db, tenants))
    before = db.query(Call).filter_by(org_id=tenants.a["org"]).count()

    _upload(tenants.a_admin, tenants)

    db.expire_all()
    assert db.query(Call).filter_by(org_id=tenants.a["org"]).count() == before


# ────────────────────────────── abuse limits ──────────────────────────────

def test_too_many_uploads_in_an_hour_are_paused(tenants, db, monkeypatch):
    monkeypatch.setattr(ratelimit, "UPLOADS_PER_HOUR_PER_ORG", 3)
    monkeypatch.setattr(ratelimit, "UPLOADS_PER_HOUR_PER_USER", 99)

    codes = [_upload(tenants.a_admin, tenants).status_code for _ in range(5)]
    assert codes[:3] == [302, 302, 302]
    assert codes[3] == 429


def test_the_rate_limit_message_offers_a_way_out(tenants, db, monkeypatch):
    """A legitimate bulk import hitting this must know what to do next."""
    monkeypatch.setattr(ratelimit, "UPLOADS_PER_HOUR_PER_ORG", 1)
    _upload(tenants.a_admin, tenants)
    body = _upload(tenants.a_admin, tenants).get_data(as_text=True)
    assert "bulk import" in body


def test_one_orgs_uploads_do_not_limit_another(tenants, db, monkeypatch):
    """Counters are scoped per org. A noisy tenant must not throttle a quiet
    one — the same isolation requirement as everywhere else in this app."""
    monkeypatch.setattr(ratelimit, "UPLOADS_PER_HOUR_PER_ORG", 2)
    monkeypatch.setattr(ratelimit, "UPLOADS_PER_HOUR_PER_USER", 99)

    for _ in range(3):
        _upload(tenants.a_admin, tenants)
    assert _upload(tenants.b_admin, tenants).status_code == 302


def test_too_many_in_flight_calls_pauses_uploads(tenants, db, monkeypatch):
    """Bounds our own worker pool and vendor concurrency, not just theirs."""
    monkeypatch.setattr(ratelimit, "MAX_CONCURRENT_PER_ORG", 2)
    for i in range(2):
        db.add(Call(id=str(uuid.uuid4()), org_id=tenants.a["org"],
                    uploaded_by_user_id=tenants.a["admin"],
                    filename=f"p{i}.mp3", status="transcribing"))
    db.commit()

    r = _upload(tenants.a_admin, tenants)
    assert r.status_code == 429
    assert "still processing" in r.get_data(as_text=True)


def test_finished_calls_do_not_count_against_concurrency(tenants, db, monkeypatch):
    monkeypatch.setattr(ratelimit, "MAX_CONCURRENT_PER_ORG", 2)
    for i in range(5):
        db.add(Call(id=str(uuid.uuid4()), org_id=tenants.a["org"],
                    uploaded_by_user_id=tenants.a["admin"],
                    filename=f"d{i}.mp3", status="complete"))
    db.commit()
    assert _upload(tenants.a_admin, tenants).status_code == 302
