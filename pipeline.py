"""
Background pipeline: AssemblyAI transcription → Claude analysis → save to DB.

Runs in a daemon thread outside Flask's request context.  Uses get_db()
directly (which creates a new session) rather than Flask's g.db.
"""

import json
import logging
import threading
from datetime import datetime, timezone

import anthropic
import assemblyai as aai

from db import get_db
from prompt_builder import build_system_prompt, build_user_message

logger = logging.getLogger(__name__)

# Track in-flight pipeline calls to prevent duplicate runs
_running: set = set()
_lock = threading.Lock()


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

        transcript_row = Transcript(
            call_id=call_id,
            raw_transcript_json={
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
            },
        )
        db.add(transcript_row)

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

        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = response.content[0].text
        start_idx = raw.find("{")
        end_idx = raw.rfind("}") + 1
        if start_idx == -1 or end_idx <= start_idx:
            raise ValueError(f"No JSON found in Claude response: {raw[:300]!r}")
        report_data = json.loads(raw[start_idx:end_idx])

        # ── Step 3: Save report ────────────────────────────────────────────
        pass_fail = report_data.get("final_determination", "")
        db.add(Report(
            call_id=call_id,
            report_json=report_data,
            pass_fail_status=pass_fail,
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
    """Spawn a daemon thread to run the pipeline."""
    t = threading.Thread(
        target=run_pipeline,
        args=(call_id, file_path, assemblyai_key, anthropic_key),
        daemon=True,
        name=f"pipeline-{call_id[:8]}",
    )
    t.start()
