"""
Clerk JWT verification and per-request auth helpers.

Clerk's JS SDK stores an authenticated session as an HttpOnly cookie named
__session containing a signed JWT. On every request we:
  1. Read that cookie.
  2. Verify its signature against Clerk's published JWKS.
  3. Extract the Clerk user ID from the 'sub' claim.
  4. Load (or lazily create) the matching row from our users table.
  5. Store the result in Flask's g.user and g.org for the duration of the
     request.

All protected routes then just check g.user / g.org via the decorators below.
"""

import logging
from functools import wraps

import jwt
from jwt import PyJWKClient
from flask import abort, current_app, g, redirect, request, url_for

from db import get_db
from models import Organization, User

logger = logging.getLogger(__name__)

# Module-level JWKS client; lazily initialised so we don't hit the network
# at import time.  PyJWKClient caches keys in memory.
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(
            current_app.config["CLERK_JWKS_URL"], cache_keys=True
        )
    return _jwks_client


def _verify_session_token(token: str) -> dict | None:
    """Return the verified JWT claims dict, or None if the token is invalid."""
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_exp": True, "verify_nbf": True},
        )
    except Exception as exc:
        logger.debug("JWT verification failed: %s", exc)
        return None


def _sync_user(clerk_user_id: str, email: str, db) -> User:
    """
    Return the User row for this Clerk user ID, creating one on first login.
    The org_id starts as None — the user is prompted to create/join an org.
    """
    user = db.query(User).filter_by(id=clerk_user_id).first()
    if user is None:
        user = User(id=clerk_user_id, email=email, role="member")
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("First login — created user record %s (%s)", clerk_user_id, email)
    return user


def load_user() -> None:
    """
    before_request hook.  Populates g.user and g.org (either may be None).
    Uses g.db which must be opened by a prior before_request hook.
    """
    g.user = None
    g.org = None

    token = request.cookies.get("__session")
    if not token:
        return

    claims = _verify_session_token(token)
    if not claims:
        return

    clerk_user_id: str = claims.get("sub", "")
    if not clerk_user_id:
        return

    # 'email' is not a standard JWT claim but Clerk includes it when you
    # enable the "Email address" field in your JWT template.  Fall back to
    # empty string if absent (user will still be created; email can be
    # backfilled later via the Clerk webhook in Phase 4).
    email: str = claims.get("email", "")

    try:
        user = _sync_user(clerk_user_id, email, g.db)
        g.user = user

        if user.org_id:
            g.org = g.db.query(Organization).filter_by(id=user.org_id).first()
    except Exception:
        logger.exception("Error loading user from DB")
        g.db.rollback()


# ---------------------------------------------------------------------------
# Route decorators
# ---------------------------------------------------------------------------

def _is_api_request() -> bool:
    return bool(
        request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


def login_required(f):
    """Require a valid Clerk session. Redirects to /auth/login otherwise."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.user is None:
            if _is_api_request():
                abort(401)
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def org_required(f):
    """Require a valid session AND membership in an org."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.user is None:
            if _is_api_request():
                abort(401)
            return redirect(url_for("auth.login"))
        if g.org is None:
            return redirect(url_for("org.setup"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require a valid session, org membership, and admin role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.user is None:
            if _is_api_request():
                abort(401)
            return redirect(url_for("auth.login"))
        if g.org is None:
            return redirect(url_for("org.setup"))
        if g.user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated
