"""
Calls blueprint — upload, async processing, status polling, report view,
and call history.

Upload flow:
  POST /calls/upload
    → save file to disk
    → create Call record (status=pending)
    → spawn background pipeline thread
    → redirect to /calls/<id>/status

Status polling:
  GET /calls/<id>/status        — HTML page with JS poller
  GET /calls/<id>/status.json   — JSON endpoint polled by the page

Report:
  GET /calls/<id>/report        — rendered report

History:
  GET /calls/                   — list of all org's calls (with filters)

Extra routes:
  GET  /calls/active.json            — in-progress calls for org polling
  POST /calls/<id>/writeup           — generate + download .docx
  GET  /calls/<id>/report.pdf        — PDF download (?fails_only=1)
  POST /calls/<id>/override          — save manager item override
"""

import io
import json
import os
import re

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from sqlalchemy import Date, cast, func

import pipeline as pipeline_module
import ratelimit
import usage
from auth import login_required, org_required
from models import Agent, Call, Report

calls_bp = Blueprint("calls", __name__, url_prefix="/calls")

ALLOWED_EXTENSIONS = {
    "mp3", "mp4", "wav", "m4a", "ogg", "webm",
    "flac", "aac", "wma", "mov", "avi", "mkv",
}

# Rows rendered per history page.
PAGE_SIZE = 50


@calls_bp.app_template_filter("ts_seconds")
def ts_seconds(value):
    """'8:12' or '1:02:33' -> seconds. Returns None when unparseable so the
    template can drop the marker rather than place it at zero."""
    if not value or not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if not all(p.strip().isdigit() for p in parts):
        return None
    parts = [int(p) for p in parts]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _report_view_model(call):
    """Everything the timeline needs, computed once from real data.

    The call-intelligence canon is built on one thing: the call as a timeline
    you can aim at. Every input here already exists — utterance speaker/start/end
    from AssemblyAI, and a timestamp on every graded item and auto-fail phrase.
    Nothing is synthesised; a call with no usable timing renders without a
    timeline rather than with a decorative one.
    """
    raw = (call.transcript.raw_transcript_json or {}) if call.transcript else {}
    utterances = raw.get("utterances") or []

    # Duration: prefer the transcribed length, fall back to the last utterance.
    duration = call.duration or 0
    if not duration and utterances:
        duration = int(max(u.get("end", 0) for u in utterances) / 1000) or 0

    # Stable speaker slots in order of first appearance, so colour means the
    # same thing on the timeline and in the transcript.
    slots, order = {}, []
    for u in utterances:
        spk = u.get("speaker") or "?"
        if spk not in slots:
            slots[spk] = "a" if not order else ("b" if len(order) == 1 else "n")
            order.append(spk)

    def pct(seconds):
        if not duration:
            return None
        return max(0.0, min(100.0, seconds / duration * 100))

    bands, lines = [], []
    for u in utterances:
        start_s, end_s = u.get("start", 0) / 1000, u.get("end", 0) / 1000
        left, right = pct(start_s), pct(end_s)
        slot = slots.get(u.get("speaker") or "?", "n")
        if left is not None and right is not None and right > left:
            bands.append({"left": round(left, 3), "width": round(max(right - left, 0.15), 3), "slot": slot})
        m, sec = int(start_s // 60), int(start_s % 60)
        lines.append({
            "start_ms": u.get("start", 0), "end_ms": u.get("end", 0),
            "ts": f"{m}:{sec:02d}", "slot": slot,
            "speaker": f"Speaker {u.get('speaker') or '?'}",
            "text": u.get("text", ""),
        })

    # Talk share is a real QA signal and falls straight out of the same data.
    talk = {}
    for u in utterances:
        slot = slots.get(u.get("speaker") or "?", "n")
        talk[slot] = talk.get(slot, 0) + max(0, u.get("end", 0) - u.get("start", 0))
    total_talk = sum(talk.values()) or 1
    talk_share = {k: round(v / total_talk * 100) for k, v in talk.items()}

    # Markers: one per graded item and per auto-fail phrase that carries a
    # parseable timestamp. Missing items and auto-fails are what a reviewer is
    # actually hunting for, so they read strongest.
    report_json = call.report.report_json if call.report else {}
    markers = []
    for section in report_json.get("sections", []) or []:
        for item in section.get("items", []) or []:
            secs = ts_seconds(item.get("timestamp"))
            if secs is None or not duration:
                continue
            covered = item.get("status") == "covered"
            markers.append({
                "pct": round(pct(secs), 3), "ts": item.get("timestamp"),
                "kind": "ok" if covered else "miss",
                "label": ("Covered" if covered else "Missing") + f" — {item.get('name', '')}",
            })
    af = report_json.get("auto_fail_phrases", {}) or {}
    for phrase in (af.get("phrases") or []):
        secs = ts_seconds(phrase.get("timestamp"))
        if secs is None or not duration:
            continue
        markers.append({
            "pct": round(pct(secs), 3), "ts": phrase.get("timestamp"),
            "kind": "crit", "label": f"Auto-fail — {phrase.get('phrase', '')}",
        })
    markers.sort(key=lambda m: m["pct"])

    return {
        "duration": duration,
        "duration_label": f"{duration // 60}:{duration % 60:02d}" if duration else "--:--",
        "bands": bands, "lines": lines, "markers": markers,
        # Only legend the kinds actually on the timeline. A missing item usually
        # has no timestamp — nothing happened to point at — so advertising a
        # "missing" marker that can never appear would be a lie in the legend.
        "marker_kinds": sorted({m["kind"] for m in markers}),
        "talk_share": talk_share, "speaker_count": len(order),
        "has_timeline": bool(duration and bands),
    }


def _org_call_or_404(call_id: str) -> Call:
    """Return the Call only if it belongs to the current org."""
    from sqlalchemy.orm import joinedload
    call = (
        g.db.query(Call)
        .options(joinedload(Call.report), joinedload(Call.agent))
        .filter_by(id=call_id, org_id=g.org.id)
        .first()
    )
    if not call:
        abort(404)
    return call


# ── Upload ────────────────────────────────────────────────────────────────────

def _upload_context(**overrides):
    """Everything calls/upload.html needs, so an error re-render is identical
    to a fresh one. Rebuilding this on the error path is what keeps the agent
    dropdown populated and the user's typed values intact."""
    from datetime import date as _date
    from models import ComplianceProfile

    profile = (
        g.db.query(ComplianceProfile)
        .filter_by(org_id=g.org.id, is_active=True)
        .first()
    )
    agents = (
        g.db.query(Agent)
        .filter_by(org_id=g.org.id)
        .order_by(Agent.name)
        .all()
    )
    max_bytes = current_app.config.get("MAX_CONTENT_LENGTH") or 0

    # What this call will actually be graded against. A reviewer about to spend
    # an API call on a 40-minute recording should be able to see the checklist
    # is the one they expect before committing to it.
    profile_summary = None
    if profile:
        sections = (profile.script_sections_json or {}).get("sections", []) or []
        phrases = (profile.script_sections_json or {}).get("auto_fail_phrases", []) or []
        # NOT "items": Jinja resolves obj.items to dict.items (the method) before
        # the key, which rendered "<built-in method items of dict object at 0x…>"
        # straight onto the upload page.
        profile_summary = {
            "name": profile.name,
            "sections": len(sections),
            "item_count": sum(len(sec.get("items", []) or []) for sec in sections),
            "phrases": len(phrases),
        }

    ctx = {
        "has_profile": bool(profile),
        "profile_summary": profile_summary,
        "agents": agents,
        "form": {},
        "error": None,
        "today": _date.today().isoformat(),
        "max_upload_bytes": max_bytes,
        "max_upload_mb": max_bytes // (1024 * 1024),
        "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
    }
    ctx.update(overrides)
    return ctx


def _submitted_upload_form():
    return {
        "agent_id": request.form.get("agent_id", "").strip(),
        "internal_id": request.form.get("internal_id", "").strip(),
        "call_date": request.form.get("call_date", "").strip(),
        "client_phone": request.form.get("client_phone", "").strip(),
    }


def _upload_error(message):
    return render_template(
        "calls/upload.html",
        **_upload_context(error=message, form=_submitted_upload_form()),
    ), 400


@calls_bp.get("/upload")
@org_required
def upload_form():
    return render_template("calls/upload.html", **_upload_context())


@calls_bp.post("/upload")
@org_required
def upload():
    from datetime import date

    f = request.files.get("file")
    if f is None or not f.filename:
        return _upload_error("Choose a call recording to upload.")

    if not _allowed(f.filename):
        return _upload_error(
            "That file type is not supported. Upload an audio or video "
            "recording (MP3, MP4, WAV, M4A, OGG, WEBM, and similar)."
        )

    from models import ComplianceProfile
    profile = (
        g.db.query(ComplianceProfile)
        .filter_by(org_id=g.org.id, is_active=True)
        .first()
    )
    if not profile:
        return render_template("calls/upload.html", **_upload_context(
            has_profile=False,
            error="Set up your compliance profile before uploading.",
            form=_submitted_upload_form(),
        )), 400

    # ── Two gates, doing different jobs ──────────────────────────────────
    # Entitlement is a question about payment state: a trial that has run out
    # or a subscription Stripe says is dead. A paid plan over its allowance is
    # deliberately *not* blocked — that is what soft overage means.
    try:
        usage.check_can_upload(g.db, g.org)
    except usage.Blocked as blocked:
        return render_template("calls/upload.html", **_upload_context(
            error=blocked.message,
            form=_submitted_upload_form(),
        )), 402 if blocked.reason == "trial_exhausted" else 403

    # Abuse is a different question, and soft overage gives no protection
    # against it. Checked before any row is written or file saved, so being
    # refused costs nothing.
    try:
        ratelimit.check_upload(g.db, g.org, g.user)
        g.db.commit()
    except ratelimit.RateLimited as limited:
        g.db.commit()          # keep the counter increment that tripped it
        return render_template("calls/upload.html", **_upload_context(
            error=limited.message,
            form=_submitted_upload_form(),
        )), 429

    # Read metadata from form
    agent_id = request.form.get("agent_id", "").strip() or None
    internal_id = request.form.get("internal_id", "").strip() or None
    call_date_str = request.form.get("call_date", "").strip()
    client_phone = request.form.get("client_phone", "").strip() or None

    # Every metadata field is optional — a reviewer can upload now and attribute
    # later from the report page. Only length and validity are enforced, because
    # those produce a raw DataError rather than a message the user can act on.
    if internal_id and len(internal_id) > 50:
        return _upload_error("That internal ID is too long (50 characters maximum).")
    if client_phone and len(client_phone) > 30:
        return _upload_error("That phone number is too long (30 characters maximum).")

    call_date = None
    if call_date_str:
        try:
            call_date = date.fromisoformat(call_date_str)
        except ValueError:
            return _upload_error("That call date is not a valid date.")
        if call_date > date.today():
            return _upload_error("A call cannot have happened in the future.")

    # Only when an agent was actually submitted. This is a tenant-isolation
    # control, not a required-field check.
    if agent_id:
        valid_agent = g.db.query(Agent).filter_by(id=agent_id, org_id=g.org.id).first()
        if not valid_agent:
            return _upload_error("That agent is no longer available. Pick another.")

    # Save file to disk. secure_filename() strips non-ASCII, so a recording named
    # entirely in a non-Latin script can come back empty — fall back to the
    # extension we already validated rather than writing a nameless file.
    safe_name = secure_filename(f.filename)
    if not safe_name or safe_name.startswith("."):
        safe_name = f"recording.{f.filename.rsplit('.', 1)[1].lower()}"

    upload_dir = os.path.join(
        current_app.config["UPLOAD_FOLDER"], g.org.id
    )
    os.makedirs(upload_dir, exist_ok=True)

    # Create Call record first so we have the ID for the filename
    call = Call(
        org_id=g.org.id,
        compliance_profile_id=profile.id,
        uploaded_by_user_id=g.user.id,
        agent_id=agent_id,
        internal_id=internal_id,
        call_date=call_date,
        client_phone=client_phone,
        filename=safe_name,
        status="pending",
    )
    g.db.add(call)
    g.db.flush()  # populate call.id

    file_path = os.path.join(upload_dir, f"{call.id}_{safe_name}")
    try:
        f.save(file_path)
    except OSError:
        current_app.logger.exception("Upload failed to write %s", file_path)
        g.db.rollback()
        return _upload_error(
            "We could not save that recording. Try again, and contact support "
            "if it keeps happening."
        )

    if os.path.getsize(file_path) == 0:
        os.remove(file_path)
        g.db.rollback()
        return _upload_error("That file is empty. Check the recording and try again.")

    call.audio_file_url = file_path
    g.db.commit()

    # Spawn background pipeline. If it cannot start, the call would otherwise sit
    # in "pending" forever with nothing working on it — mark it failed instead so
    # history shows the truth.
    try:
        pipeline_module.spawn(
            call_id=call.id,
            file_path=file_path,
            assemblyai_key=current_app.config["ASSEMBLYAI_API_KEY"],
            anthropic_key=current_app.config["ANTHROPIC_API_KEY"],
        )
    except Exception:
        current_app.logger.exception("Pipeline failed to start for call %s", call.id)
        call.status = "error"
        call.error_message = "Analysis could not be started. Re-upload the call to retry."
        g.db.commit()

    return redirect(url_for("calls.history"))


# ── Status ────────────────────────────────────────────────────────────────────

@calls_bp.get("/<call_id>/status")
@org_required
def status(call_id: str):
    call = _org_call_or_404(call_id)
    if call.status == "complete":
        return redirect(url_for("calls.report", call_id=call_id))
    return render_template("calls/processing.html", call=call)


@calls_bp.get("/<call_id>/status.json")
@org_required
def status_json(call_id: str):
    call = _org_call_or_404(call_id)
    return jsonify({
        "status": call.status,
        "error": call.error_message,
    })


# ── Audio file ────────────────────────────────────────────────────────────────

@calls_bp.get("/<call_id>/audio")
@org_required
def audio(call_id: str):
    import mimetypes
    call = _org_call_or_404(call_id)
    if not call.audio_file_url or not os.path.exists(call.audio_file_url):
        abort(404)
    mime = mimetypes.guess_type(call.audio_file_url)[0] or "audio/mpeg"
    return send_file(call.audio_file_url, mimetype=mime, conditional=True)


# ── Active calls (for history page polling) ───────────────────────────────────

@calls_bp.get("/active.json")
@org_required
def active_json():
    active = (
        g.db.query(Call)
        .filter(
            Call.org_id == g.org.id,
            Call.status.in_(["pending", "transcribing", "analyzing"]),
        )
        .all()
    )
    return jsonify([
        {"id": c.id, "status": c.status, "filename": c.filename}
        for c in active
    ])


# ── Report ────────────────────────────────────────────────────────────────────

@calls_bp.get("/<call_id>/report")
@org_required
def report(call_id: str):
    call = _org_call_or_404(call_id)
    if call.status != "complete" or not call.report:
        return redirect(url_for("calls.status", call_id=call_id))
    agents = (
        g.db.query(Agent).filter_by(org_id=g.org.id).order_by(Agent.name).all()
    )
    return render_template(
        "calls/report.html", call=call, report=call.report,
        vm=_report_view_model(call), agents=agents,
    )


# ── Manager override ──────────────────────────────────────────────────────────

@calls_bp.post("/<call_id>/override")
@org_required
def override(call_id: str):
    call = _org_call_or_404(call_id)
    if not call.report:
        abort(404)

    data = request.get_json(silent=True) or {}
    section_key = data.get("section_key", "")
    item_name = data.get("item_name", "")
    override_status = data.get("status", "")

    if not section_key or not item_name or override_status not in ("approved", "failed", ""):
        abort(400)

    from sqlalchemy.orm.attributes import flag_modified
    rpt = call.report
    overrides = dict(rpt.overrides_json or {})
    composite_key = f"{section_key}::{item_name}"

    if override_status == "":
        overrides.pop(composite_key, None)
    else:
        overrides[composite_key] = override_status

    rpt.overrides_json = overrides
    flag_modified(rpt, "overrides_json")
    g.db.commit()
    return jsonify({"ok": True})


@calls_bp.post("/<call_id>/agent")
@org_required
def assign_agent(call_id: str):
    """Attribute a call after the fact.

    Metadata is optional at upload, so a call can arrive unattributed and be
    assigned once someone knows whose it was. Same permission tier as an
    override — reviewers already do that work.
    """
    call = _org_call_or_404(call_id)          # 404s cross-org before reading the body
    agent_id = request.form.get("agent_id", "").strip()

    if agent_id:
        agent = g.db.query(Agent).filter_by(id=agent_id, org_id=g.org.id).first()
        if not agent:
            flash("That agent is no longer available.", "error")
            return redirect(url_for("calls.report", call_id=call_id))
        call.agent_id = agent.id
        flash(f"Call assigned to {agent.name}.", "success")
    else:
        call.agent_id = None
        flash("Call is now unassigned.", "success")

    g.db.commit()
    return redirect(url_for("calls.report", call_id=call_id))


# ── Write-up ──────────────────────────────────────────────────────────────────

@calls_bp.post("/<call_id>/writeup")
@org_required
def writeup(call_id: str):
    call = _org_call_or_404(call_id)
    if not call.report or not call.transcript:
        abort(404)

    import anthropic as anthropic_lib
    import writeup as writeup_module

    client = anthropic_lib.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])

    agent_name = (call.agent.name if call.agent else "") or "Agent"
    internal_id = (call.internal_id or "").strip()

    call_date_str = ""
    if call.call_date:
        call_date_str = call.call_date.strftime("%B %d, %Y")

    # Build transcript text from raw JSON (AssemblyAI utterances format)
    raw = call.transcript.raw_transcript_json
    utterances = raw.get("utterances") or raw.get("words") or []
    transcript_text = ""
    if utterances and isinstance(utterances[0], dict) and "text" in utterances[0]:
        lines = []
        for u in utterances:
            ts = _fmt_ts(u.get("start", 0))
            speaker = u.get("speaker", "")
            text = u.get("text", "")
            lines.append(f"[{ts}] {speaker}: {text}" if speaker else f"[{ts}] {text}")
        transcript_text = "\n".join(lines)
    else:
        transcript_text = str(raw)

    report_data = call.report.report_json

    misguidance, risk_disclosure = writeup_module.generate_finding_bodies(
        client, agent_name, transcript_text, report_data
    )

    buf = writeup_module.build_writeup_docx(
        agent_name, internal_id, call_date_str,
        misguidance, risk_disclosure,
    )

    # Both parts sanitised: this string goes straight into a Content-Disposition
    # header, and internal_id is user-typed.
    safe_agent = re.sub(r"[^A-Za-z0-9_-]+", "_", agent_name).strip("_") or "agent"
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", internal_id).strip("_") or call_id
    filename = f"WrittenWarning_{safe_agent}_{safe_id}.docx"

    return Response(
        buf.read(),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _fmt_ts(ms: int) -> str:
    secs = ms // 1000
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ── PDF report ────────────────────────────────────────────────────────────────

@calls_bp.get("/<call_id>/report.pdf")
@org_required
def report_pdf(call_id: str):
    call = _org_call_or_404(call_id)
    if not call.report:
        abort(404)

    fails_only = request.args.get("fails_only") == "1"
    html = render_template(
        "calls/report_pdf.html",
        call=call,
        report=call.report,
        fails_only=fails_only,
    )

    from xhtml2pdf import pisa
    buf = io.BytesIO()
    pisa.CreatePDF(html, dest=buf)
    buf.seek(0)

    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", call.filename).strip("_")
    suffix = "_fails_only" if fails_only else ""
    pdf_filename = f"report_{safe_name}{suffix}.pdf"

    return Response(
        buf.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{pdf_filename}"'},
    )


# ── History ───────────────────────────────────────────────────────────────────

@calls_bp.get("/")
@org_required
def history():
    from sqlalchemy.orm import joinedload
    from datetime import date

    q = (
        g.db.query(Call)
        .options(joinedload(Call.report), joinedload(Call.agent))
        .filter(Call.org_id == g.org.id)
    )

    # Filters
    agent_id = request.args.get("agent_id", "").strip()
    status_filter = request.args.get("status", "").strip()
    result_filter = request.args.get("result", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    phone_filter = request.args.get("phone", "").strip()

    if agent_id == "unassigned":
        q = q.filter(Call.agent_id.is_(None))
    elif agent_id:
        q = q.filter(Call.agent_id == agent_id)
    if status_filter:
        q = q.filter(Call.status == status_filter)
    # History displays upload_date when call_date is missing, so the filter has to
    # agree. A bare `Call.call_date >= x` drops every NULL row under SQL
    # three-valued logic, which silently hid undated calls once dates became
    # optional. COALESCE keeps filter and display consistent.
    effective_date = func.coalesce(Call.call_date, cast(Call.upload_date, Date))
    if date_from:
        try:
            q = q.filter(effective_date >= date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(effective_date <= date.fromisoformat(date_to))
        except ValueError:
            pass
    if phone_filter:
        q = q.filter(Call.client_phone.ilike(f"%{phone_filter}%"))

    calls = q.order_by(Call.upload_date.desc()).all()

    # Apply result filter post-query (based on pass_fail_status string)
    if result_filter:
        if result_filter == "pass":
            calls = [c for c in calls if c.report and c.report.pass_fail_status
                     and "PASS" in c.report.pass_fail_status and "FAIL" not in c.report.pass_fail_status]
        elif result_filter == "fail":
            calls = [c for c in calls if c.report and c.report.pass_fail_status
                     and "FAIL" in c.report.pass_fail_status and "CRITICAL" not in c.report.pass_fail_status]
        elif result_filter == "critical":
            calls = [c for c in calls if c.report and c.report.pass_fail_status
                     and "CRITICAL" in c.report.pass_fail_status]

    agents = (
        g.db.query(Agent)
        .filter_by(org_id=g.org.id)
        .order_by(Agent.name)
        .all()
    )

    # Totals are counted across the whole filtered set, then a single page is
    # rendered. An org with thousands of calls would otherwise put every row in
    # the DOM — but a compliance tool must still report honest totals, so the
    # summary counts below are not page-scoped.
    total_calls = len(calls)
    summary = {"passed": 0, "failed": 0, "critical": 0}
    for c in calls:
        det = (c.report.pass_fail_status if c.report else "") or ""
        if "CRITICAL" in det:
            summary["critical"] += 1
        elif "PASS" in det and "FAIL" not in det:
            summary["passed"] += 1
        elif "FAIL" in det:
            summary["failed"] += 1

    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    total_pages = max(1, (total_calls + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    start = (page - 1) * PAGE_SIZE
    page_calls = calls[start:start + PAGE_SIZE]

    # Preserve the active filters when moving between pages.
    page_args = {k: v for k, v in request.args.items() if k != "page" and v}

    # IDs of in-progress calls for polling
    active_ids = [c.id for c in page_calls if c.status in ("pending", "transcribing", "analyzing")]

    return render_template(
        "calls/history.html",
        calls=page_calls,
        agents=agents,
        active_ids_json=json.dumps(active_ids),
        summary=summary,
        total_calls=total_calls,
        page=page,
        total_pages=total_pages,
        page_start=start + 1 if page_calls else 0,
        page_end=start + len(page_calls),
        page_args=page_args,
        filters={
            "agent_id": agent_id,
            "status": status_filter,
            "result": result_filter,
            "date_from": date_from,
            "date_to": date_to,
            "phone": phone_filter,
        },
    )
