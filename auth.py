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


def _email_is_verified(clerk_user_id: str, email: str) -> bool | None:
    """Has Clerk actually verified that this user owns this address?

    True / False / None, where None means "could not determine".

    This exists because re-linking an existing account to a new Clerk id on the
    strength of a matching email is an account takeover if the address was never
    proven. Signing up as someone@example.com would inherit their organization
    and their admin role.

    This instance does demand an emailed code at sign-up and on a new device, so
    in practice the addresses are verified — but the session token carries no
    `email_verified` claim, so the app cannot see that, and an instance setting
    can change without anyone touching this code. An assumption that grants
    admin access has to be checked, not remembered.

    Asked of Clerk directly, which is authoritative. Anything that is not a
    clear yes returns None and the caller refuses to re-link.
    """
    secret = current_app.config.get("CLERK_SECRET_KEY")
    if not secret:
        return None

    import json as _json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"https://api.clerk.com/v1/users/{clerk_user_id}",
        headers={"Authorization": f"Bearer {secret}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
    except Exception as exc:
        # Never let Clerk being slow or unreachable turn into a login failure —
        # the caller degrades to "do not re-link", which is safe.
        logger.warning("Could not confirm email verification for %s: %s",
                       clerk_user_id, exc)
        return None

    target = (email or "").strip().lower()
    for addr in data.get("email_addresses") or []:
        if str(addr.get("email_address", "")).strip().lower() != target:
            continue
        return (addr.get("verification") or {}).get("status") == "verified"
    return None


def _sync_user(clerk_user_id: str, email: str, db) -> User:
    """
    Return the User row for this Clerk user ID, creating one on first login.
    The org_id starts as None — the user is prompted to create/join an org.

    Handles the case where the email is already known under a *different* Clerk
    id. That happens whenever a Clerk account is deleted and remade, or someone
    signs up again with an address they used before: Clerk issues a brand new
    `sub`, while `users.email` is UNIQUE here.

    Before this, the insert below raised IntegrityError from inside a
    before_request hook, so every authenticated request died and the person was
    bounced to the landing page — permanently, with no way out from the UI, and
    no clue as to why. It locked a real user out during a live test.

    Re-linking is the right resolution rather than a second row: Clerk verifies
    ownership of the mailbox before it will issue a session, so the same
    verified address is the same person. That is the model `users.email UNIQUE`
    already asserted; this makes the code agree with it.
    """
    user = db.query(User).filter_by(id=clerk_user_id).first()
    if user is not None:
        return user

    if email:
        prior = db.query(User).filter_by(email=email).first()
        if prior is not None:
            # Only re-link on a *proven* address. Anything less would let
            # signing up as someone@example.com inherit their org and role.
            if _email_is_verified(clerk_user_id, email) is True:
                return _relink_user(prior, clerk_user_id, email, db)
            logger.error(
                "Refusing to re-link %s to Clerk id %s — could not confirm the "
                "address is verified. They get a new, empty account. Set "
                "CLERK_SECRET_KEY to enable this check.",
                email, clerk_user_id,
            )
            # Fall through with a distinct address so the unique index is not
            # violated. The 500 loop this whole function exists to fix stays
            # fixed; the person lands on org setup rather than someone else's
            # organization, which is the safe direction to be wrong in.
            email = f"{email}#unverified-{clerk_user_id[-8:]}"

    # `users.email` is NOT NULL and UNIQUE, so an absent email claim cannot be
    # stored as "" more than once — the second such user would collide and 500
    # exactly like the bug above. `.invalid` is reserved by RFC 2606 and can
    # never route, so a placeholder here is unmistakably not a real address.
    # Clerk does send `email` for this instance; this is a guard, not a path we
    # expect to take.
    if not email:
        email = f"unknown+{clerk_user_id}@users.qaboom.invalid"
        logger.warning("Clerk token for %s carried no email claim", clerk_user_id)

    user = User(id=clerk_user_id, email=email, role="member")
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("First login — created user record %s (%s)", clerk_user_id, email)
    return user


def _relink_user(prior: User, clerk_user_id: str, email: str, db) -> User:
    """Move an existing user's org, role and history onto a new Clerk id.

    The id cannot simply be updated: `calls.uploaded_by_user_id` and
    `reports.reviewed_by_user_id` reference it ON UPDATE NO ACTION, so the row
    is migrated rather than mutated — free the unique email, insert the new
    identity, re-point everything that referenced the old one, drop it. One
    transaction, so a failure anywhere leaves the old identity intact and the
    person merely sees the same error as before rather than losing their work.
    """
    from sqlalchemy import update as sa_update

    from models import Call, Report

    old_id, org_id, role, created = (
        prior.id, prior.org_id, prior.role, prior.created_at
    )
    logger.warning(
        "Re-linking %s from Clerk id %s to %s — same verified address, new "
        "Clerk account. Org and history are preserved.",
        email, old_id, clerk_user_id,
    )

    # Free the unique email before inserting the replacement.
    prior.email = f"{email}#migrating-{clerk_user_id[-8:]}"
    db.flush()

    replacement = User(id=clerk_user_id, org_id=org_id, email=email,
                       role=role, created_at=created)
    db.add(replacement)
    db.flush()

    db.execute(sa_update(Call)
               .where(Call.uploaded_by_user_id == old_id)
               .values(uploaded_by_user_id=clerk_user_id))
    db.execute(sa_update(Report)
               .where(Report.reviewed_by_user_id == old_id)
               .values(reviewed_by_user_id=clerk_user_id))

    db.delete(db.get(User, old_id))
    db.commit()
    db.refresh(replacement)
    return replacement


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
