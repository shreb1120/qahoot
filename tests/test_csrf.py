"""CSRF protection.

Before this, every POST in the app was defended only by SameSite=Lax. Adding
endpoints that change a subscription made that untenable — a forged request
that cancels someone's plan is a different class of problem from one that
renames an agent.

The rest of the suite runs with CSRF off (the harness sets WTF_CSRF_ENABLED,
matching the flag it already had), so these tests turn it back on explicitly.
Otherwise the protection would be shipped and never once exercised.
"""
import io

import pytest

import csrf


@pytest.fixture
def enforced(app):
    """Run with CSRF actually switched on."""
    app.config["WTF_CSRF_ENABLED"] = True
    yield
    app.config["WTF_CSRF_ENABLED"] = False


@pytest.fixture(autouse=True)
def _no_pipeline(monkeypatch):
    import pipeline
    monkeypatch.setattr(pipeline, "spawn", lambda **kw: None)


def _token(actor):
    """Pull a real token out of a rendered page, the way a browser would."""
    import re
    body = actor.get("/agents/").get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([0-9a-f]+)"', body)
    assert m, "no CSRF token rendered into the agents page"
    return m.group(1)


# ────────────────────────────── enforcement ──────────────────────────────

def test_a_post_without_a_token_is_rejected(tenants, enforced):
    r = tenants.a_admin.post("/agents/add", data={"name": "Forged"})
    assert r.status_code == 400


def test_a_post_with_a_forged_token_is_rejected(tenants, enforced):
    r = tenants.a_admin.post("/agents/add",
                             data={"name": "Forged", "csrf_token": "deadbeef" * 8})
    assert r.status_code == 400


def test_a_post_with_the_real_token_succeeds(tenants, enforced, db):
    """The protection has to let real work through, or it is just an outage."""
    from models import Agent
    token = _token(tenants.a_admin)
    r = tenants.a_admin.post("/agents/add",
                             data={"name": "Real Person", "csrf_token": token})
    assert r.status_code == 302
    db.expire_all()
    assert db.query(Agent).filter_by(
        org_id=tenants.a["org"], name="Real Person").count() == 1


def test_a_token_from_one_session_is_useless_in_another(app, tenants, enforced):
    """The whole scheme rests on this: the token is derived from the signed
    session, so an attacker who sees a token but has no session cookie cannot
    use it.

    Deliberately builds a second test client. The `tenants` actors all share
    one client and therefore one cookie jar, which is a harness artifact — two
    real users are two browsers, and that is what is modelled here.
    """
    from tests.conftest import Actor
    stolen = _token(tenants.a_admin)

    other_browser = Actor(app.test_client(),
                          tenants.b["admin"], tenants.b["org"])
    r = other_browser.post("/agents/add",
                           data={"name": "Cross", "csrf_token": stolen})
    assert r.status_code == 400


def test_a_rejection_tells_the_user_what_to_do(tenants, enforced):
    r = tenants.a_admin.post("/agents/add", data={"name": "x"})
    assert "Reload the page" in r.get_data(as_text=True)


# ─────────────────────────────── coverage ───────────────────────────────

@pytest.mark.parametrize("path,data", [
    ("/agents/add", {"name": "x"}),
    ("/agents/import", {}),
    ("/org/invite", {}),
])
def test_state_changing_routes_are_all_covered(tenants, enforced, path, data):
    """Protection installed as one before_request hook rather than per-route,
    because the failure mode of per-route decorators is the one you forget."""
    assert tenants.a_admin.post(path, data=data).status_code == 400


def test_uploads_are_covered_too(tenants, enforced):
    r = tenants.a_admin.post(
        "/calls/upload",
        data={"file": (io.BytesIO(b"ID3"), "c.mp3")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


def test_reads_are_never_blocked(tenants, enforced):
    """A GET must never need a token — including for a signed-out visitor,
    who has no session to carry one."""
    for path in ("/", "/calls/", "/agents/", "/auth/login"):
        assert tenants.a_admin.get(path).status_code in (200, 302)


def test_the_stripe_webhook_is_exempt(app):
    """Stripe signs its deliveries and has no session. It is the only
    exemption, and it is explicit rather than implied by a missing decorator."""
    assert "billing.webhook" in csrf.EXEMPT_ENDPOINTS
    assert len(csrf.EXEMPT_ENDPOINTS) == 1, (
        "every exemption is a hole — add one only with a reason in the set"
    )


def test_an_anonymous_visitor_gets_no_session_cookie_from_reading(app, anon, enforced):
    """The token is minted lazily. Rendering a public page must not set a
    cookie on someone who has done nothing — that is a consent question as much
    as a technical one."""
    r = anon.get("/auth/login")
    assert "Set-Cookie" not in r.headers or "session=" not in r.headers.get("Set-Cookie", "")
