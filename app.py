"""
Qahoot — multi-tenant call compliance QA (SaaS rewrite).

This file is the application factory.  Run via serve.py (Waitress) in
production, or `flask run` for local development.

The original single-tenant tool (qa_prompt.py, writeup.py) is preserved in
this repo and will be wired in during Phase 2.
"""
import logging
import os

from dotenv import load_dotenv
from flask import Flask, g, redirect, url_for

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
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://*.clerk.accounts.dev https://*.clerk.com; "
            "style-src 'self' 'unsafe-inline' https://*.clerk.accounts.dev; "
            "img-src 'self' data: https://*.clerk.accounts.dev https://img.clerk.com; "
            "connect-src 'self' https://*.clerk.accounts.dev https://*.clerk.com https://*.clerkinc.com wss://*.clerk.accounts.dev wss://*.clerk.com; "
            "frame-src https://*.clerk.accounts.dev https://*.clerk.com; "
            "font-src 'self' https://*.clerk.accounts.dev; "
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

    app.register_blueprint(auth_bp)
    app.register_blueprint(org_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(calls_bp)
    app.register_blueprint(profile_bp)

    # Convenience redirect: /login → /auth/login
    @app.get("/login")
    def login_redirect():
        return redirect(url_for("auth.login"))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=False)
