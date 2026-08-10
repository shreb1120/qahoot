"""The pipeline's two money-losing failure modes.

Both are invisible in normal operation and only show up on a bad day:

* A deploy kills every worker mid-flight. Those calls were already paid for at
  AssemblyAI. Without recovery they sit in `transcribing` forever — the call
  looks stuck to the customer and the money is simply gone.
* The grading request shape is load-bearing for cost. Opus 5 thinks by default
  and `max_tokens` bounds thinking plus output together, so a request built the
  old way can truncate a report; and the checklist prefix only caches if it is
  actually marked cacheable.

Neither is covered by the happy path, so they get their own tests.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import pipeline


# ──────────────────────────── restart recovery ────────────────────────────

@pytest.fixture
def queued(monkeypatch):
    """Capture what recover_stranded would hand to the worker pool."""
    seen = []
    monkeypatch.setattr(pipeline, "spawn", lambda **kw: seen.append(kw))
    return seen


def _stranded_call(db, tenants, status, *, age_minutes, audio="/tmp/x.mp3"):
    from models import Call
    c = Call(
        id=str(uuid.uuid4()), org_id=tenants.a["org"],
        uploaded_by_user_id=tenants.a["admin"],
        filename="stranded.mp3", status=status,
        audio_file_url=audio,
        upload_date=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
    )
    db.add(c)
    db.commit()
    return c


@pytest.mark.parametrize("status", ["pending", "transcribing", "analyzing"])
def test_a_call_left_mid_flight_is_requeued(tenants, db, queued, status):
    call = _stranded_call(db, tenants, status, age_minutes=45)
    assert pipeline.recover_stranded("aai", "anth") == 1
    assert queued[0]["call_id"] == call.id
    assert queued[0]["file_path"] == "/tmp/x.mp3"


def test_a_recently_started_call_is_left_alone(tenants, db, queued):
    """A call two minutes into transcription is working, not stranded."""
    _stranded_call(db, tenants, "transcribing", age_minutes=2)
    assert pipeline.recover_stranded("aai", "anth") == 0
    assert queued == []


def test_finished_calls_are_never_requeued(tenants, db, queued):
    """Re-running a complete call would pay AssemblyAI again for nothing."""
    _stranded_call(db, tenants, "complete", age_minutes=999)
    _stranded_call(db, tenants, "error", age_minutes=999)
    assert pipeline.recover_stranded("aai", "anth") == 0


def test_a_call_whose_upload_never_landed_is_closed_out(tenants, db, queued):
    """No audio on disk means there is nothing to re-run. Marking it error
    stops it showing as in-flight forever."""
    from models import Call
    call = _stranded_call(db, tenants, "pending", age_minutes=45, audio=None)

    assert pipeline.recover_stranded("aai", "anth") == 0
    assert queued == []

    db.expire_all()
    refreshed = db.query(Call).filter_by(id=call.id).one()
    assert refreshed.status == "error"
    assert "did not complete" in (refreshed.error_message or "")


def test_recovery_crosses_orgs(tenants, db, queued):
    """Recovery is an operational sweep, not a tenant-scoped query — a stranded
    call belonging to any org must be picked up."""
    from models import Call
    other = Call(
        id=str(uuid.uuid4()), org_id=tenants.b["org"],
        uploaded_by_user_id=tenants.b["admin"],
        filename="b.mp3", status="analyzing", audio_file_url="/tmp/b.mp3",
        upload_date=datetime.now(timezone.utc) - timedelta(minutes=45),
    )
    db.add(other)
    db.commit()
    assert pipeline.recover_stranded("aai", "anth") == 1


# ───────────────────────── grading request shape ─────────────────────────

class _Recorder:
    """Stands in for the Anthropic client and records the request."""
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        raise RuntimeError("stop here — we only want the request")


def _capture_request(monkeypatch, tenants, db):
    """Run the pipeline far enough to build the Anthropic request, then stop."""
    rec = _Recorder()

    class FakeClient:
        def __init__(self, **_):
            self.messages = rec

    class FakeUtt:
        speaker, start, end, text = "A", 0, 1000, "hello"

    class FakeResult:
        # `completed` on the first look, so the deadline loop in
        # _transcribe_with_deadline exits without polling.
        id = "tr_fake"
        status = "completed"
        error = None
        text = "hello"
        utterances = [FakeUtt()]
        audio_duration = 600

    class FakeTranscriber:
        # The pipeline submits and polls to its own deadline rather than
        # calling the SDK's unbounded transcribe().
        def submit(self, *a, **kw):
            return FakeResult()

    monkeypatch.setattr(pipeline.anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(pipeline.aai, "Transcriber", FakeTranscriber)
    monkeypatch.setattr(pipeline.aai, "settings", type("S", (), {"api_key": ""})())

    pipeline.run_pipeline(tenants.a["call"], "/tmp/x.mp3", "aai", "anth")
    return rec.kwargs


def test_rerunning_a_call_that_already_has_a_transcript_does_not_collide(
        tenants, db, monkeypatch):
    """The exact case recovery exists for: a call stranded in `analyzing`
    already carries a transcript, and transcripts.call_id is UNIQUE. A plain
    insert fails on precisely the calls we are trying to rescue."""
    from models import Transcript
    call_id = tenants.a["call"]
    assert db.query(Transcript).filter_by(call_id=call_id).count() == 1

    _capture_request(monkeypatch, tenants, db)   # runs transcription, then stops

    db.expire_all()
    assert db.query(Transcript).filter_by(call_id=call_id).count() == 1
    assert db.query(Transcript).filter_by(call_id=call_id).one() \
             .raw_transcript_json["text"] == "hello"


def test_the_checklist_prefix_is_marked_cacheable(tenants, db, monkeypatch):
    """Opus 4.6 silently refused to cache a prefix under 4,096 tokens, so this
    never engaged. It only pays off if the block is actually marked."""
    kw = _capture_request(monkeypatch, tenants, db)
    assert isinstance(kw["system"], list), "system must be blocks to carry cache_control"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_thinking_is_disabled_and_max_tokens_leaves_room(tenants, db, monkeypatch):
    """Opus 5 thinks by default and max_tokens bounds thinking + output
    together. Reports run ~1,650 tokens; 4,096 with thinking on can truncate."""
    kw = _capture_request(monkeypatch, tenants, db)
    assert kw["model"] == "claude-opus-5"
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["max_tokens"] >= 8000
