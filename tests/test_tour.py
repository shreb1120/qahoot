"""The public product walkthrough.

Served to anonymous visitors, which is the entire point — every call to action
on the site used to require an account first. That also makes it the one page
where a data leak would be maximally bad, so the first group of tests is about
what must never appear here.
"""
import demo_tour


def test_it_is_public(anon):
    assert anon.get("/tour").status_code == 200


def test_it_renders_the_real_report_template(anon):
    """A tour built from screenshots, or from a copy of the markup, drifts the
    first time the product changes. This one includes the same partial the app
    renders, so it cannot."""
    body = anon.get("/tour").get_data(as_text=True)
    for marker in ("rp-transport", "rp-track", "rp-verdict", "rp-panel"):
        assert marker in body, f"{marker} missing — the tour is not the real report"


def test_no_customer_data_can_reach_it(anon, tenants, db):
    """The fixture is a plain object, not a database row. Nothing on this page
    should come from a tenant."""
    body = anon.get("/tour").get_data(as_text=True)
    from models import Agent, Call, Organization
    for org in db.query(Organization).all():
        assert org.name not in body, f"org {org.name!r} leaked onto the public tour"
    for agent in db.query(Agent).all():
        assert agent.name not in body
    for call in db.query(Call).all():
        if call.internal_id:
            assert call.internal_id not in body
        assert call.filename not in body


def test_the_audio_is_the_synthetic_file_not_a_real_recording(anon):
    """The per-call audio route is authenticated and correctly 404s for an
    anonymous visitor, so the demo must not reference it — it would render a
    dead player."""
    body = anon.get("/tour").get_data(as_text=True)
    assert demo_tour.DEMO_AUDIO_REL.split("/")[-1] in body
    assert "/calls/walkthrough/audio" not in body


def test_the_narration_is_present_without_javascript(anon):
    """Steps are rendered server-side, so the story is readable to a crawler and
    to anyone with JS blocked."""
    body = anon.get("/tour").get_data(as_text=True)
    assert "Dana" in body
    for step in demo_tour.STEPS[:3]:
        assert step["title"] in body


def test_every_step_targets_something_real(anon):
    """A step pointing at a selector that no longer exists would spotlight empty
    space. The runtime drops those, but a step list where most have gone stale
    means the tour has quietly hollowed out."""
    body = anon.get("/tour").get_data(as_text=True)
    anchored = [s for s in demo_tour.STEPS if s.get("target")]
    missing = []
    for s in anchored:
        sel = s["target"].split(",")[0].strip()
        token = sel.lstrip(".#").split("[")[0].split(":")[0]
        if token and token not in body:
            missing.append(s["target"])
    assert len(missing) <= 1, f"tour steps point at markup that is gone: {missing}"


def test_the_demo_report_exercises_the_interesting_states(anon):
    """The walkthrough is only worth taking if it shows the things that are hard
    to explain in prose: a miss, an unassessed item, a disclaimed phrase, and a
    program flip."""
    r = demo_tour._report_json()
    statuses = {i["status"] for s in r["sections"] for i in s["items"]}
    assert {"covered", "missed", "not_assessed"} <= statuses
    assert r["program_flip"]["detected"] is True
    phrases = r["auto_fail_phrases"]["phrases"]
    assert any(p["spoken_by"] == "client" for p in phrases)
    assert any(p["spoken_by"] == "agent" and not p["is_violation"] for p in phrases)
    assert r["auto_fail_phrases"]["detected"] is False, \
        "a demo that opens on CRITICAL FAIL misrepresents the common case"


def test_the_audio_generator_is_idempotent(tmp_path):
    """Called on every request. It must write once and then be a no-op."""
    first = demo_tour.ensure_audio(str(tmp_path))
    p = tmp_path / first
    stamp = p.stat().st_mtime_ns
    assert demo_tour.ensure_audio(str(tmp_path)) == first
    assert p.stat().st_mtime_ns == stamp, "regenerated the audio on a second call"
