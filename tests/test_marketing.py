"""The public marketing pages and the in-app usage surfaces.

The landing page is the only part of this app a stranger sees, and the one
place where a wrong claim is a business problem rather than a bug. Two things
are pinned here: it renders for someone with no account, and its prices come
from the same module Checkout charges from.
"""
import re

import pytest

import usage
from plans import PLANS_BY_CODE, TRIAL


# ───────────────────────────── public access ─────────────────────────────

def test_the_landing_page_renders_for_a_stranger(anon):
    """Asserts the page's furniture, not its prose. Copy gets rewritten; a test
    that pins a sentence just breaks every time someone improves one."""
    r = anon.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Start free" in body                  # the primary CTA
    assert "/auth/signup" in body                # that actually goes somewhere
    assert "Pricing" in body


def test_the_landing_page_does_not_render_the_app_shell(anon):
    """It extends the same base template; the sidebar must not leak into a
    page seen by someone with no org."""
    body = anon.get("/").get_data(as_text=True)
    assert 'id="sidebar"' not in body
    assert "Sign out" not in body


def test_a_signed_in_customer_gets_their_dashboard_not_a_sales_pitch(tenants):
    r = tenants.a_admin.get("/")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/dashboard")


def test_pricing_is_public_and_linkable(anon):
    assert anon.get("/pricing").status_code == 200


# ─────────────────── prices come from one place only ───────────────────

def test_the_landing_page_prices_match_plans_py(anon):
    """A marketing page that disagrees with the charge is how you bill someone
    an amount they never saw. Both read plans.py; this asserts it."""
    body = anon.get("/").get_data(as_text=True)
    for code in ("starter", "growth"):
        plan = PLANS_BY_CODE[code]
        assert f"${plan.price_dollars:,.0f}" in body, f"{code} price missing"
        assert plan.name in body


def test_changing_a_price_changes_the_page(anon, monkeypatch):
    """Negative control: if the page hardcoded prices the assertion above
    would pass while the page was wrong."""
    import plans
    original = plans.PLANS_BY_CODE["starter"]
    monkeypatch.setitem(
        plans.PLANS_BY_CODE, "starter",
        type(original)(**{**original.__dict__, "price_micros": 12_345 * 1_000_000}),
    )
    monkeypatch.setattr(plans, "PLANS", (plans.PLANS_BY_CODE["trial"],
                                         plans.PLANS_BY_CODE["starter"],
                                         plans.PLANS_BY_CODE["growth"]))
    body = anon.get("/").get_data(as_text=True)
    assert "$12,345" in body


def test_the_trial_allowance_is_stated_consistently(anon):
    body = anon.get("/").get_data(as_text=True)
    assert str(TRIAL.included_minutes) in body


# ──────────────────────── claims we must not make ────────────────────────

def test_the_page_makes_no_fabricated_social_proof(anon):
    """A compliance product that oversells itself on its own marketing page
    has a credibility problem before the first demo."""
    body = anon.get("/").get_data(as_text=True).lower()
    for claim in ("trusted by", "customers say", "join thousands",
                  "soc 2", "hipaa compliant", "iso 27001",
                  "as featured in", "5-star"):
        assert claim not in body, f"unsupported claim on the landing page: {claim!r}"


@pytest.mark.parametrize("page", ["/", "/pricing"])
def test_public_pages_never_name_our_vendors_or_stack(anon, page):
    """A prospect should read what the product does, not what it's built on.
    Naming the transcription and scoring vendors tells a competitor how to
    rebuild it and tells a customer nothing they can use."""
    body = anon.get(page).get_data(as_text=True).lower()
    for term in ("assemblyai", "anthropic", "claude", "openai", "gpt",
                 "stripe", "clerk", "postgres", "flask", "whisper"):
        assert term not in body, f"{page} names our stack: {term!r}"


def test_public_pages_avoid_machine_facing_language(anon):
    """"The grader", "the model", "the AI" describe our implementation, not the
    customer's job. They also invite the wrong question — whether to trust a
    machine — instead of the right one, which is whether the evidence holds.

    Matched on word boundaries, not substrings: "llm" lives inside
    "enrollment", which is core vocabulary for a debt-settlement checklist. A
    check that flags legitimate copy is a check people learn to ignore.
    """
    body = anon.get("/").get_data(as_text=True).lower()
    for term in ("the grader", "the model", "our ai", "llm", "machine learning"):
        assert not re.search(rf"\b{re.escape(term)}\b", body), (
            f"machine-facing language on the landing page: {term!r}"
        )


def test_the_page_says_it_is_not_legal_advice(anon):
    """The templates are built from published rules, not legal counsel, and a
    compliance buyer is entitled to see that said plainly."""
    body = anon.get("/").get_data(as_text=True)
    assert "legal advice" in body.lower()


# ───────────────────────── in-app usage surfaces ─────────────────────────

def _org(db, tenants):
    from models import Organization
    return db.get(Organization, tenants.a["org"])


def _burn(db, org, minutes, key="s:transcription:1"):
    usage.record(db, org_id=org.id, call_id=None,
                 resource_type=usage.TRANSCRIPTION,
                 idempotency_key=key, billable_seconds=minutes * 60)
    db.commit()


def test_the_sidebar_shows_the_meter_on_every_app_page(tenants, db):
    used = 25
    _burn(db, _org(db, tenants), used)
    left = f"{TRIAL.included_minutes - used:,} min left"
    for path in ("/dashboard", "/calls/", "/agents/"):
        body = tenants.a_admin.get(path).get_data(as_text=True)
        assert "qb-meter" in body, f"no usage meter on {path}"
        assert left in body


def test_the_meter_never_appears_for_a_stranger(anon):
    assert "qb-meter" not in anon.get("/").get_data(as_text=True)


def test_the_upload_page_warns_only_once_it_matters(tenants, db):
    """A banner shown on every visit stops being read by the time it counts."""
    allowance = TRIAL.included_minutes
    org = _org(db, tenants)
    _burn(db, org, int(allowance * 0.5), "a:transcription:1")
    assert "minutes left" not in tenants.a_admin.get("/calls/upload").get_data(as_text=True)

    _burn(db, org, int(allowance * 0.35), "b:transcription:1")     # now ~85%
    body = tenants.a_admin.get("/calls/upload").get_data(as_text=True)
    assert "minutes left" in body and "in your trial" in body


def test_an_exhausted_trial_is_told_what_to_do_next(tenants, db):
    _burn(db, _org(db, tenants), TRIAL.included_minutes + 20)
    body = tenants.a_admin.get("/calls/upload").get_data(as_text=True)
    assert "free trial is used up" in body
    assert "/billing/" in body
    assert "stays put" in body


def test_a_broken_usage_summary_does_not_break_the_page(tenants, monkeypatch):
    """The meter decorates the page; it must never be able to take it down."""
    import usage as usage_mod
    monkeypatch.setattr(usage_mod, "summary_for",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    r = tenants.a_admin.get("/dashboard")
    assert r.status_code == 200
    assert "qb-meter" not in r.get_data(as_text=True)
