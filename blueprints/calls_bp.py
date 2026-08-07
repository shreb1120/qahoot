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
    g,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    url_for,
)
from werkzeug.utils import secure_filename

import pipeline as pipeline_module
from auth import login_required, org_required
from models import Agent, Call, Report

calls_bp = Blueprint("calls", __name__, url_prefix="/calls")

ALLOWED_EXTENSIONS = {
    "mp3", "mp4", "wav", "m4a", "ogg", "webm",
    "flac", "aac", "wma", "mov", "avi", "mkv",
}


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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

@calls_bp.get("/upload")
@org_required
def upload_form():
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
    return render_template("calls/upload.html", has_profile=bool(profile), agents=agents)


@calls_bp.post("/upload")
@org_required
def upload():
    if "file" not in request.files:
        return render_template("calls/upload.html", has_profile=True, agents=[],
                               error="No file selected."), 400

    f = request.files["file"]
    if not f.filename:
        return render_template("calls/upload.html", has_profile=True, agents=[],
                               error="No file selected."), 400

    if not _allowed(f.filename):
        return render_template("calls/upload.html", has_profile=True, agents=[],
                               error="Unsupported file type. Upload an audio or video file."), 400

    from models import ComplianceProfile
    profile = (
        g.db.query(ComplianceProfile)
        .filter_by(org_id=g.org.id, is_active=True)
        .first()
    )
    if not profile:
        return render_template("calls/upload.html", has_profile=False, agents=[],
                               error="Set up your compliance profile before uploading."), 400

    # Read metadata from form
    agent_id = request.form.get("agent_id") or None
    alv_id = request.form.get("alv_id", "").strip() or None
    call_date_str = request.form.get("call_date", "").strip()
    client_phone = request.form.get("client_phone", "").strip() or None

    call_date = None
    if call_date_str:
        from datetime import date
        try:
            call_date = date.fromisoformat(call_date_str)
        except ValueError:
            pass

    # Validate agent belongs to this org
    if agent_id:
        valid_agent = g.db.query(Agent).filter_by(id=agent_id, org_id=g.org.id).first()
        if not valid_agent:
            agent_id = None

    # Save file to disk
    safe_name = secure_filename(f.filename)
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
        alv_id=alv_id,
        call_date=call_date,
        client_phone=client_phone,
        filename=safe_name,
        status="pending",
    )
    g.db.add(call)
    g.db.flush()  # populate call.id

    file_path = os.path.join(upload_dir, f"{call.id}_{safe_name}")
    f.save(file_path)

    call.audio_file_url = file_path
    g.db.commit()

    # Spawn background pipeline
    pipeline_module.spawn(
        call_id=call.id,
        file_path=file_path,
        assemblyai_key=current_app.config["ASSEMBLYAI_API_KEY"],
        anthropic_key=current_app.config["ANTHROPIC_API_KEY"],
    )

    return redirect(url_for("calls.status", call_id=call.id))


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
    return render_template("calls/report.html", call=call, report=call.report)


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
    alv_number = (call.alv_id or "").strip()
    if alv_number.upper().startswith("ALV-"):
        alv_number = alv_number[4:]

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
        agent_name, alv_number, call_date_str,
        misguidance, risk_disclosure,
    )

    safe_agent = re.sub(r"[^A-Za-z0-9_-]+", "_", agent_name).strip("_") or "agent"
    filename = f"WrittenWarning_{safe_agent}_ALV-{alv_number or call_id}.docx"

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

    if agent_id:
        q = q.filter(Call.agent_id == agent_id)
    if status_filter:
        q = q.filter(Call.status == status_filter)
    if date_from:
        try:
            q = q.filter(Call.call_date >= date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(Call.call_date <= date.fromisoformat(date_to))
        except ValueError:
            pass

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

    # IDs of in-progress calls for polling
    active_ids = [c.id for c in calls if c.status in ("pending", "transcribing", "analyzing")]

    return render_template(
        "calls/history.html",
        calls=calls,
        agents=agents,
        active_ids_json=json.dumps(active_ids),
        filters={
            "agent_id": agent_id,
            "status": status_filter,
            "result": result_filter,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
