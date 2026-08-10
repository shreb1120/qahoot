# Qaboom

Multi-tenant call compliance QA. A reviewer drops a call recording into the
browser, the tool transcribes it with speaker labels, and Claude scores the call
against **that organization's own checklist** — sections, requirements and
auto-fail phrases they write and edit themselves. Results and the full
transcript are saved for later review, and failures go into a manager sign-off
queue.

Runs as a hosted SaaS at qaboom.io. Every org's checklist, calls, agents and
usage are scoped to that org.

---

## How it works

```
Browser (QA reviewer)
   │  drag-drop audio file
   ▼
Flask app  ──► AssemblyAI (universal, speaker labels)
   │                    │
   │                    ▼
   │              formatted transcript
   │                    │
   │                    ▼
   └──► Anthropic Claude (claude-sonnet-4-6)
                        │
                        ▼
              structured JSON results
                        │
                        ▼
                  SQLite (qa_history.db)
                        │
                        ▼
                Browser renders results
                + History tab lists past runs
```

**Stack**

| Layer | Choice |
|---|---|
| Web framework | Flask 3.1 |
| Production server | Waitress 3.0 (`serve.py`) |
| Transcription | AssemblyAI `universal` with speaker labels + timestamps |
| Analysis | Anthropic `claude-sonnet-4-6` via the Python SDK |
| Storage | SQLite (single file, `qa_history.db`) |
| Auth | Session cookie, single shared password from `APP_PASSWORD` |
| UI | Server-rendered HTML + vanilla JS (no build step) |

**Job model.** Uploads are processed by a background thread; the browser polls `/status/<job_id>` every couple of seconds for progress (`uploading` → `transcribing` → `analyzing` → `complete`). In-flight jobs are kept in memory only — a restart loses jobs that haven't finished but **never loses completed history**, which is on disk in SQLite.

**The checklist is per-org data, not code.** What "compliant" means lives in each organization's `ComplianceProfile` and is edited in the app. The prompt in `pipeline.py` is assembled from it at grading time, so nothing in this repo hardcodes any one customer's rules.

**Cost.** Roughly **$0.20–$0.40 per ~30-minute call** between AssemblyAI and Anthropic.

**File support.** MP3, MP4, WAV, FLAC, M4A, AAC, OGG, WebM, WMA — up to 500 MB.

---

## Styling — rebuild CSS after editing templates

Tailwind is compiled ahead of time into `static/tailwind.css`. It used to load
from a CDN and compile in the browser on every page load; that was ~126 KB of
compiler on the critical path plus an outage dependency on a third party.

**After editing anything in `templates/` or `static/*.js`, run:**

```bash
./build-css.sh
sudo systemctl restart qaboom
```

Tailwind only emits the utility classes it can actually find in your files. If
you add a class to a template and don't rebuild, that class simply won't exist
and the element will render unstyled. The app logs a warning at startup when
`static/tailwind.css` is older than a template, so check the service log if
something looks wrong:

```bash
sudo systemctl status qaboom
```

While editing a lot, `./build-css.sh --watch` rebuilds automatically.

Node is only needed to *build* the CSS. The running server just serves the
committed file — production needs no Node at all.

Inter is self-hosted in `static/fonts/` (variable font, latin + latin-ext).
Nothing needs regenerating unless the required weights or subsets change.

## Routes

| Path | Method | Purpose |
|---|---|---|
| `/login`, `/logout` | GET, POST | Shared-password auth |
| `/` | GET | Upload UI |
| `/analyze` | POST | Accepts the audio file, returns a `job_id` |
| `/status/<job_id>` | GET | Polled by the browser for progress + results |
| `/history` | GET | JSON list of past analyses (most recent first) |
| `/history/<id>` | GET | JSON for a single past analysis (transcript + full results) |

Every route except `/login` requires authentication.

---

## Install — local development

Prerequisites: **Python 3.13**, **git**.

```powershell
git clone https://github.com/<your-username>/call-qa-tool.git
cd call-qa-tool

python -m venv venv
.\venv\Scripts\Activate.ps1

pip install -r requirements.txt

copy .env.example .env
# Open .env and fill in the four required values (see below)

python app.py
```

The Flask dev server runs at <http://localhost:5000>. Use this for code changes — it auto-reloads. Don't use it for production.

### Required environment variables

Put these in a `.env` file in the project root. `.env` is gitignored — never commit it.

| Variable | What it is |
|---|---|
| `ASSEMBLYAI_API_KEY` | From <https://www.assemblyai.com/app/account> |
| `ANTHROPIC_API_KEY` | From <https://console.anthropic.com/settings/keys> |
| `APP_PASSWORD` | The shared password your QA team types into the login screen |
| `SECRET_KEY` | Used to sign session cookies. Generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

Optional (used by `serve.py` only):

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `127.0.0.1` | Interface to bind. Default is loopback only. To serve the LAN, set to the server's specific LAN IP (e.g. `192.168.1.50`). **Avoid `0.0.0.0`** — it binds every NIC including VPN/virtual interfaces. |
| `PORT` | `5000` | TCP port. |
| `THREADS` | `8` | Waitress worker threads. |
| `LAN_ALLOWED_CIDRS` | RFC1918 + loopback + link-local + IPv6 ULA | Comma-separated CIDR allowlist enforced inside the Flask app. Tighten to your office subnet (e.g. `192.168.1.0/24,127.0.0.1/32`) for defense in depth. |
| `LAN_CHECK_DISABLED` | `false` | Set to `true` only for local debugging. Disables the app-level LAN check (firewall still applies). |
| `SESSION_COOKIE_SECURE` | `false` | Set to `true` only if a TLS-terminating reverse proxy sits in front of the app. Adds the `Secure` cookie flag and HSTS. |

---

## Install — production (Windows server)

Use Waitress as the WSGI server and NSSM to run it as a Windows service that starts on boot.

### 1. Copy / clone the project to the server

```powershell
cd C:\apps
git clone https://github.com/<your-username>/call-qa-tool.git
cd call-qa-tool
```

### 2. Create a venv and install dependencies

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Create the production `.env`

```powershell
copy .env.example .env
notepad .env
```

Use a **fresh** strong `SECRET_KEY` (do not reuse the development one) and a strong `APP_PASSWORD`.

### 4. Smoke test before installing the service

```powershell
python serve.py
```

You should see `Call QA Analyzer listening on http://<your-LAN-IP>:5000`. Open `http://<server-ip>:5000` from another machine on the LAN, log in, upload a short test recording, and confirm an entry appears in History. Then `Ctrl+C` to stop.

> Set `HOST=<server's LAN IP>` in `.env` before this step. The default `127.0.0.1` is loopback-only and won't be reachable from other machines.

### 5. Install NSSM and register the service

Download NSSM from <https://nssm.cc/download> and extract `nssm.exe` to `C:\nssm\`.

```powershell
C:\nssm\nssm.exe install CallQATool
```

In the dialog that opens:

- **Path:** `C:\apps\call-qa-tool\venv\Scripts\python.exe`
- **Startup directory:** `C:\apps\call-qa-tool`
- **Arguments:** `serve.py`
- **I/O tab:** point **Output (stdout)** and **Error (stderr)** at `C:\apps\call-qa-tool\logs\service.log` (create the `logs\` folder first)
- **Details tab:** set Startup type to **Automatic**

Then start it:

```powershell
C:\nssm\nssm.exe start CallQATool
```

It will now start automatically on every reboot. Manage it with:

```powershell
C:\nssm\nssm.exe status  CallQATool
C:\nssm\nssm.exe restart CallQATool
C:\nssm\nssm.exe stop    CallQATool
```

### 6. Open the firewall (LAN-only)

Remove any existing overly-permissive rule first, then add a rule scoped to your LAN. Replace the `-RemoteAddress` list with your actual office subnet(s) — the example below covers all of RFC1918, which you should narrow if you can.

```powershell
# Remove any old rule on this port that may have allowed wider access
Get-NetFirewallRule -DisplayName "Call QA Analyzer*" -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -Confirm:$false

New-NetFirewallRule -DisplayName "Call QA Analyzer - LAN Only" `
                    -Direction Inbound -Protocol TCP -LocalPort 5000 `
                    -RemoteAddress @("192.168.0.0/16","10.0.0.0/8","172.16.0.0/12") `
                    -Action Allow -Profile Domain,Private
```

Verify:

```powershell
Get-NetFirewallRule -DisplayName "Call QA Analyzer - LAN Only" | Format-List
```

The app also enforces an IP allowlist at the application layer (`LAN_ALLOWED_CIDRS`) as a defense-in-depth check — anything from outside that list gets a 403 even if the firewall is misconfigured. Tighten both lists to your actual subnet (e.g. `192.168.1.0/24`) if you can.

**Do not** expose port 5000 to the public internet or set up port-forwarding on the office router. There is no TLS; the shared password would travel in cleartext.

### 7. Updating later

```powershell
cd C:\apps\call-qa-tool
git pull
C:\nssm\nssm.exe restart CallQATool
```

If `requirements.txt` changed, `pip install -r requirements.txt` inside the venv first.

---

## Security

The app is designed for LAN-only use behind a shared password. The following protections are in place:

- **LAN-only enforcement** at two layers: Windows Firewall rule + an in-app IP allowlist (`LAN_ALLOWED_CIDRS`). Both default to private ranges only.
- **Loopback by default** — `HOST` defaults to `127.0.0.1`; an admin must explicitly bind to the LAN IP.
- **Session cookies** are `HttpOnly` + `SameSite=Lax`. Set `SESSION_COOKIE_SECURE=true` if you put TLS in front of the app.
- **CSRF protection** — state-changing requests require a same-origin `Origin` or `Referer`.
- **Login brute-force throttling** — 8 failed attempts per IP per 5-minute window then a temporary lockout.
- **Security headers** — `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, a restrictive CSP, and `Referrer-Policy: same-origin`.
- **Werkzeug debugger is disabled** in both `app.py` and `serve.py`.
- **Generic error messages** to clients; full stack traces go only to `logs/service.log`.

What you still need to do yourself:

1. **Rotate the API keys** in `.env` — `ASSEMBLYAI_API_KEY` and `ANTHROPIC_API_KEY` were committed to disk historically and should be regenerated at the provider consoles.
2. **Set `HOST`** to the server's specific LAN IP in `.env` before installing the service.
3. **Tighten `LAN_ALLOWED_CIDRS`** and the firewall rule's `-RemoteAddress` to your actual office subnet.
4. **Back up `qa_history.db`** somewhere off this server.

## Backups

The only stateful file is **`qa_history.db`**. Back it up on whatever cadence your retention policy requires — a nightly file copy to a backed-up share is enough. Everything else is in source control.

---

## Troubleshooting

**"Port 5000 already in use" / the app shows stale behavior.** A previous `python` process is still holding the port. Find and kill it:

```powershell
Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue | Select-Object OwningProcess
Stop-Process -Id <pid>
```

**Login page shows "APP_PASSWORD is not configured".** `.env` is missing or `APP_PASSWORD` isn't set. Confirm the file exists in the project root and the variable has a value.

**Transcription or analysis fails immediately.** Check outbound HTTPS from the server to `api.assemblyai.com` and `api.anthropic.com` isn't blocked.

**Service won't start after install.** Check `C:\apps\call-qa-tool\logs\service.log` — usually a missing env var or a typo in the NSSM Path / Startup directory.

---

## Project layout

```
call-qa-tool/
├── app.py                # Flask app, routes, job worker, SQLite persistence
├── serve.py              # Waitress production entry point
├── requirements.txt
├── .env.example          # Template — copy to .env and fill in
├── .gitignore
├── templates/
│   ├── login.html
│   └── index.html        # Upload + Results + History UI
└── static/
    ├── app.js
    └── style.css
```

---

## Keep this repository private

It holds deployment configuration and the grading pipeline for a live service
carrying customer call recordings and transcripts.
