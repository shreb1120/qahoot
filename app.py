"""
Qaboom — multi-tenant call compliance QA (SaaS rewrite).

This file is the application factory.  Run via serve.py (Waitress) in
production, or `flask run` for local development.
"""
import logging
import os

from dotenv import load_dotenv
from flask import Flask, g, redirect, request, url_for

import csrf
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

    # ── CSRF (runs after load_user, so a rejection is attributable) ───────
    csrf.init_app(app)

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
        # Tailwind and Inter are now built and self-hosted, so cdn.tailwindcss.com,
        # fonts.googleapis.com and fonts.gstatic.com are gone from every
        # directive — the app no longer executes or loads anything from them.
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

    # The sidebar meter renders on every authenticated page, so the summary is
    # injected once here rather than threaded through a dozen view functions.
    # One primary-key read; failures are swallowed because a usage widget must
    # never be able to take down the page it decorates.
    @app.context_processor
    def inject_usage():
        if not (getattr(g, "user", None) and getattr(g, "org", None)):
            return {"usage_summary": None}
        try:
            import usage
            return {"usage_summary": usage.summary_for(g.db, g.org)}
        except Exception:
            logger.exception("Could not build usage summary for the sidebar")
            return {"usage_summary": None}

    # ── Cache-busted static URLs ──────────────────────────────────────────
    # The built CSS and the font files never change without their content
    # changing, so they can be cached hard — provided the URL moves when the
    # file does. Hash is computed once per file per process.
    _asset_hashes: dict = {}

    def static_url(filename: str) -> str:
        import hashlib
        if filename not in _asset_hashes:
            path = os.path.join(app.static_folder, filename)
            try:
                with open(path, "rb") as fh:
                    _asset_hashes[filename] = hashlib.md5(fh.read()).hexdigest()[:10]
            except OSError:
                logger.warning("static_url: %s not found", filename)
                _asset_hashes[filename] = "missing"
        return url_for("static", filename=filename, v=_asset_hashes[filename])

    app.jinja_env.globals["static_url"] = static_url

    @app.after_request
    def cache_static(response):
        # Only fingerprinted URLs get a long max-age; anything else keeps
        # Flask's conditional-request default so it can never go stale.
        if request.path.startswith("/static/") and request.args.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    # ── Built CSS freshness check ─────────────────────────────────────────
    # Tailwind is compiled ahead of time, so a template edited after the last
    # build gets classes that were never emitted — styles silently missing on
    # that page. Cheap mtime check at boot turns that into a loud log line.
    def _warn_if_css_stale():
        css = os.path.join(app.static_folder, "tailwind.css")
        if not os.path.exists(css):
            logger.error("static/tailwind.css is MISSING — run ./build-css.sh")
            return
        built = os.path.getmtime(css)
        newer = [
            os.path.join(root, f)
            for root, _, files in os.walk(app.template_folder)
            for f in files if f.endswith(".html")
            and os.path.getmtime(os.path.join(root, f)) > built
        ]
        if newer:
            logger.warning(
                "static/tailwind.css is older than %d template(s) — run "
                "./build-css.sh or new classes will have no styles. First: %s",
                len(newer), os.path.relpath(newer[0], app.root_path),
            )

    _warn_if_css_stale()

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
    from blueprints.billing_bp import billing_bp
    from blueprints.marketing_bp import marketing_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(org_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(calls_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(agents_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(marketing_bp)

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


# Deliberately NO module-level `app = create_app()`.
#
# `serve.py` does `from app import create_app`, which executes this module —
# so a module-level call built a second, production-configured app on every
# start: recover_stranded ran twice per deploy, and a second engine orphaned
# the first pool's connections.
#
# Worse, tests/conftest.py imports this module too. That call used the real
# Config, so **running pytest connected to the production database and ran
# recover_stranded against it** — which can flip live rows to `error` and
# re-submit real calls to the vendors. conftest's _guard_not_production()
# checks the test URL and cannot see an import side-effect that already
# happened.
#
# For `flask run`, point FLASK_APP at the factory: FLASK_APP="app:create_app".
if __name__ == "__main__":
    create_app().run(debug=False)
