"""
Qaboom — multi-tenant call compliance QA (SaaS rewrite).

This file is the application factory.  Run via serve.py (Waitress) in
production, or `flask run` for local development.

The original single-tenant tool (qa_prompt.py, writeup.py) is preserved in
this repo and will be wired in during Phase 2.
"""
import logging
import os

from dotenv import load_dotenv
from flask import Flask, g, redirect, request, url_for

from config import Config
from db import get_db, init_db
from auth import load_user

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # ── Database ──────────────────────────────────────────────────────────
    init_db(app.config["DATABASE_URL"])

    # ── Per-request DB session ────────────────────────────────────────────
    @app.before_request
    def open_db():
        g.db = get_db()

    @app.teardown_request
    def close_db(exc=None):
        db = g.pop("db", None)
        if db is not None:
            if exc:
                db.rollback()
            db.close()

    # ── Auth (runs after open_db so g.db is available) ────────────────────
    app.before_request(load_user)

    # ── Request logging ───────────────────────────────────────────────────
    @app.after_request
    def log_request(response):
        logger.info("%s %s → %d (user=%s)",
                    request.method, request.path, response.status_code,
                    getattr(g, "user", None) and g.user.id)
        return response

    # ── Security headers ──────────────────────────────────────────────────
    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        # CSP allows Clerk's CDN and API domains needed for the JS components.
        # 'unsafe-inline' for style-src is required by Clerk's embedded components;
        # this will be tightened with a nonce in Phase 4.
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.tailwindcss.com https://*.clerk.accounts.dev https://*.clerk.com; "
            "style-src 'self' 'unsafe-inline' https://*.clerk.accounts.dev https://fonts.googleapis.com; "
            "img-src 'self' data: https://*.clerk.accounts.dev https://img.clerk.com; "
            "connect-src 'self' https://cdn.tailwindcss.com https://*.clerk.accounts.dev https://*.clerk.com https://*.clerkinc.com wss://*.clerk.accounts.dev wss://*.clerk.com; "
            "frame-src https://*.clerk.accounts.dev https://*.clerk.com; "
            "font-src 'self' https://*.clerk.accounts.dev https://fonts.gstatic.com; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["Content-Security-Policy"] = csp
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    # ── Template globals ──────────────────────────────────────────────────
    # clerk_key is needed in base.html on every page, not just auth pages.
    @app.context_processor
    def inject_clerk_key():
        return {"clerk_key": app.config.get("CLERK_PUBLISHABLE_KEY", "")}

    # ── Upload directory ──────────────────────────────────────────────────
    import os as _os
    _os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # ── Blueprints ────────────────────────────────────────────────────────
    from blueprints.auth_bp import auth_bp
    from blueprints.org_bp import org_bp
    from blueprints.dashboard_bp import dashboard_bp
    from blueprints.calls_bp import calls_bp
    from blueprints.profile_bp import profile_bp
    from blueprints.agents_bp import agents_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(org_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(calls_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(agents_bp)

    # Convenience redirect: /login → /auth/login
    @app.get("/login")
    def login_redirect():
        return redirect(url_for("auth.login"))

    # ── Error pages ───────────────────────────────────────────────────────
    # Without these, Werkzeug's unbranded default pages are what a customer
    # sees when something goes wrong — including on the upload path, where an
    # oversized file is a routine mistake rather than an exceptional one.
    from flask import jsonify, render_template

    def _wants_json() -> bool:
        return (
            request.path.endswith(".json")
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.accept_mimetypes.best == "application/json"
        )

    def _error_page(code: int, heading: str, detail: str, action: str):
        if _wants_json():
            return jsonify({"error": heading, "detail": detail}), code

        signed_in = getattr(g, "user", None) and getattr(g, "org", None)
        if action == "signin" or not signed_in:
            action_url, action_label = url_for("auth.login"), "Sign in"
        elif action == "upload":
            action_url, action_label = url_for("calls.upload_form"), "Back to upload"
        else:
            action_url, action_label = url_for("dashboard.index"), "Go to dashboard"

        return render_template(
            "errors/error.html",
            code=code, heading=heading, detail=detail,
            action_url=action_url, action_label=action_label,
        ), code

    @app.errorhandler(401)
    def handle_401(_e):
        return _error_page(
            401, "Your session has expired",
            "Sign in again to pick up where you left off.",
            "signin",
        )

    @app.errorhandler(403)
    def handle_403(_e):
        return _error_page(
            403, "You don't have access to that",
            "This page is limited to organization admins. Ask a teammate with "
            "admin access if you need it.",
            "dashboard",
        )

    @app.errorhandler(404)
    def handle_404(_e):
        return _error_page(
            404, "We couldn't find that page",
            "The link may be out of date, or the call may have been removed "
            "from your organization.",
            "dashboard",
        )

    @app.errorhandler(413)
    def handle_413(_e):
        limit_mb = app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)
        return _error_page(
            413, "That recording is too large",
            f"Uploads are limited to {limit_mb} MB. Split the recording into "
            "shorter segments or compress it, then try again.",
            "upload",
        )

    @app.errorhandler(500)
    def handle_500(e):
        logger.error("Unhandled error on %s %s", request.method, request.path, exc_info=e)
        return _error_page(
            500, "Something went wrong on our end",
            "The error has been logged. Try again in a moment — if it keeps "
            "happening, contact support with the time it occurred.",
            "dashboard",
        )

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=False)
