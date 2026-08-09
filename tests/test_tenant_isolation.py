"""Tenant isolation — PRODUCT.md's first principle.

  "Org A must never reach Org B's calls, transcripts, reports, or checklist
   through any route, including direct API calls — not just what the UI shows."

Every test here requests another organization's resource *by id*, the way a
curl would, never by clicking through the UI.
"""
import pytest


def _id_routes(app, param):
    """Every rule taking this id param, straight from the app's own url_map, so
    a route added later is covered without anyone remembering to add a test."""
    out = []
    for rule in app.url_map.iter_rules():
        if param in rule.arguments and "static" not in rule.endpoint:
            methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
            out.append((rule.rule, rule.endpoint, methods[0]))
    return sorted(out)


DENIED = {401, 403, 404}


def test_the_route_inventory_is_not_empty(app):
    """Guards the two tests below: if the derivation breaks they would pass vacuously."""
    assert len(_id_routes(app, "call_id")) >= 6
    assert len(_id_routes(app, "agent_id")) >= 1


def test_no_call_route_serves_another_orgs_call(app, tenants):
    """The core assertion. B's admin asks for A's call on every call route."""
    leaks = []
    for path, endpoint, method in _id_routes(app, "call_id"):
        url = path.replace("<call_id>", tenants.a["call"])
        resp = getattr(tenants.b_admin, method.lower())(url)
        if resp.status_code not in DENIED:
            leaks.append(f"{method} {path} -> {resp.status_code} ({endpoint})")
    assert not leaks, "Cross-org access allowed on:\n  " + "\n  ".join(leaks)


def test_no_agent_route_serves_another_orgs_agent(app, tenants):
    leaks = []
    for path, endpoint, method in _id_routes(app, "agent_id"):
        url = path.replace("<agent_id>", tenants.a["agent"])
        resp = getattr(tenants.b_admin, method.lower())(url)
        if resp.status_code not in DENIED:
            leaks.append(f"{method} {path} -> {resp.status_code} ({endpoint})")
    assert not leaks, "Cross-org agent access allowed on:\n  " + "\n  ".join(leaks)


def test_own_call_is_reachable(tenants):
    """Negative control: the isolation tests would pass if everything 404'd."""
    assert tenants.a_admin.get(f"/calls/{tenants.a['call']}/report").status_code == 200
    assert tenants.b_admin.get(f"/calls/{tenants.b['call']}/report").status_code == 200


def test_report_body_never_contains_the_other_orgs_content(tenants):
    """Not just the status code — the other tenant's text must not appear."""
    body = tenants.a_admin.get(f"/calls/{tenants.a['call']}/report").get_data(as_text=True)
    assert "Acme evidence quote" in body          # own content present
    assert "Borden" not in body                    # nothing of the other tenant


def test_history_lists_only_your_own_calls(tenants):
    a_body = tenants.a_admin.get("/calls/").get_data(as_text=True)
    b_body = tenants.b_admin.get("/calls/").get_data(as_text=True)
    assert "acme-call.mp3" in a_body and "borden-call.mp3" not in a_body
    assert "borden-call.mp3" in b_body and "acme-call.mp3" not in b_body


def test_active_json_is_scoped(tenants):
    r = tenants.b_admin.get("/calls/active.json", headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 200
    assert tenants.a["call"] not in r.get_data(as_text=True)


def test_audio_of_another_org_is_not_served(tenants):
    r = tenants.b_admin.get(f"/calls/{tenants.a['call']}/audio")
    assert r.status_code in DENIED
    assert b"Acme" not in r.data


def test_override_cannot_be_written_to_another_orgs_report(tenants, db):
    from models import Call
    r = tenants.b_admin.post(
        f"/calls/{tenants.a['call']}/override",
        json={"section_key": "approval_script", "item_name": "Disclose fees", "status": "approved"},
    )
    assert r.status_code in DENIED
    db.expire_all()
    call = db.query(Call).filter_by(id=tenants.a["call"]).first()
    assert not (call.report.overrides_json or {}), "another org wrote an override onto this report"


def test_dashboard_counts_only_your_own_org(tenants):
    """The dashboard aggregates were added late and are the easiest place to
    accidentally count across tenants."""
    body = tenants.a_admin.get("/").get_data(as_text=True)
    assert "Acme" in body and "Borden Agent" not in body


def _agent_performance_table(html):
    """Isolate the Agent performance table. The agent's name also appears in the
    Needs Review panel above it, so a page-wide search reads the wrong region."""
    import re
    m = re.search(r"Agent performance.*?</table>", html, re.S)
    return m.group(0) if m else ""


def test_agent_performance_does_not_count_another_orgs_call(tenants, db):
    """agent_stats reaches Calls through an Agent join. If only Agent.org_id is
    scoped, a call carrying another org's id but pointing at this org's agent is
    counted. Build exactly that row and assert it is excluded."""
    import re
    import uuid
    from datetime import date
    from models import Call, Report

    contaminated = Call(
        id=str(uuid.uuid4()),
        org_id=tenants.b["org"],          # belongs to Borden
        agent_id=tenants.a["agent"],      # but points at Acme's agent
        compliance_profile_id=None, uploaded_by_user_id=tenants.b["admin"],
        internal_id="XORG-1", call_date=date(2026, 8, 1),
        filename="contaminated.mp3", status="complete", duration=120,
    )
    db.add(contaminated)
    db.flush()
    db.add(Report(id=str(uuid.uuid4()), call_id=contaminated.id,
                  pass_fail_status="PASS", report_json={"final_determination": "PASS"}))
    db.commit()

    table = _agent_performance_table(tenants.a_admin.get("/").get_data(as_text=True))
    assert "Acme Agent" in table, "agent performance row not rendered"
    assert "Borden" not in table, "another org's agent appeared in this org's table"

    # Read the Graded column by position, not by CSS class — a styling rename
    # should not be able to turn this assertion off. (It did once: the class was
    # text-slate-600 until the ramp moved onto tokens.)
    row = re.search(r"<tr>(?:(?!</tr>).)*?Acme Agent.*?</tr>", table, re.S)
    assert row, "agent performance row not found"
    cells = [re.sub(r"<[^>]+>", " ", c).strip()
             for c in re.findall(r"<td[^>]*>(.*?)</td>", row.group(0), re.S)]
    assert len(cells) >= 4, f"unexpected column count: {cells}"
    graded = cells[2].split()[0]          # Agent | Pass rate | Graded | Critical
    assert graded == "1", (
        f"another org's call was counted into this org's agent performance "
        f"(graded={graded}, expected 1). Row cells: {cells}"
    )


def test_checklist_shows_only_your_own_profile(tenants):
    a = tenants.a_admin.get("/profile/").get_data(as_text=True)
    assert "Acme checklist" in a
    assert "Borden checklist" not in a


def test_checklist_mutation_hits_only_your_own_profile(tenants, db):
    """Section indices are positional; org scoping must come from the session,
    never from the submitted index."""
    from models import ComplianceProfile

    def sections(org_id):
        db.expire_all()
        p = db.query(ComplianceProfile).filter_by(org_id=org_id).first()
        return len(p.script_sections_json["sections"])

    a_before, b_before = sections(tenants.a["org"]), sections(tenants.b["org"])
    r = tenants.a_admin.post("/profile/sections/add", data={"name": "Injected"})

    # Guard against a vacuous pass: if the URL were wrong this would 404 and the
    # assertion below would hold for entirely the wrong reason.
    assert r.status_code in (200, 302), f"the mutation never ran ({r.status_code})"
    assert sections(tenants.a["org"]) == a_before + 1, "the admin's own edit did not apply"
    assert sections(tenants.b["org"]) == b_before, \
        "one org's checklist edit changed another org's profile"
