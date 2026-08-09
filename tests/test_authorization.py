"""Role and session gates: anonymous, member, admin."""
import pytest

ADMIN_ONLY_POSTS = [
    ("/profile/sections/add", {"name": "X"}),
    ("/agents/add", {"name": "X"}),
]


# Reachable without signing in. Short and explicit: adding a route here is a
# decision to expose it to the whole internet, so it should be a deliberate
# edit rather than something that happens by forgetting a decorator.
PUBLIC_PATHS = [
    "/", "/pricing",
    "/auth/login", "/auth/signup",
    # Signing out has to work for someone whose session already expired —
    # gating it would strand them on a page they cannot leave.
    "/auth/logout",
]

APP_PATHS = ["/dashboard", "/calls/", "/calls/upload", "/profile/", "/agents/",
             "/org/settings", "/billing/"]


def test_anonymous_is_redirected_from_every_app_page(anon):
    for path in APP_PATHS:
        r = anon.get(path)
        assert r.status_code in (301, 302), f"{path} served content to an anonymous visitor"
        assert "/auth/login" in r.headers.get("Location", ""), path


def test_public_pages_are_reachable_without_an_account(anon):
    """The landing page has to work for someone who has never seen the product.
    Redirecting it to login would make the marketing site unreachable."""
    for path in PUBLIC_PATHS:
        assert anon.get(path).status_code == 200, f"{path} was not public"


def test_every_route_is_either_public_or_gated(app, anon):
    """Catches a new GET route that forgets its decorator — the failure mode
    where a page ships readable by anyone and nobody notices."""
    leaked = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.rule.startswith("/static"):
            continue
        if "<" in rule.rule:                      # needs an id; covered elsewhere
            continue
        if rule.rule in PUBLIC_PATHS:
            continue
        r = anon.get(rule.rule)
        if r.status_code == 200:
            leaked.append(rule.rule)
    assert not leaked, (
        "these GET routes served an anonymous visitor and are not in "
        f"PUBLIC_PATHS: {leaked}"
    )


def test_anonymous_api_calls_get_401_not_a_redirect(anon):
    r = anon.get("/calls/active.json", headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 401


def test_member_cannot_perform_admin_mutations(tenants):
    for path, data in ADMIN_ONLY_POSTS:
        r = tenants.a_member.post(path, data=data)
        assert r.status_code == 403, f"member was allowed to POST {path} ({r.status_code})"


def test_admin_can_perform_admin_mutations(tenants):
    """Negative control for the test above."""
    r = tenants.a_admin.post("/agents/add", data={"name": "New Agent"})
    assert r.status_code in (200, 302)


def test_member_cannot_delete_an_agent(tenants, db):
    from models import Agent
    r = tenants.a_member.post(f"/agents/{tenants.a['agent']}/delete")
    assert r.status_code == 403
    db.expire_all()
    assert db.query(Agent).filter_by(id=tenants.a["agent"]).first() is not None


def test_member_can_still_read_and_upload(tenants):
    """Members are reviewers — the role gate must not lock them out of the job."""
    assert tenants.a_member.get("/calls/").status_code == 200
    assert tenants.a_member.get("/calls/upload").status_code == 200
    assert tenants.a_member.get(f"/calls/{tenants.a['call']}/report").status_code == 200


def test_member_sees_read_only_checklist(tenants):
    body = tenants.a_member.get("/profile/").get_data(as_text=True)
    assert "Read only" in body
    assert "Delete section" not in body


def test_team_settings_is_admin_only(tenants):
    assert tenants.a_admin.get("/org/settings").status_code == 200
    r = tenants.a_member.get("/org/settings")
    assert r.status_code in (302, 403)
