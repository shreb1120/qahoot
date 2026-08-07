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
  GET /calls/                   — list of all org's calls
"""

import os

from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.utils import secure_filename

import pipeline as pipeline_module
from auth import login_required, org_required
from models import Call

calls_bp = Blueprint("calls", __name__, url_prefix="/calls")

ALLOWED_EXTENSIONS = {
    "mp3", "mp4", "wav", "m4a", "ogg", "webm",
    "flac", "aac", "wma", "mov", "avi", "mkv",
}


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _org_call_or_404(call_id: str) -> Call:
    """Return the Call only if it belongs to the current org."""
    call = g.db.query(Call).filter_by(id=call_id, org_id=g.org.id).first()
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
    return render_template("calls/upload.html", has_profile=bool(profile))


@calls_bp.post("/upload")
@org_required
def upload():
    if "file" not in request.files:
        return render_template("calls/upload.html", has_profile=True,
                               error="No file selected."), 400

    f = request.files["file"]
    if not f.filename:
        return render_template("calls/upload.html", has_profile=True,
                               error="No file selected."), 400

    if not _allowed(f.filename):
        return render_template("calls/upload.html", has_profile=True,
                               error="Unsupported file type. Upload an audio or video file."), 400

    from models import ComplianceProfile
    profile = (
        g.db.query(ComplianceProfile)
        .filter_by(org_id=g.org.id, is_active=True)
        .first()
    )
    if not profile:
        return render_template("calls/upload.html", has_profile=False,
                               error="Set up your compliance profile before uploading."), 400

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


# ── Report ────────────────────────────────────────────────────────────────────

@calls_bp.get("/<call_id>/report")
@org_required
def report(call_id: str):
    call = _org_call_or_404(call_id)
    if call.status != "complete" or not call.report:
        return redirect(url_for("calls.status", call_id=call_id))
    return render_template("calls/report.html", call=call, report=call.report)


# ── History ───────────────────────────────────────────────────────────────────

@calls_bp.get("/")
@org_required
def history():
    calls = (
        g.db.query(Call)
        .filter_by(org_id=g.org.id)
        .order_by(Call.upload_date.desc())
        .all()
    )
    return render_template("calls/history.html", calls=calls)
