"""
Background pipeline: AssemblyAI transcription → Claude analysis → save to DB.

Runs on a worker pool outside Flask's request context. Uses get_db() directly
(which creates a new session) rather than Flask's g.db.

Two properties this module has to hold, because both cost real money:

* **Bounded concurrency.** Work runs on a fixed pool, not one thread per
  upload. A customer importing forty calls used to spawn forty threads against
  a connection pool of five, each holding a vendor API call open.
* **Nothing is silently abandoned.** Workers are daemons, so a deploy still
  kills them mid-flight — but `recover_stranded` re-queues anything left in a
  non-terminal state at startup. Without it, a call sits in `transcribing`
  forever having already been paid for at AssemblyAI.
"""

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import anthropic
import assemblyai as aai

import usage
from db import get_db
from prompt_builder import build_system_prompt, build_user_message
from report_normalizer import normalize_report

logger = logging.getLogger(__name__)

# Track in-flight pipeline calls to prevent duplicate runs. In-process only —
# it is a courtesy, not a guarantee. Exactly-once metering is enforced by the
# unique idempotency key on usage_events, not by this set.
_running: set = set()
_lock = threading.Lock()

# Bounded pool. Four concurrent calls is comfortably above one customer's
# normal burst and comfortably below the SQLAlchemy pool (5 + 10 overflow),
# leaving room for the web threads that actually serve requests.
MAX_WORKERS = 4
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()

# How long a call may sit in a non-terminal state before we assume its worker
# died. Comfortably longer than a slow transcription of a 69-minute call.
STRANDED_AFTER = timedelta(minutes=30)
ACTIVE_STATUSES = ("pending", "transcribing", "analyzing")

# ── Vendor deadlines ────────────────────────────────────────────────────────
#
# Everything that talks to a vendor gets a bound, because the pool has four
# workers and neither SDK gives up on its own.
#
# `transcriber.transcribe()` uploads, then polls until the job reaches a
# terminal status — with no ceiling. A job that stays `processing` pins one of
# four workers indefinitely, and `recover_stranded` could not free it: that
# looks for calls whose row is stale, and this call's worker is alive and
# faithfully polling. Three workers in that state and the queue stops moving,
# with nothing in the logs to say why.
#
# So the wait is ours, not the SDK's, and it is bounded twice: per HTTP request
# (a hung socket) and overall (a job that never finishes).
ASSEMBLYAI_HTTP_TIMEOUT = 300     # one request; generous — this covers uploads
TRANSCRIBE_TIMEOUT = 1800         # the whole job, ~4x the slowest real call
TRANSCRIBE_POLL_INTERVAL = 5

# The Anthropic SDK defaults to a 10-minute timeout and 2 retries. Both are
# stated here rather than inherited, so a default change upstream cannot
# quietly alter how long a worker is held.
ANTHROPIC_TIMEOUT = 600
ANTHROPIC_MAX_RETRIES = 2


class TranscriptionTimeout(RuntimeError):
    """The job did not reach a terminal status inside TRANSCRIBE_TIMEOUT.

    Distinct from a vendor error: the transcript may still complete on
    AssemblyAI's side, and we have already paid for it either way.
    """


MODEL = "claude-opus-5"
# Generous because max_tokens bounds the whole response. Reports run ~1,650
# tokens; the headroom is what stops a long checklist truncating one.
MAX_TOKENS = 16000

# Anthropic list prices for MODEL, in micros ($1 = 1_000_000) per token.
# Cache reads cost a tenth of full price, which is the entire point of marking
# the checklist prefix cacheable.
_IN_MICROS_PER_TOKEN = 5.0
_OUT_MICROS_PER_TOKEN = 25.0
_CACHE_READ_MULTIPLIER = 0.1
_CACHE_WRITE_MULTIPLIER = 1.25


def _transcription_cost_micros(seconds: int) -> int:
    """What AssemblyAI charged, from a configured hourly rate.

    Defaults to 0 rather than a guess: an unset rate should make margin
    reporting obviously empty, not quietly wrong.
    """
    rate = float(os.environ.get("ASSEMBLYAI_COST_PER_HOUR", "0") or 0)
    return int(round(seconds / 3600 * rate * 1_000_000))


def _analysis_cost_micros(response) -> int:
    u = getattr(response, "usage", None)
    if u is None:
        return 0
    g = lambda n: int(getattr(u, n, 0) or 0)
    return int(round(
        g("input_tokens") * _IN_MICROS_PER_TOKEN
        + g("output_tokens") * _OUT_MICROS_PER_TOKEN
        + g("cache_read_input_tokens") * _IN_MICROS_PER_TOKEN * _CACHE_READ_MULTIPLIER
        + g("cache_creation_input_tokens") * _IN_MICROS_PER_TOKEN * _CACHE_WRITE_MULTIPLIER
    ))


def _usage_meta(response) -> dict:
    u = getattr(response, "usage", None)
    g = lambda n: int(getattr(u, n, 0) or 0) if u is not None else 0
    return {
        "model": MODEL,
        "input_tokens": g("input_tokens"),
        "output_tokens": g("output_tokens"),
        "cache_read_input_tokens": g("cache_read_input_tokens"),
        "cache_creation_input_tokens": g("cache_creation_input_tokens"),
    }


def _attempt(db, call_id: str) -> int:
    """Which grading attempt this is, so a re-grade records its real cost.

    Transcription is pinned at attempt 1 — minutes are billed once. Grading is
    not: a re-run spends tokens again, and the ledger should say so.
    """
    from models import UsageEvent
    prior = db.query(UsageEvent).filter(
        UsageEvent.call_id == call_id,
        UsageEvent.resource_type == "analysis",
    ).count()
    return prior + 1


def _log_usage(call_id: str, response) -> None:
    """Record what the grading call actually consumed.

    Cheap insurance against the failure this replaces: nothing in the app read
    `response.usage`, so token spend and cache behaviour were both invisible.
    A cache_read of 0 on every call is the signal that the prefix is being
    invalidated somewhere.
    """
    u = getattr(response, "usage", None)
    if u is None:
        return
    logger.info(
        "Pipeline [%s]: usage in=%s out=%s cache_read=%s cache_write=%s",
        call_id,
        getattr(u, "input_tokens", None),
        getattr(u, "output_tokens", None),
        getattr(u, "cache_read_input_tokens", None),
        getattr(u, "cache_creation_input_tokens", None),
    )


def _get_pool() -> ThreadPoolExecutor:
    """Lazily create the shared pool, so importing this module starts nothing."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=MAX_WORKERS, thread_name_prefix="pipeline"
            )
        return _pool


def _transcribe_with_deadline(transcriber, file_path, config, call_id: str):
    """Submit, then poll to a wall-clock deadline instead of forever.

    `submit()` returns as soon as the job is queued, so the unbounded wait
    inside `transcribe()` is replaced by one we control. The transcript id is
    logged before the first poll: if we do time out, that id is how the work
    already paid for can be retrieved by hand rather than bought twice.
    """
    transcript = transcriber.submit(file_path, config=config)
    logger.info("Pipeline [%s]: AssemblyAI transcript id %s", call_id, transcript.id)

    deadline = time.monotonic() + TRANSCRIBE_TIMEOUT
    terminal = (aai.TranscriptStatus.completed, aai.TranscriptStatus.error)

    while transcript.status not in terminal:
        if time.monotonic() >= deadline:
            raise TranscriptionTimeout(
                f"AssemblyAI did not finish within {TRANSCRIBE_TIMEOUT}s "
                f"(transcript {transcript.id}, last status {transcript.status})"
            )
        time.sleep(TRANSCRIBE_POLL_INTERVAL)
        transcript = aai.Transcript.get_by_id(transcript.id)

    return transcript


def _format_transcript(utterances) -> str:
    lines = []
    for utt in utterances:
        ms = utt.start
        minutes = ms // 60000
        seconds = (ms % 60000) // 1000
        lines.append(f"[{minutes:02d}:{seconds:02d}] Speaker {utt.speaker}: {utt.text}")
    return "\n".join(lines)


def _set_status(db, call, status: str, error: str | None = None) -> None:
    call.status = status
    if error is not None:
        call.error_message = error
    db.commit()


def run_pipeline(call_id: str, file_path: str,
                 assemblyai_key: str, anthropic_key: str) -> None:
    """Entry point — called via threading.Thread(target=run_pipeline, ...)."""
    with _lock:
        if call_id in _running:
            logger.warning("Pipeline [%s]: already running, skipping duplicate", call_id)
            return
        _running.add(call_id)

    db = get_db()
    call = None
    try:
        from models import Call, ComplianceProfile, Report, Transcript

        call = db.query(Call).filter_by(id=call_id).first()
        if not call:
            logger.error("Pipeline [%s]: call record not found", call_id)
            return

        profile = (
            db.query(ComplianceProfile)
            .filter_by(org_id=call.org_id, is_active=True)
            .first()
        )
        if not profile:
            _set_status(db, call, "error",
                        "No active compliance profile found for this org.")
            return

        profile_data = profile.script_sections_json

        # ── Step 1: Transcribe ─────────────────────────────────────────────
        _set_status(db, call, "transcribing")
        logger.info("Pipeline [%s]: transcribing %s", call_id, file_path)

        aai.settings.api_key = assemblyai_key
        aai.settings.http_timeout = ASSEMBLYAI_HTTP_TIMEOUT
        config = aai.TranscriptionConfig(speaker_labels=True)
        transcriber = aai.Transcriber()
        result = _transcribe_with_deadline(transcriber, file_path, config, call_id)

        if result.status == aai.TranscriptStatus.error:
            raise RuntimeError(f"AssemblyAI error: {result.error}")

        utterances = result.utterances or []
        transcript_text = _format_transcript(utterances)
        if not transcript_text:
            transcript_text = result.text or ""

        transcript_json = {
            "utterances": [
                {
                    "speaker": u.speaker,
                    "start": u.start,
                    "end": u.end,
                    "text": u.text,
                }
                for u in utterances
            ],
            "text": result.text or "",
        }

        # Overwrite rather than insert. A call re-queued by recover_stranded
        # from the `analyzing` state already carries a transcript, and both
        # transcripts.call_id and reports.call_id are UNIQUE — a plain insert
        # would fail on exactly the calls recovery exists to rescue.
        existing = db.query(Transcript).filter_by(call_id=call_id).first()
        if existing:
            existing.raw_transcript_json = transcript_json
        else:
            db.add(Transcript(call_id=call_id, raw_transcript_json=transcript_json))

        if result.audio_duration:
            call.duration = int(result.audio_duration)
        else:
            # Do not invent a number here. Estimating from file size would mean
            # guessing a bitrate that varies ~10x across the allowed formats,
            # and this figure goes on an invoice. Meter zero and flag it.
            logger.warning(
                "Pipeline [%s]: AssemblyAI returned no duration — metering 0 "
                "minutes, needs manual review", call_id,
            )

        # Meter the transcription inside this transaction. AssemblyAI has
        # already been paid by the time we get here, so the minutes are owed
        # whether or not grading later succeeds — and the commit below is what
        # puts them beyond reach of the rollback in the error handler.
        usage.record(
            db,
            org_id=call.org_id,
            call_id=call_id,
            resource_type=usage.TRANSCRIPTION,
            # Pinned at attempt 1: a re-queued or re-graded call must never
            # re-bill minutes the customer already paid for.
            idempotency_key=f"{call_id}:transcription:1",
            billable_seconds=int(call.duration or 0),
            vendor_cost_micros=_transcription_cost_micros(call.duration or 0),
            meta={"source": "assemblyai", "duration_reported": bool(result.audio_duration)},
        )
        db.commit()

        logger.info("Pipeline [%s]: transcription done (%d chars)",
                    call_id, len(transcript_text))

        # ── Step 2: Analyze with Claude ────────────────────────────────────
        _set_status(db, call, "analyzing")
        logger.info("Pipeline [%s]: running Claude analysis", call_id)

        client = anthropic.Anthropic(
            api_key=anthropic_key,
            timeout=ANTHROPIC_TIMEOUT,
            max_retries=ANTHROPIC_MAX_RETRIES,
        )
        system_prompt = build_system_prompt(profile_data)
        user_msg = build_user_message(transcript_text)

        # The checklist prefix is identical on every call for an org, so it is
        # marked cacheable — cache reads cost a tenth of full price. This only
        # works on a model whose minimum cacheable prefix is below our ~1,470
        # tokens: Opus 4.6 required 4,096 and silently never cached, Opus 5
        # requires 512. Same price per token either way.
        #
        # Thinking is DISABLED deliberately. Opus 5 turns it on by default and
        # max_tokens caps thinking and response text together, so leaving the
        # default would both change the cost profile and risk truncating a
        # report mid-way. Enabling it is a separate experiment that needs its
        # own measurement, not a side effect of switching models.
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "disabled"},
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_msg}],
        )
        _log_usage(call_id, response)

        # Meter the grading call *here*, and commit immediately — before the
        # JSON extraction below, which can raise, and before normalize_report,
        # which can too. Both of those failures happen after Anthropic has
        # already charged us; anything still uncommitted when the error handler
        # rolls back is simply lost.
        usage.record(
            db,
            org_id=call.org_id,
            call_id=call_id,
            resource_type=usage.ANALYSIS,
            idempotency_key=f"{call_id}:analysis:{_attempt(db, call_id)}",
            billable_seconds=0,          # minutes are billed once, at transcription
            vendor_cost_micros=_analysis_cost_micros(response),
            meta=_usage_meta(response),
        )
        db.commit()

        raw = response.content[0].text
        start_idx = raw.find("{")
        end_idx = raw.rfind("}") + 1
        if start_idx == -1 or end_idx <= start_idx:
            raise ValueError(f"No JSON found in Claude response: {raw[:300]!r}")
        model_output = json.loads(raw[start_idx:end_idx])

        # Reconcile against the checklist that was actually in force. The model
        # is evidence; the checklist is truth. A requirement it failed to return
        # becomes not_assessed rather than silently disappearing, counts are
        # recomputed, and the determination is derived — so the report describes
        # the customer's checklist no matter what came back.
        report_data = normalize_report(model_output, profile_data)

        # ── Step 3: Save report ────────────────────────────────────────────
        # Overwrite for the same reason as the transcript: reports.call_id is
        # UNIQUE, and a re-graded call must replace its report, not collide
        # with it. Manager overrides are deliberately preserved — a re-run is
        # a re-grade, not a reason to discard a human's decisions.
        pass_fail = report_data.get("final_determination", "")
        verdict = report_data.get("verdict")
        report = db.query(Report).filter_by(call_id=call_id).first()
        if report:
            report.report_json = report_data
            report.pass_fail_status = pass_fail
            report.verdict = verdict
            report.created_at = datetime.now(timezone.utc)
        else:
            db.add(Report(
                call_id=call_id,
                report_json=report_data,
                pass_fail_status=pass_fail,
                verdict=verdict,
                created_at=datetime.now(timezone.utc),
            ))
        _set_status(db, call, "complete")
        _after_usage(db, call)

        logger.info("Pipeline [%s]: complete — %s", call_id, pass_fail)

    except Exception as exc:
        logger.exception("Pipeline [%s]: failed", call_id)
        if call is not None:
            try:
                db.rollback()
                _set_status(db, call, "error", str(exc))
                # A failed grade still consumed transcription minutes, and that
                # row survived the rollback. Evaluate the warning thresholds
                # here too, or a customer can cross their allowance entirely on
                # calls that errored and never be told.
                _after_usage(db, call)
            except Exception:
                logger.exception("Pipeline [%s]: could not write error status", call_id)
    finally:
        db.close()
        with _lock:
            _running.discard(call_id)


def _after_usage(db, call) -> None:
    """Evaluate usage warning thresholds once usage has been committed."""
    try:
        org = call.org
        if org is None:
            return
        fired = usage.thresholds_crossed(db, org)
        if fired:
            db.commit()
            logger.warning("Org %s crossed the %s%% usage threshold",
                           org.id, fired[0])
    except Exception:
        # Never let a notification concern fail a pipeline run that otherwise
        # succeeded — the report is the product, this is a courtesy.
        logger.exception("Could not evaluate usage thresholds for call %s", call.id)
        db.rollback()


def spawn(call_id: str, file_path: str,
          assemblyai_key: str, anthropic_key: str) -> None:
    """Queue a call for analysis on the shared worker pool.

    Returns as soon as the work is queued. Keyword-compatible with the old
    thread-per-call version — the upload route and six tests call it by keyword.
    """
    _get_pool().submit(
        run_pipeline, call_id, file_path, assemblyai_key, anthropic_key
    )


def recover_stranded(assemblyai_key: str, anthropic_key: str) -> int:
    """Re-queue calls whose worker died before reaching a terminal state.

    Runs at startup and then on a timer (see `start_recovery_sweeper`). Safe to
    run repeatedly: re-running a call rewrites its transcript and report, and
    the usage ledger's idempotency key stops it being billed twice. The known
    cost is at the vendor — a re-run pays AssemblyAI a second time while the
    customer is charged once, which is the right way round.

    Returns the number of calls re-queued.
    """
    from models import Call

    db = get_db()
    try:
        cutoff = datetime.now(timezone.utc) - STRANDED_AFTER
        stranded = (
            db.query(Call)
            .filter(Call.status.in_(ACTIVE_STATUSES), Call.upload_date < cutoff)
            .all()
        )
        # Only reachable once this runs on a timer: at startup no worker of ours
        # is alive, so every stale row was genuinely abandoned. On a timer, a
        # long-but-healthy transcription is stale by the clock while its worker
        # is alive and polling — re-queuing it would run the same call twice
        # against both vendors. `_running` is in-process only, which is exactly
        # the right scope: it answers "is a worker *here* on this call", and a
        # row stale because another process died has no entry.
        with _lock:
            in_flight = set(_running)
        if in_flight:
            skipped = [c.id for c in stranded if c.id in in_flight]
            if skipped:
                logger.info("Recovery: %d call(s) still in flight here, leaving them",
                            len(skipped))
            stranded = [c for c in stranded if c.id not in in_flight]

        queued = 0
        for call in stranded:
            if not call.audio_file_url:
                # Nothing to re-run — the upload never landed. Close it out so
                # it stops showing as in-flight forever.
                call.status = "error"
                call.error_message = "Upload did not complete."
                continue
            spawn(
                call_id=call.id,
                file_path=call.audio_file_url,
                assemblyai_key=assemblyai_key,
                anthropic_key=anthropic_key,
            )
            queued += 1
        db.commit()
        if stranded:
            logger.warning(
                "Recovery: %d stranded call(s), %d re-queued",
                len(stranded), queued,
            )
        return queued
    finally:
        db.close()


# How often the sweeper looks for abandoned work. Recovery used to run only at
# startup, which meant a call abandoned by a crashed worker sat in
# `transcribing` until the next deploy — already paid for, invisible to the
# customer except as a row that never finishes.
RECOVERY_INTERVAL = 600

_sweeper: threading.Thread | None = None
_sweeper_lock = threading.Lock()
_sweeper_stop = threading.Event()


def start_recovery_sweeper(assemblyai_key: str, anthropic_key: str,
                           interval: int = RECOVERY_INTERVAL) -> bool:
    """Run recovery on a timer for the life of the process.

    Started from `serve.py`, never from `create_app()` — building an app must
    stay free of side effects, or importing the package starts a thread that
    writes to whatever database the ambient config points at. That is how the
    test suite once ended up connected to production.

    Returns True if this call started the sweeper.
    """
    global _sweeper
    with _sweeper_lock:
        if _sweeper is not None and _sweeper.is_alive():
            return False

        _sweeper_stop.clear()

        def loop():
            # `Event.wait` rather than `time.sleep`: a stop signal takes effect
            # at once instead of after the rest of a ten-minute nap.
            while not _sweeper_stop.wait(interval):
                try:
                    recover_stranded(assemblyai_key, anthropic_key)
                except Exception:
                    # A sweep that raises must not end the sweeper — a transient
                    # DB blip would otherwise silently disable recovery for the
                    # life of the process, which is the failure it exists to fix.
                    logger.exception("Recovery sweep failed; will retry")

        _sweeper = threading.Thread(target=loop, name="pipeline-recovery",
                                    daemon=True)
        _sweeper.start()
        logger.info("Recovery sweeper started (every %ds)", interval)
        return True


def stop_recovery_sweeper(timeout: float = 5.0) -> None:
    """Stop the sweeper and wait for it to finish the current sweep."""
    global _sweeper
    _sweeper_stop.set()
    with _sweeper_lock:
        thread, _sweeper = _sweeper, None
    if thread is not None:
        thread.join(timeout)
