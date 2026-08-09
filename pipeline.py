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
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import anthropic
import assemblyai as aai

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


MODEL = "claude-opus-5"
# Generous because max_tokens bounds the whole response. Reports run ~1,650
# tokens; the headroom is what stops a long checklist truncating one.
MAX_TOKENS = 16000


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
        config = aai.TranscriptionConfig(speaker_labels=True)
        transcriber = aai.Transcriber()
        result = transcriber.transcribe(file_path, config=config)

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
        db.commit()

        logger.info("Pipeline [%s]: transcription done (%d chars)",
                    call_id, len(transcript_text))

        # ── Step 2: Analyze with Claude ────────────────────────────────────
        _set_status(db, call, "analyzing")
        logger.info("Pipeline [%s]: running Claude analysis", call_id)

        client = anthropic.Anthropic(api_key=anthropic_key)
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

        logger.info("Pipeline [%s]: complete — %s", call_id, pass_fail)

    except Exception as exc:
        logger.exception("Pipeline [%s]: failed", call_id)
        if call is not None:
            try:
                db.rollback()
                _set_status(db, call, "error", str(exc))
            except Exception:
                logger.exception("Pipeline [%s]: could not write error status", call_id)
    finally:
        db.close()
        with _lock:
            _running.discard(call_id)


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

    Called once at startup. Safe to run repeatedly: re-running a call rewrites
    its transcript and report, and the usage ledger's idempotency key stops it
    being billed twice. The known cost is at the vendor — a re-run pays
    AssemblyAI a second time while the customer is charged once, which is the
    right way round.

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
                "Startup recovery: %d stranded call(s), %d re-queued",
                len(stranded), queued,
            )
        return queued
    finally:
        db.close()
