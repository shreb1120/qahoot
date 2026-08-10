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
    """Return the verified JWT claims dict, or None if the token is invalid.

    A valid signature is not the same as a token meant for us. Two bindings
    beyond the signature, and the reason for each:

    **Issuer.** Pins the token to our own Clerk instance. Without it any Clerk
    instance whose key our JWKS client would fetch could authenticate here.

    **Authorized party (`azp`).** This is the one that matters. `azp` is the
    origin that asked Clerk for the token. A Clerk *development* instance
    answers any origin, and the publishable key is public in our page source —
    so another site can have Clerk mint a genuine, correctly-signed session
    token for a visiting user of ours, with `azp` set to that site. Replayed
    here as `Authorization: Bearer`, it used to be accepted: full account
    takeover with a signature that verifies perfectly. CSRF does not help, as
    the attacker's own server can fetch a matching token and cookie pair.

    `azp` is enforced when present rather than required, deliberately. Clerk
    puts it in every token this instance issues (verified against a live one),
    so "must match if present" blocks the attack completely — the attacker's
    token *has* an `azp`, just the wrong one. Requiring it would add nothing
    against that attack while risking locking every user out if Clerk ever
    changes its claim set.

    `aud` is not checked at all: these tokens do not carry one.
    """
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=current_app.config.get("CLERK_ISSUER") or None,
            options={
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iss": bool(current_app.config.get("CLERK_ISSUER")),
                "verify_aud": False,
            },
        )
    except Exception as exc:
        logger.debug("JWT verification failed: %s", exc)
        return None

    allowed = current_app.config.get("CLERK_AUTHORIZED_PARTIES") or ()
    azp = claims.get("azp")
    if azp and allowed and azp not in allowed:
        # Warning, not debug: this is either an attack or a misconfiguration
        # after adding a domain, and both need to be visible.
        logger.warning(
            "Rejected a validly-signed Clerk token issued to another origin "
            "(azp=%s, user=%s). If this domain is legitimately ours, add it to "
            "CLERK_AUTHORIZED_PARTIES.",
            azp, claims.get("sub"),
        )
        return None
    if not azp:
        logger.warning("Clerk token has no azp claim; origin binding not enforced")

    return claims


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

    # Prefer Authorization: Bearer header (used by AJAX polling to pass a
    # freshly-minted Clerk token, avoiding cookie expiry 401s).
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
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


def safe_next(value: str | None) -> str | None:
    """Return `value` only if it is a same-site path we may redirect to.

    Anything that could leave the site is dropped. An attacker-supplied
    ?next= is a classic open-redirect and this is the only place it is trusted.
    """
    if not value or not value.startswith("/"):
        return None
    # "//evil.com" and "/\evil.com" are protocol-relative or browser-normalised
    # escapes out of the site.
    if value.startswith(("//", "/\\")):
        return None
    if "://" in value or "\n" in value or "\r" in value:
        return None
    return value


def _login_redirect():
    """Send the visitor to sign-in, remembering where they were going.

    Without this, an expired or not-yet-ready Clerk token turns any deep link
    into a trip to the dashboard: the guard bounces to /auth/login, Clerk's JS
    finds a valid session and follows afterSignInUrl, and the original
    destination is gone.
    """
    from urllib.parse import urlencode
    target = url_for("auth.login")
    if request.method == "GET":
        here = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
        if safe_next(here) and not here.startswith(target):
            return redirect(f"{target}?{urlencode({'next': here})}")
    return redirect(target)


def login_required(f):
    """Require a valid Clerk session. Redirects to /auth/login otherwise."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.user is None:
            if _is_api_request():
                abort(401)
            return _login_redirect()
        return f(*args, **kwargs)
    return decorated


def org_required(f):
    """Require a valid session AND membership in an org."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.user is None:
            if _is_api_request():
                abort(401)
            return _login_redirect()
        if g.org is None:
            if _is_api_request():
                abort(403)
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
            return _login_redirect()
        if g.org is None:
            return redirect(url_for("org.setup"))
        if g.user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated
