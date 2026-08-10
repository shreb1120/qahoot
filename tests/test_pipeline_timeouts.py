"""Vendor deadlines, and the recovery sweeper.

The pool has four workers, and neither vendor SDK gives up on its own.
`transcriber.transcribe()` polls until the job reaches a terminal status with no
ceiling, so a job stuck in `processing` pinned a worker indefinitely — and
`recover_stranded` could not help, because it looks for calls whose *row* is
stale and that call's worker was alive and faithfully polling. Four of those and
the queue stopped moving with nothing in the logs to say why.

Recovery also ran only at startup, so a call abandoned by a crashed worker sat
in `transcribing` until the next deploy.

Fixing the second created a hazard the first never had: a sweep on a timer runs
while workers are alive, so it can re-queue a call that is merely slow. The last
group of tests is about that.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import pipeline
from models import Call


class FakeTranscript:
    def __init__(self, statuses, tid="tr_123"):
        self._statuses = list(statuses)
        self.id = tid
        self.status = self._statuses.pop(0)
        self.error = None

    def advance(self):
        if self._statuses:
            self.status = self._statuses.pop(0)
        return self


@pytest.fixture
def fast_poll(monkeypatch):
    """Keep the deadline logic intact but make the test finish in milliseconds."""
    monkeypatch.setattr(pipeline, "TRANSCRIBE_POLL_INTERVAL", 0)
    slept = []
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: slept.append(s))
    return slept


# ───────────────────── the transcription deadline ─────────────────────

def test_a_job_that_never_finishes_raises_instead_of_polling_forever(monkeypatch,
                                                                     fast_poll):
    """The bug. Without a deadline this loop never exits and the worker is gone
    for good."""
    import assemblyai as aai

    stuck = FakeTranscript(["queued"])
    monkeypatch.setattr(aai.Transcript, "get_by_id",
                        staticmethod(lambda tid: FakeTranscript(["processing"])))

    # Time advances on its own; the vendor never does.
    clock = iter([0] + [i * 100 for i in range(1, 500)])
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: next(clock))

    transcriber = type("T", (), {"submit": lambda self, d, config=None: stuck})()

    with pytest.raises(pipeline.TranscriptionTimeout) as exc:
        pipeline._transcribe_with_deadline(transcriber, "f.mp3", None, "call-1")

    assert "tr_123" in str(exc.value), \
        "the transcript id must be in the error — it is how already-paid-for work is recovered by hand"


def test_a_job_that_completes_is_returned(monkeypatch, fast_poll):
    import assemblyai as aai

    submitted = FakeTranscript(["queued"])
    done = FakeTranscript(["completed"])
    seq = iter([FakeTranscript(["processing"]), done])
    monkeypatch.setattr(aai.Transcript, "get_by_id", staticmethod(lambda tid: next(seq)))
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: 0)

    transcriber = type("T", (), {"submit": lambda self, d, config=None: submitted})()
    out = pipeline._transcribe_with_deadline(transcriber, "f.mp3", None, "call-1")
    assert out.status == "completed"


def test_a_vendor_error_is_returned_not_retried_to_the_deadline(monkeypatch, fast_poll):
    """`error` is terminal. Polling a failed job to the 30-minute deadline would
    hold a worker for half an hour to learn something known immediately."""
    import assemblyai as aai
    submitted = FakeTranscript(["error"])
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: 0)
    transcriber = type("T", (), {"submit": lambda self, d, config=None: submitted})()

    out = pipeline._transcribe_with_deadline(transcriber, "f.mp3", None, "call-1")
    assert out.status == "error"
    assert not fast_poll, "slept while holding a terminal status"


def test_the_deadline_and_the_http_timeout_are_both_set():
    """Two different hangs: a job that never finishes, and a socket that never
    returns. One bound does not cover the other."""
    assert pipeline.TRANSCRIBE_TIMEOUT > 0
    assert pipeline.ASSEMBLYAI_HTTP_TIMEOUT > 0
    assert pipeline.ANTHROPIC_TIMEOUT > 0
    assert pipeline.TRANSCRIBE_TIMEOUT > pipeline.ASSEMBLYAI_HTTP_TIMEOUT


# ───────────────── recovery does not double-run live work ─────────────────

def test_recovery_leaves_a_call_this_process_is_still_working_on(tenants, db,
                                                                 monkeypatch):
    """The hazard the sweeper introduces. A slow-but-healthy transcription is
    stale by the clock while its worker is alive; re-queuing it runs the same
    call twice against both paid vendors."""
    call = db.get(Call, tenants.a["call"])
    call.status = "transcribing"
    call.upload_date = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()

    spawned = []
    monkeypatch.setattr(pipeline, "spawn", lambda **kw: spawned.append(kw["call_id"]))

    with pipeline._lock:
        pipeline._running.add(call.id)
    try:
        queued = pipeline.recover_stranded("aai", "anthropic")
    finally:
        with pipeline._lock:
            pipeline._running.discard(call.id)

    assert spawned == [], "re-ran a call whose worker is still alive"
    assert queued == 0


def test_recovery_still_requeues_a_call_no_worker_here_owns(tenants, db, monkeypatch):
    """The other side: a row stale because another process died has no entry in
    this process's `_running`, and must be picked up."""
    call = db.get(Call, tenants.a["call"])
    call.status = "transcribing"
    call.upload_date = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()

    spawned = []
    monkeypatch.setattr(pipeline, "spawn", lambda **kw: spawned.append(kw["call_id"]))

    assert pipeline.recover_stranded("aai", "anthropic") == 1
    assert spawned == [call.id]


# ───────────────────────── the sweeper ─────────────────────────

@pytest.fixture(autouse=True)
def _no_leaked_sweeper():
    """A sweeper that outlives its test keeps calling the real recovery against
    a torn-down schema, and the failure surfaces in whatever test runs next."""
    yield
    pipeline.stop_recovery_sweeper(timeout=2)



def test_the_sweeper_runs_recovery_more_than_once(monkeypatch):
    """The point of it. Startup-only recovery left abandoned work until the next
    deploy."""
    calls = []
    done = threading.Event()

    def fake_recover(a, b):
        calls.append((a, b))
        if len(calls) >= 3:
            done.set()
        return 0

    monkeypatch.setattr(pipeline, "recover_stranded", fake_recover)
    monkeypatch.setattr(pipeline, "_sweeper", None)

    assert pipeline.start_recovery_sweeper("aai", "anthropic", interval=0.01)
    assert done.wait(timeout=5), "the sweeper did not run repeatedly"
    assert calls[0] == ("aai", "anthropic")


def test_a_failing_sweep_does_not_kill_the_sweeper(monkeypatch):
    """A transient DB blip must not silently disable recovery for the life of
    the process — that is the exact failure the sweeper exists to prevent."""
    attempts = []
    done = threading.Event()

    def explodes(a, b):
        attempts.append(1)
        if len(attempts) >= 3:
            done.set()
        raise RuntimeError("database went away")

    monkeypatch.setattr(pipeline, "recover_stranded", explodes)
    monkeypatch.setattr(pipeline, "_sweeper", None)

    pipeline.start_recovery_sweeper("aai", "anthropic", interval=0.01)
    assert done.wait(timeout=5), "the sweeper stopped after a failing sweep"


def test_only_one_sweeper_runs(monkeypatch):
    monkeypatch.setattr(pipeline, "recover_stranded", lambda a, b: 0)
    monkeypatch.setattr(pipeline, "_sweeper", None)

    assert pipeline.start_recovery_sweeper("a", "b", interval=30) is True
    assert pipeline.start_recovery_sweeper("a", "b", interval=30) is False


def test_creating_the_app_starts_no_sweeper():
    """Building an app must stay free of side effects. A thread started at import
    time writes to whatever database the ambient config points at — which is how
    the test suite once connected to production.

    Parsed rather than grepped: app.py carries a comment explaining why these
    live in serve.py, and a substring check would read that as a violation."""
    import ast
    import inspect
    import app as app_module

    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else
        getattr(node.func, "id", "")
        for node in ast.walk(ast.parse(inspect.getsource(app_module)))
        if isinstance(node, ast.Call)
    }
    assert "start_recovery_sweeper" not in called
    assert "recover_stranded" not in called


# ─────────── the sweeper must not re-run work it has already queued ───────────

def test_recovery_leaves_alone_a_call_that_is_queued_but_not_yet_started(tenants, db,
                                                                        monkeypatch):
    """The bug the first version of the sweeper shipped with.

    `_running` is populated by a worker when it *starts*. A call sitting in the
    pool's queue is in neither `_running` nor a terminal state, so recovery saw
    it as abandoned. A bulk import whose tail waited past STRANDED_AFTER was
    re-submitted every ten minutes — paying AssemblyAI and Anthropic again on
    each sweep while the customer was correctly billed once.

    Simulated by submitting to a pool that never runs anything, which is exactly
    what a saturated pool looks like from outside.
    """
    call = db.get(Call, tenants.a["call"])
    call.status = "pending"
    call.upload_date = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()

    class NeverRuns:
        def submit(self, *a, **kw):
            return None

    monkeypatch.setattr(pipeline, "_get_pool", lambda: NeverRuns())
    pipeline.spawn(call_id=call.id, file_path="/tmp/x.mp3",
                   assemblyai_key="a", anthropic_key="b")
    assert call.id in pipeline._queued, "spawn did not record the call as queued"

    resubmitted = []
    monkeypatch.setattr(pipeline, "spawn",
                        lambda **kw: resubmitted.append(kw["call_id"]))
    try:
        assert pipeline.recover_stranded("aai", "anthropic") == 0
        assert resubmitted == [], "re-queued a call that was already waiting in the pool"
    finally:
        with pipeline._lock:
            pipeline._queued.discard(call.id)


def test_a_failed_submit_does_not_leave_the_call_marked_as_queued(tenants, db,
                                                                  monkeypatch):
    """The opposite failure: a call stuck as 'queued' when nothing holds it
    would be invisible to recovery forever."""
    class Broken:
        def submit(self, *a, **kw):
            raise RuntimeError("pool is shut down")

    monkeypatch.setattr(pipeline, "_get_pool", lambda: Broken())
    with pytest.raises(RuntimeError):
        pipeline.spawn(call_id="call-x", file_path="/tmp/x.mp3",
                       assemblyai_key="a", anthropic_key="b")
    assert "call-x" not in pipeline._queued


def test_a_finished_call_stops_being_queued(tenants, db, monkeypatch):
    """Otherwise the set grows forever and recovery stops working for those ids."""
    call = db.get(Call, tenants.a["call"])
    with pipeline._lock:
        pipeline._queued.add(call.id)

    monkeypatch.setattr(pipeline.aai, "settings", type("S", (), {"api_key": ""})())
    monkeypatch.setattr(pipeline, "_get_pool", lambda: None)
    # Run with a key that fails fast; we only care that the finally block clears.
    pipeline.run_pipeline(call.id, "/nonexistent.mp3", "bad", "bad")
    assert call.id not in pipeline._queued
    assert call.id not in pipeline._running
