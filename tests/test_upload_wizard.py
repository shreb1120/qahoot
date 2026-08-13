"""Bulk upload, and the fields we should never have asked for.

The old screen asked for agent, internal ID, call date and phone before you
could choose a file — and took exactly one file. A week of calls meant thirty
or forty separate trips through it, retyping a date and a phone number that
were already written in every filename.

Now: choose files, answer one question if there is more than one, then fill in
only what the filenames could not answer.
"""
import io
from datetime import date, timedelta

import pytest

from models import Call
from upload_meta import normalise_phone, parse_filename


# ─────────────────── what a filename already tells us ───────────────────

@pytest.mark.parametrize("name,d,phone", [
    ("20260810-172905_3616522851-all.mp3", date(2026, 8, 10), "3616522851"),
    ("20260126-075444_5679_3134082479-all.mp3", date(2026, 1, 26), "3134082479"),
    ("20260126111735_6820_919513097039-all.mp3", date(2026, 1, 26), "9513097039"),
    ("20260807-142611_2074769156-all.mp3", date(2026, 8, 7), "2074769156"),
])
def test_real_dialer_filenames_are_read(name, d, phone):
    """Every one of these is a filename taken from the live database."""
    got = parse_filename(name)
    assert got["call_date"] == d
    assert got["client_phone"] == phone


@pytest.mark.parametrize("name", [
    "recording.mp3", "", "notes-about-the-call.m4a", "2026-08-10 call.mp3",
])
def test_an_unreadable_filename_asks_rather_than_guesses(name):
    got = parse_filename(name)
    assert got["call_date"] is None and got["client_phone"] is None


def test_an_impossible_date_is_left_for_a_person():
    """20261399 is not a date. Attaching a wrong one to a compliance record is
    worse than attaching none."""
    assert parse_filename("20261399-000000_5551234567.mp3")["call_date"] is None


def test_a_future_date_is_refused():
    """A recording dated next year is a filename that does not mean what it
    looks like."""
    future = (date.today() + timedelta(days=400)).strftime("%Y%m%d")
    assert parse_filename(f"{future}-120000_5551234567.mp3")["call_date"] is None


@pytest.mark.parametrize("raw,out", [
    ("13616522851", "3616522851"),      # country code trimmed
    ("919513097039", "9513097039"),
    ("3616522851", "3616522851"),
    ("12345", None),                     # too short to be a number
])
def test_phone_normalisation(raw, out):
    assert normalise_phone(raw) == out


def test_a_ten_digit_number_starting_with_one_is_not_truncated():
    """1-prefix trimming must not eat a digit from a number that is already the
    right length."""
    assert normalise_phone("1234567890") == "1234567890"


# ─────────────────────────── the batch ───────────────────────────

def _mp3(name):
    return (io.BytesIO(b"ID3" + b"\x00" * 2048), name)


def _batch(tenants, n=3, **extra):
    data = {"csrf_token": "x",
            "files": [_mp3(f"20260810-1200{i:02d}_555000{i:04d}-all.mp3") for i in range(n)]}
    data.update(extra)
    return data


def test_several_recordings_arrive_as_several_calls(tenants, db, monkeypatch):
    import pipeline
    monkeypatch.setattr(pipeline, "spawn", lambda **kw: None)
    before = db.query(Call).filter_by(org_id=tenants.a["org"]).count()

    r = tenants.a_admin.post("/calls/upload", data=_batch(tenants, 4),
                             content_type="multipart/form-data")
    assert r.status_code in (302, 303)
    after = db.query(Call).filter_by(org_id=tenants.a["org"]).count()
    assert after - before == 4, "a batch of four produced something other than four calls"


def test_each_file_keeps_its_own_details(tenants, db, monkeypatch):
    """The 'different clients' branch. Details are indexed per file, so they
    must not smear across the batch."""
    import pipeline
    monkeypatch.setattr(pipeline, "spawn", lambda **kw: None)
    data = _batch(tenants, 2)
    data.update({"internal_id_0": "AAA-1", "internal_id_1": "BBB-2",
                 "client_phone_0": "5551110000", "client_phone_1": "5552220000"})
    tenants.a_admin.post("/calls/upload", data=data, content_type="multipart/form-data")

    ids = {c.internal_id: c.client_phone
           for c in db.query(Call).filter_by(org_id=tenants.a["org"]).all()
           if c.internal_id in ("AAA-1", "BBB-2")}
    assert ids == {"AAA-1": "5551110000", "BBB-2": "5552220000"}


def test_one_bad_file_does_not_discard_the_batch(tenants, db, monkeypatch):
    """A partially-accepted import is hard to unpick, but rejecting nine good
    recordings because the tenth was unreadable is worse."""
    import pipeline
    monkeypatch.setattr(pipeline, "spawn", lambda **kw: None)
    data = {"csrf_token": "x", "files": [
        _mp3("20260810-120000_5550000001-all.mp3"),
        (io.BytesIO(b""), "20260810-120001_5550000002-all.mp3"),   # empty
        _mp3("20260810-120002_5550000003-all.mp3"),
    ]}
    before = db.query(Call).filter_by(org_id=tenants.a["org"]).count()
    r = tenants.a_admin.post("/calls/upload", data=data, content_type="multipart/form-data")
    assert r.status_code in (302, 303)
    assert db.query(Call).filter_by(org_id=tenants.a["org"]).count() - before == 2


def test_the_whole_batch_is_rejected_before_anything_is_written(tenants, db):
    """Validation runs over every file first. A rejection must leave no rows —
    six in and four refused, with no clear boundary, is the worst outcome."""
    before = db.query(Call).filter_by(org_id=tenants.a["org"]).count()
    data = _batch(tenants, 3)
    data["files"].append(_mp3("notes.txt"))
    r = tenants.a_admin.post("/calls/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 400
    assert db.query(Call).filter_by(org_id=tenants.a["org"]).count() == before


def test_a_batch_counts_as_many_uploads_not_one(tenants, db):
    """The rate limit bounds vendor spend, and spend follows recordings. A
    limiter counting requests would let a hundred calls through on one tick."""
    import ratelimit
    from models import Organization, User
    org = db.get(Organization, tenants.a["org"])
    user = db.get(User, tenants.a["admin"])

    ratelimit.check_upload(db, org, user, count=5)
    db.commit()
    from sqlalchemy import select
    from models import RateLimitCounter
    total = db.execute(
        select(RateLimitCounter.count).where(
            RateLimitCounter.scope_key == f"org:{org.id}:upload")
    ).scalars().first()
    assert total >= 5, "a batch of five was counted as fewer than five uploads"


def test_the_upload_page_offers_multiple_selection(tenants):
    body = tenants.a_admin.get("/calls/upload").get_data(as_text=True)
    assert 'name="files"' in body and "multiple" in body


def test_the_first_screen_asks_for_nothing_but_the_files(tenants):
    """The point of the redesign. Metadata inputs may exist in later steps, but
    step one is a single target."""
    body = tenants.a_admin.get("/calls/upload").get_data(as_text=True)
    step1 = body.split('id="up-step-2"')[0]
    for asked in ('name="internal_id"', 'name="call_date"', 'name="client_phone"'):
        assert asked not in step1, f"step one still asks for {asked}"


# ─────────────────── stored XSS through an agent name ───────────────────

def test_an_agent_name_cannot_inject_script_into_the_upload_page(tenants, db):
    """Found by a security review of the batch-upload work.

    The wizard builds its rows with string concatenation and insertAdjacentHTML,
    and agent names are typed by an admin or pasted in from a CSV import. An
    agent named `<img src=x onerror=…>` would have executed for every member of
    that org who opened the upload page — stored XSS, inside the tenant.
    """
    from models import Agent
    import uuid

    payload = '<img src=x onerror="alert(1)">'
    db.add(Agent(id=str(uuid.uuid4()), org_id=tenants.a["org"], name=payload))
    db.commit()

    body = tenants.a_admin.get("/calls/upload").get_data(as_text=True)
    assert payload not in body, "the raw agent name reached the page"
    # It must still be *present*, escaped — dropping the agent is not the fix.
    assert "img src=x" in body.replace("\\u003c", "<").replace("\\u003e", ">") or \
           "alert(1)" in body, "the agent vanished instead of being escaped"


def test_the_wizard_builds_no_html_from_strings(tenants):
    """The structural fix, replacing an escaping rule nobody could be trusted to
    remember.

    Three values reached insertAdjacentHTML unescaped across two security
    reviews — an agent name, a filename, and the parsed date and number — and
    each was fixed by adding one more esc() call. The rows are DOM nodes now, so
    .value and .textContent cannot interpret markup and there is nothing left to
    forget.

    Asserted on the property rather than on the fix: any reintroduction of
    string-built markup fails this, whatever it ends up being called.
    """
    import re
    body = tenants.a_admin.get("/calls/upload").get_data(as_text=True)
    js = body[body.index('id="upload-form"'):]

    assert ".innerHTML" not in js, "markup is being assigned as a string again"

    # insertAdjacentHTML survives in exactly one place, for a constant SVG with
    # nothing interpolated into it. Any other argument is a regression.
    for call in re.findall(r"insertAdjacentHTML\(([^)]*)\)", js):
        assert "ALERT_ICON" in call, f"insertAdjacentHTML({call}) builds markup from data"

    assert "textContent" in js, "rows are not being built through the DOM API"
