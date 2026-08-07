import os
import re
import json
import uuid
import time
import logging
import ipaddress
import tempfile
import threading
import sqlite3
from collections import deque
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, abort, send_file
from dotenv import load_dotenv
import assemblyai as aai
import anthropic
from qa_prompt import SYSTEM_PROMPT, build_analysis_prompt
from writeup import render_writeup_for_analysis

env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(env_path, override=True)

# Fallback: read .env manually if dotenv has encoding issues (Windows BOM)
if not os.getenv('ANTHROPIC_API_KEY') and os.path.exists(env_path):
    with open(env_path, 'r', encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())


def _require_env(name):
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill in all required values."
        )
    return val


SECRET_KEY = _require_env('SECRET_KEY')
APP_PASSWORD = _require_env('APP_PASSWORD')
_require_env('ASSEMBLYAI_API_KEY')
_require_env('ANTHROPIC_API_KEY')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(hours=12)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true',
)

# Re-read templates from disk when they change instead of caching them in
# memory. This tool is low-traffic and edited often, so the tiny per-render
# stat() cost is worth not needing a service restart for HTML-only changes.
app.config['TEMPLATES_AUTO_RELOAD'] = True

def _static_version(filename):
    """Build a /static URL with a ?v=<mtime> cache-busting token so browsers
    refetch a changed asset automatically (no manual hard refresh needed)."""
    try:
        v = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
    except OSError:
        v = 0
    return f"{app.static_url_path}/{filename}?v={v}"


@app.context_processor
def _inject_static_version():
    return {'static_v': _static_version}


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'qa_history.db')

logger = logging.getLogger('call-qa-tool')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

aai.settings.api_key = os.getenv('ASSEMBLYAI_API_KEY')
claude_client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

jobs = {}
jobs_lock = threading.Lock()

ALLOWED_EXTENSIONS = {'mp3', 'mp4', 'wav', 'flac', 'm4a', 'aac', 'ogg', 'webm', 'wma'}


# ---------------------------------------------------------------------------
# Network exposure controls
# ---------------------------------------------------------------------------

def _parse_cidrs(raw):
    nets = []
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid CIDR in LAN_ALLOWED_CIDRS: %r", part)
    return nets


_DEFAULT_LAN_CIDRS = '127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16,::1/128,fc00::/7,fe80::/10'
LAN_ALLOWED_NETS = _parse_cidrs(os.getenv('LAN_ALLOWED_CIDRS', _DEFAULT_LAN_CIDRS))
LAN_CHECK_ENABLED = os.getenv('LAN_CHECK_DISABLED', '').lower() != 'true'


def _is_lan_address(addr):
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in LAN_ALLOWED_NETS)


@app.before_request
def enforce_lan_only():
    if not LAN_CHECK_ENABLED:
        return None
    remote = request.remote_addr
    if not _is_lan_address(remote):
        logger.warning("Blocked non-LAN request from %s to %s", remote, request.path)
        return ('Access denied: this service is only available on the local network.', 403)
    return None


# ---------------------------------------------------------------------------
# CSRF / origin check for state-changing requests
# ---------------------------------------------------------------------------

def _same_origin(url):
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if not parsed.netloc:
        return False
    return parsed.netloc.lower() == request.host.lower()


@app.before_request
def csrf_origin_check():
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return None
    origin = request.headers.get('Origin')
    referer = request.headers.get('Referer')
    if origin:
        if _same_origin(origin):
            return None
        logger.warning("Blocked cross-origin %s to %s (Origin=%s)", request.method, request.path, origin)
        return ('Cross-origin request blocked.', 403)
    if referer:
        if _same_origin(referer):
            return None
        logger.warning("Blocked cross-origin %s to %s (Referer=%s)", request.method, request.path, referer)
        return ('Cross-origin request blocked.', 403)
    # No Origin and no Referer on a state-changing request — reject.
    logger.warning("Blocked %s to %s with no Origin/Referer", request.method, request.path)
    return ('Missing Origin/Referer header on state-changing request.', 403)


# ---------------------------------------------------------------------------
# Security response headers
# ---------------------------------------------------------------------------

@app.after_request
def set_security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'same-origin')
    resp.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    )
    resp.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
    if app.config.get('SESSION_COOKIE_SECURE'):
        resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return resp


# ---------------------------------------------------------------------------
# Login rate limiting (per-IP, in-memory)
# ---------------------------------------------------------------------------

_login_attempts = {}  # ip -> deque[timestamps of failed attempts]
_login_lock = threading.Lock()
LOGIN_MAX_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 300  # 5 minutes


def _login_attempt_record(ip, succeeded):
    now = time.time()
    cutoff = now - LOGIN_WINDOW_SECONDS
    with _login_lock:
        dq = _login_attempts.get(ip)
        if dq is None:
            dq = deque()
            _login_attempts[ip] = dq
        while dq and dq[0] < cutoff:
            dq.popleft()
        if succeeded:
            _login_attempts.pop(ip, None)
            return 0
        dq.append(now)
        return len(dq)


def _login_attempts_remaining(ip):
    now = time.time()
    cutoff = now - LOGIN_WINDOW_SECONDS
    with _login_lock:
        dq = _login_attempts.get(ip)
        if not dq:
            return LOGIN_MAX_ATTEMPTS
        while dq and dq[0] < cutoff:
            dq.popleft()
        return max(0, LOGIN_MAX_ATTEMPTS - len(dq))


# ---------------------------------------------------------------------------
# Filename + tag metadata
# ---------------------------------------------------------------------------

# ALV-<anything-non-empty>, case-insensitive on the prefix.
ALV_TAG_RE = re.compile(r'^ALV-\S.*$', re.IGNORECASE)

# YYYYMMDD-HHMMSS_NNNNNNNNNN at the start of the basename (extras after are ignored).
FILENAME_META_RE = re.compile(r'^(\d{4})(\d{2})(\d{2})-\d{6}_(\d{10})')


def parse_filename_metadata(filename):
    """Pull call date (YYYY-MM-DD) and raw 10-digit phone from upload filename.

    Returns ('', '') if the filename doesn't match the expected pattern.
    """
    base = os.path.basename(filename or '')
    m = FILENAME_META_RE.match(base)
    if not m:
        return '', ''
    year, month, day, phone = m.groups()
    return f"{year}-{month}-{day}", phone


def format_phone(raw):
    if raw and len(raw) == 10 and raw.isdigit():
        return f"({raw[0:3]}) {raw[3:6]}-{raw[6:10]}"
    return raw or ''


def normalize_alv_tag(raw):
    """Trim, validate, and uppercase the ALV- prefix. Returns '' if invalid."""
    s = (raw or '').strip()
    if not ALV_TAG_RE.match(s):
        return ''
    return 'ALV-' + s[4:]


def normalize_agent_name(raw):
    """Collapse internal whitespace and trim. Returns '' if blank."""
    s = ' '.join((raw or '').split())
    return s


# ---------------------------------------------------------------------------
# Manual overrides (per-item status toggles + final determination)
# ---------------------------------------------------------------------------

_VALID_STATUSES = ('covered', 'not_covered')
_VALID_DETERMINATIONS = ('PASS', 'FAIL', 'CRITICAL FAIL')


def sanitize_overrides(raw):
    """Reduce arbitrary input to a strict {approval, post_enrollment, determination}.

    Returns None if there are no effective overrides.
    """
    if not isinstance(raw, dict):
        return None
    out = {'approval': {}, 'post_enrollment': {}, 'determination': None}
    for section_key in ('approval', 'post_enrollment'):
        section = raw.get(section_key)
        if not isinstance(section, dict):
            continue
        for idx, status in section.items():
            if status not in _VALID_STATUSES:
                continue
            try:
                out[section_key][str(int(idx))] = status
            except (TypeError, ValueError):
                continue
    det = raw.get('determination')
    if isinstance(det, str) and det in _VALID_DETERMINATIONS:
        out['determination'] = det
    if not out['approval'] and not out['post_enrollment'] and not out['determination']:
        return None
    return out


def apply_overrides(results, overrides):
    """Return a shallow-modified results dict with overrides folded in.

    Item overrides update each item's status and recompute covered_count.
    A determination override replaces final_determination.result.
    Mutated items get an 'overridden': True flag for UI highlighting.
    """
    if not overrides or not isinstance(results, dict):
        return results
    out = dict(results)
    for results_key, ov_key in (('approval_script', 'approval'),
                                ('post_enrollment_script', 'post_enrollment')):
        section = out.get(results_key)
        if not isinstance(section, dict):
            continue
        section_overrides = overrides.get(ov_key) or {}
        if not section_overrides:
            continue
        items = list(section.get('items') or [])
        for idx_str, new_status in section_overrides.items():
            try:
                idx = int(idx_str)
            except ValueError:
                continue
            if 0 <= idx < len(items) and isinstance(items[idx], dict):
                if items[idx].get('status') != new_status:
                    items[idx] = {**items[idx], 'status': new_status, 'overridden': True}
        section = {**section,
                   'items': items,
                   'covered_count': sum(1 for it in items if isinstance(it, dict) and it.get('status') == 'covered')}
        out[results_key] = section
    det_override = overrides.get('determination')
    if det_override:
        fd = dict(out.get('final_determination') or {})
        fd['result'] = det_override
        fd['overridden'] = True
        out['final_determination'] = fd
    return out


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            analyzed_at TEXT NOT NULL,
            transcript TEXT NOT NULL,
            results_json TEXT NOT NULL,
            determination TEXT,
            approval_score TEXT,
            enrollment_score TEXT,
            critical_fail INTEGER DEFAULT 0
        )
    ''')
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(analyses)")}
    for col in ('alv_tag', 'call_date', 'client_phone', 'agent_name', 'overrides_json'):
        if col not in existing_cols:
            conn.execute(f'ALTER TABLE analyses ADD COLUMN {col} TEXT')
    if 'program_flip' not in existing_cols:
        conn.execute('ALTER TABLE analyses ADD COLUMN program_flip INTEGER DEFAULT 0')
    backfill = conn.execute(
        "SELECT id, filename FROM analyses "
        "WHERE COALESCE(call_date, '') = '' OR COALESCE(client_phone, '') = ''"
    ).fetchall()
    for row_id, filename in backfill:
        cd, ph = parse_filename_metadata(filename)
        if cd or ph:
            conn.execute(
                "UPDATE analyses SET call_date = ?, client_phone = ? WHERE id = ?",
                (cd, ph, row_id),
            )
    conn.commit()
    conn.close()
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


def save_analysis(filename, transcript, results, alv_tag='', call_date='', client_phone='', agent_name=''):
    determination = results.get('final_determination', {}).get('result', 'UNKNOWN')
    approval = results.get('approval_script', {}) or {}
    enrollment = results.get('post_enrollment_script', {}) or {}
    high_risk = results.get('high_risk_phrases', {}) or {}
    flip = results.get('program_flip', {}) or {}
    approval_score = f"{approval.get('covered_count', 0)}/{approval.get('total', 18)}"
    enrollment_score = f"{enrollment.get('covered_count', 0)}/{enrollment.get('total', 15)}"
    critical_fail = 1 if high_risk.get('detected') else 0
    program_flip = 1 if flip.get('detected') else 0

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO analyses (filename, analyzed_at, transcript, results_json,
                              determination, approval_score, enrollment_score, critical_fail,
                              alv_tag, call_date, client_phone, agent_name, program_flip)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (filename, datetime.now().isoformat(timespec='seconds'),
          transcript, json.dumps(results), determination,
          approval_score, enrollment_score, critical_fail,
          alv_tag, call_date, client_phone, agent_name, program_flip))
    conn.commit()
    analysis_id = cur.lastrowid
    conn.close()
    return analysis_id


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_or_create_session_id():
    sid = session.get('sid')
    if not sid:
        sid = uuid.uuid4().hex
        session['sid'] = sid
    return sid


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            wants_json = (
                request.accept_mimetypes.best == 'application/json'
                or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or request.path == '/analyze'
                or request.path.startswith('/status/')
                or request.path.startswith('/history')
            )
            if wants_json:
                return jsonify({'error': 'authentication required'}), 401
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def format_timestamp(ms):
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def format_transcript(transcript):
    lines = []
    for utterance in transcript.utterances:
        ts = format_timestamp(utterance.start)
        lines.append(f"[{ts}] Speaker {utterance.speaker}: {utterance.text}")
    return "\n".join(lines)


def _set_job(job_id, **updates):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return
        job.update(updates)


def process_call(job_id, filepath, original_filename, alv_tag, agent_name):
    try:
        call_date, client_phone = parse_filename_metadata(original_filename)
        _set_job(
            job_id,
            status='transcribing',
            message='Transcribing audio with speaker detection...',
            alv_tag=alv_tag,
            agent_name=agent_name,
            call_date=call_date,
            client_phone=format_phone(client_phone),
        )

        config = aai.TranscriptionConfig(
            speaker_labels=True,
            speech_models=[aai.SpeechModel.universal.value],
        )
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(filepath, config=config)

        if transcript.status == aai.TranscriptStatus.error:
            logger.error("Transcription failed for job %s: %s", job_id, transcript.error)
            _set_job(job_id, status='error', error='Transcription failed. See server logs for details.')
            return

        formatted = format_transcript(transcript)
        _set_job(job_id, transcript=formatted, status='analyzing',
                 message='Analyzing transcript against QA checklist...')

        response = claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_analysis_prompt(formatted)}]
        )

        raw = response.content[0].text
        if response.stop_reason == 'max_tokens':
            logger.warning(
                "Job %s: Claude hit max_tokens (%d chars returned). Output likely truncated.",
                job_id, len(raw)
            )
        json_start = raw.find('{')
        json_end = raw.rfind('}') + 1
        if json_start != -1 and json_end > json_start:
            try:
                results = json.loads(raw[json_start:json_end])
            except json.JSONDecodeError as je:
                logger.error(
                    "Job %s: JSON parse failed at %s. stop_reason=%s, raw length=%d. First 500 chars: %r",
                    job_id, je, response.stop_reason, len(raw), raw[:500]
                )
                raise
        else:
            results = {"error": "Could not parse analysis results"}

        try:
            analysis_id = save_analysis(
                original_filename, formatted, results,
                alv_tag=alv_tag, call_date=call_date, client_phone=client_phone,
                agent_name=agent_name,
            )
            _set_job(job_id, analysis_id=analysis_id)
        except Exception as save_err:
            logger.exception("Failed to save analysis for job %s", job_id)
            _set_job(job_id, save_warning='Result not saved to history. See server logs.')

        _set_job(job_id, status='complete', results=results, message='Analysis complete')

    except Exception:
        logger.exception("Job %s failed", job_id)
        _set_job(job_id, status='error', error='Processing failed. See server logs for details.')
    finally:
        if os.path.exists(filepath):
            try:
                os.unlink(filepath)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        ip = request.remote_addr or 'unknown'
        if _login_attempts_remaining(ip) <= 0:
            logger.warning("Login lockout active for %s", ip)
            return render_template(
                'login.html',
                error='Too many failed attempts. Try again in a few minutes.'
            ), 429
        submitted = request.form.get('password') or ''
        # constant-time-ish compare
        ok = len(submitted) == len(APP_PASSWORD) and \
             sum(a != b for a, b in zip(submitted, APP_PASSWORD)) == 0
        _login_attempt_record(ip, ok)
        if ok:
            session.clear()
            session.permanent = True
            session['authenticated'] = True
            _get_or_create_session_id()
            next_url = request.args.get('next') or url_for('index')
            # only allow relative next URLs
            if not next_url.startswith('/') or next_url.startswith('//'):
                next_url = url_for('index')
            return redirect(next_url)
        return render_template('login.html', error='Incorrect password'), 401
    if session.get('authenticated'):
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    return render_template('index.html')


@app.route('/analyze', methods=['POST'])
@login_required
def analyze():
    if 'audio' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['audio']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        exts = ', '.join(sorted(ALLOWED_EXTENSIONS))
        return jsonify({'error': f'Unsupported format. Supported: {exts}'}), 400

    alv_tag = normalize_alv_tag(request.form.get('alv_tag'))
    if not alv_tag:
        return jsonify({'error': 'ALV tag is required. It must start with "ALV-" and include an identifier.'}), 400

    agent_name = normalize_agent_name(request.form.get('agent_name'))
    if not agent_name:
        return jsonify({'error': 'Agent name is required.'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
    file.save(temp.name)
    temp.close()

    call_date, client_phone_raw = parse_filename_metadata(file.filename)

    sid = _get_or_create_session_id()
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            'status': 'uploading',
            'message': 'Processing upload...',
            'filename': file.filename,
            'alv_tag': alv_tag,
            'agent_name': agent_name,
            'call_date': call_date,
            'client_phone': format_phone(client_phone_raw),
            '_owner_sid': sid,
        }

    thread = threading.Thread(
        target=process_call,
        args=(job_id, temp.name, file.filename, alv_tag, agent_name),
        daemon=True,
    )
    thread.start()

    return jsonify({'job_id': job_id})


@app.route('/status/<job_id>')
@login_required
def status(job_id):
    sid = session.get('sid')
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({'status': 'not_found'}), 404
        if job.get('_owner_sid') != sid:
            # Don't leak existence of jobs that belong to other sessions.
            return jsonify({'status': 'not_found'}), 404
        # Strip internal fields before returning.
        safe = {k: v for k, v in job.items() if not k.startswith('_')}
    return jsonify(safe)


@app.route('/history')
@login_required
def history_list():
    limit = request.args.get('limit', default=100, type=int)
    limit = max(1, min(limit, 500))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('''
        SELECT id, filename, analyzed_at, determination,
               approval_score, enrollment_score, critical_fail,
               alv_tag, call_date, client_phone, agent_name, program_flip
        FROM analyses
        ORDER BY id DESC
        LIMIT ?
    ''', (limit,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d['client_phone'] = format_phone(d.get('client_phone'))
        out.append(d)
    return jsonify(out)


@app.route('/history/<int:analysis_id>')
@login_required
def history_detail(analysis_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute('''
        SELECT id, filename, analyzed_at, transcript, results_json,
               alv_tag, call_date, client_phone, agent_name, overrides_json
        FROM analyses WHERE id = ?
    ''', (analysis_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'not found'}), 404
    base_results = json.loads(row['results_json'])
    overrides = json.loads(row['overrides_json']) if row['overrides_json'] else None
    return jsonify({
        'id': row['id'],
        'filename': row['filename'],
        'analyzed_at': row['analyzed_at'],
        'transcript': row['transcript'],
        'results': base_results,
        'overrides': overrides or {},
        'alv_tag': row['alv_tag'] or '',
        'call_date': row['call_date'] or '',
        'client_phone': format_phone(row['client_phone']),
        'agent_name': row['agent_name'] or '',
    })


@app.route('/analysis/<int:analysis_id>/override', methods=['POST'])
@login_required
def set_overrides(analysis_id):
    data = request.get_json(silent=True) or {}
    clean = sanitize_overrides(data)
    conn = sqlite3.connect(DB_PATH)
    exists = conn.execute('SELECT 1 FROM analyses WHERE id = ?', (analysis_id,)).fetchone()
    if not exists:
        conn.close()
        return jsonify({'error': 'Analysis not found.'}), 404
    payload = json.dumps(clean) if clean else None
    conn.execute('UPDATE analyses SET overrides_json = ? WHERE id = ?', (payload, analysis_id))
    # Also refresh the cached determination column so the history list reflects overrides.
    if clean and clean.get('determination'):
        conn.execute('UPDATE analyses SET determination = ? WHERE id = ?',
                     (clean['determination'], analysis_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'overrides': clean or {}})


@app.route('/writeup/<int:analysis_id>', methods=['POST'])
@login_required
def writeup(analysis_id):
    data = request.get_json(silent=True) or {}
    agent_name_override = normalize_agent_name(data.get('agent_name'))
    mode = 'verbal' if data.get('mode') == 'verbal' else 'written'
    try:
        buf, filename = render_writeup_for_analysis(
            DB_PATH, claude_client, analysis_id, agent_name_override or None,
            apply_overrides_fn=apply_overrides,
            mode=mode,
        )
    except LookupError:
        return jsonify({'error': 'Analysis not found.'}), 404
    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400
    except Exception:
        logger.exception("Write-up generation failed for analysis %s", analysis_id)
        return jsonify({'error': 'Could not generate write-up. See server logs.'}), 500

    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        as_attachment=True,
        download_name=filename,
    )


@app.errorhandler(413)
def too_large(_):
    return jsonify({'error': 'File too large. Maximum upload size is 200 MB.'}), 413


init_db()


if __name__ == '__main__':
    # Dev server. Production should use `python serve.py` (Waitress).
    # debug=False — never enable the Werkzeug debugger; it allows RCE if reachable.
    app.run(host='127.0.0.1', port=5000, debug=False)
