"""History filtering and pagination, and the branded error pages."""
import pytest


def test_filters_narrow_the_list(tenants):
    r = tenants.a_admin.get("/calls/?status=complete")
    assert r.status_code == 200 and "acme-call.mp3" in r.get_data(as_text=True)
    r = tenants.a_admin.get("/calls/?status=pending")
    assert "acme-call.mp3" not in r.get_data(as_text=True)


def test_result_filter_matches_the_verdict(tenants):
    """Acme's call is CRITICAL FAIL, Borden's is PASS."""
    assert "acme-call.mp3" in tenants.a_admin.get("/calls/?result=critical").get_data(as_text=True)
    assert "acme-call.mp3" not in tenants.a_admin.get("/calls/?result=pass").get_data(as_text=True)
    assert "borden-call.mp3" in tenants.b_admin.get("/calls/?result=pass").get_data(as_text=True)


def test_phone_filter(tenants):
    assert "acme-call.mp3" in tenants.a_admin.get("/calls/?phone=8005").get_data(as_text=True)
    assert "acme-call.mp3" not in tenants.a_admin.get("/calls/?phone=9999").get_data(as_text=True)


def test_no_matches_says_so_rather_than_looking_empty(tenants):
    body = tenants.a_admin.get("/calls/?phone=0000000").get_data(as_text=True)
    assert "No calls match these filters" in body


@pytest.mark.parametrize("page", ["0", "-3", "abc", "99999"])
def test_pagination_survives_a_hostile_page_number(tenants, page):
    assert tenants.a_admin.get(f"/calls/?page={page}").status_code == 200


def test_an_invalid_date_filter_does_not_500(tenants):
    assert tenants.a_admin.get("/calls/?date_from=not-a-date&date_to=xx").status_code == 200


def test_404_is_branded_and_not_werkzeugs_default(anon):
    r = anon.get("/no-such-page")
    body = r.get_data(as_text=True)
    assert r.status_code == 404
    assert "Error 404" in body and "couldn" in body
    assert "<title>404 Not Found</title>" not in body


def test_json_requests_get_json_errors(anon):
    r = anon.get("/calls/nope.json")
    assert r.status_code in (401, 404)
    assert r.mimetype == "application/json"


def test_401_page_is_branded(tenants, client):
    """No identity injected + an XHR header takes the api branch."""
    r = client.get("/calls/active.json", headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 401
    assert r.mimetype == "application/json"


def test_report_survives_a_malformed_report_json(tenants, db):
    """The grader is an LLM; the report shape is not guaranteed."""
    from models import Call
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    call.report.report_json = {"final_determination": "FAIL"}   # no sections, no phrases
    db.commit()
    r = tenants.a_admin.get(f"/calls/{tenants.a['call']}/report")
    assert r.status_code == 200
    assert "No checklist results" in r.get_data(as_text=True)
