"""CSRF protection for state-changing requests.

There was none. Every POST in this app — deleting an agent, switching a
checklist, overriding a compliance verdict — was defended only by
`SESSION_COOKIE_SAMESITE="Lax"`, which browsers apply inconsistently and which
was never designed to be the whole defence. Adding subscription endpoints made
that untenable: a cross-site POST that changes a customer's plan is a different
class of problem from one that renames an agent.

The design is a signed double-submit token:

* The token is derived from the signed session, so it cannot be forged without
  the session cookie, and no server-side store is needed.
* It is checked on every unsafe method by a single `before_request` hook rather
  than per-route, because the failure mode of per-route protection is the route
  someone forgets.
* Exemptions are explicit and few. The Stripe webhook is the only one that
  matters: it is authenticated by signature, has no session, and Stripe cannot
  send our token.
"""
from __future__ import annotations

import hmac
import logging
import secrets
from hashlib import sha256

from flask import abort, current_app, g, request, session

logger = logging.getLogger(__name__)

SESSION_KEY = "_csrf"
FORM_FIELD = "csrf_token"
HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Endpoints that must not be CSRF-checked, by endpoint name. Kept deliberately
# short — every entry is a hole, so each one carries its reason.
EXEMPT_ENDPOINTS = frozenset({
    # Stripe signs its deliveries and has no session to carry a token.
    "billing.webhook",
})


def _secret() -> bytes:
    return current_app.config["SECRET_KEY"].encode()


def issue() -> str:
    """Return this session's CSRF token, minting one on first use."""
    raw = session.get(SESSION_KEY)
    if not raw:
        raw = secrets.token_urlsafe(32)
        session[SESSION_KEY] = raw
        session.permanent = True
    return hmac.new(_secret(), raw.encode(), sha256).hexdigest()


def _submitted() -> str | None:
    return (
        request.form.get(FORM_FIELD)
        or request.headers.get(HEADER)
        or request.args.get(FORM_FIELD)
    )


def validate() -> None:
    """before_request hook. Rejects unsafe requests without a valid token."""
    if request.method in SAFE_METHODS:
        return
    if request.endpoint in EXEMPT_ENDPOINTS:
        return
    if current_app.config.get("WTF_CSRF_ENABLED") is False:
        # The test harness sets this, matching the flag it already used before
        # any CSRF existed. Production never sets it.
        return

    expected = session.get(SESSION_KEY)
    submitted = _submitted()

    if not expected or not submitted:
        logger.warning("CSRF: missing token on %s %s", request.method, request.path)
        abort(400, "Your session expired. Reload the page and try again.")

    if not hmac.compare_digest(
        hmac.new(_secret(), expected.encode(), sha256).hexdigest(), submitted
    ):
        logger.warning("CSRF: bad token on %s %s", request.method, request.path)
        abort(400, "Your session expired. Reload the page and try again.")


def init_app(app) -> None:
    """Install the hook and expose the token to templates."""
    app.before_request(validate)

    @app.context_processor
    def _inject():
        # A callable, not a value: minting a token on every GET would write a
        # session cookie to anonymous visitors reading the landing page.
        return {"csrf_token": issue}
