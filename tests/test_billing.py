"""Billing: the plan page, the webhook, and overage.

The webhook is the sharpest edge in this codebase. It is a public,
unauthenticated-by-session endpoint that writes subscription state, exempt from
CSRF by necessity. Two things therefore have to be true and stay true:

* an unsigned request cannot change anything, and
* a replayed delivery cannot be processed twice.

Stripe retries on any non-2xx, so the second is not hypothetical — it happens
every time our handler is slow.
"""
import json
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import usage
from models import StripeEvent, Subscription, UsagePeriod


# ─────────────────────────────── plan page ───────────────────────────────

@pytest.fixture
def stripe_configured(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_fake")


def test_the_plan_page_shows_trial_usage(tenants, db):
    from models import Organization
    o = db.get(Organization, tenants.a["org"])
    usage.record(db, org_id=o.id, call_id=None, resource_type=usage.TRANSCRIPTION,
                 idempotency_key="p:transcription:1", billable_seconds=40 * 60)
    db.commit()

    body = tenants.a_admin.get("/billing/").get_data(as_text=True)
    assert "40" in body and "100" in body
    assert "trial minutes left" in body


def test_the_plan_page_explains_overage_rather_than_warning_vaguely(tenants, db):
    from models import Organization
    o = db.get(Organization, tenants.a["org"])
    db.add(Subscription(org_id=o.id, plan_code="starter", status="active",
                        included_minutes=2000, overage_micros_per_minute=180_000))
    db.commit()
    usage.record(db, org_id=o.id, call_id=None, resource_type=usage.TRANSCRIPTION,
                 idempotency_key="o:transcription:1", billable_seconds=2500 * 60)
    db.commit()

    body = tenants.a_admin.get("/billing/").get_data(as_text=True)
    assert "500 minutes over" in body
    assert "$0.18" in body
    assert "uploads keep working" in body


def test_a_member_cannot_change_the_plan(tenants, stripe_configured):
    """Only meaningful once there are plans to choose between — with billing
    unconfigured the page says so instead, which is the more useful answer."""
    body = tenants.a_member.get("/billing/").get_data(as_text=True)
    assert "Only admins can change the plan" in body
    assert 'action="/billing/checkout"' not in body


def test_billing_not_configured_says_so_rather_than_showing_dead_buttons(
        tenants, monkeypatch):
    """Must control its own environment. This read the ambient .env, so it
    passed on a machine without Stripe keys and failed the moment real ones
    were added — the test was measuring the developer's setup, not the code."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    body = tenants.a_admin.get("/billing/").get_data(as_text=True)
    assert "Billing isn't switched on yet" in body
    assert "nothing will be charged" in body


def test_one_orgs_billing_page_never_shows_anothers_usage(tenants, db):
    from models import Organization
    a = db.get(Organization, tenants.a["org"])
    usage.record(db, org_id=a.id, call_id=None, resource_type=usage.TRANSCRIPTION,
                 idempotency_key="x:transcription:1", billable_seconds=9999 * 60)
    db.commit()

    body = tenants.b_admin.get("/billing/").get_data(as_text=True)
    assert "9,999" not in body


# ─────────────────────────────── webhook ───────────────────────────────

def _event(kind, obj, event_id="evt_test_1"):
    return {"id": event_id, "type": kind, "data": {"object": obj}}


def _post_event(client, event, *, verified=True):
    """Post a webhook, controlling whether signature verification passes."""
    import blueprints.billing_bp as bp

    def construct(payload, sig, secret):
        if not verified:
            raise ValueError("Invalid signature")
        return event

    with patch.object(bp, "_stripe") as fake:
        fake.return_value.Webhook.construct_event.side_effect = construct
        return client.post("/billing/webhook", data=json.dumps(event),
                           content_type="application/json",
                           headers={"Stripe-Signature": "t=1,v1=x"})


def test_an_unsigned_webhook_changes_nothing(anon, db, tenants, stripe_configured):
    """The signature is the *only* authentication on this endpoint. If it can
    be bypassed, anyone on the internet can give themselves a plan."""
    event = _event("customer.subscription.updated", {
        "id": "sub_1", "status": "active", "customer": "cus_1",
        "metadata": {"org_id": tenants.a["org"], "plan_code": "growth"},
    })
    r = _post_event(anon, event, verified=False)
    assert r.status_code == 400
    assert db.get(Subscription, tenants.a["org"]) is None


def test_a_valid_webhook_records_the_subscription(anon, db, tenants, stripe_configured):
    event = _event("customer.subscription.updated", {
        "id": "sub_1", "status": "active", "customer": "cus_1",
        "current_period_start": 1754611200, "current_period_end": 1757289600,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_growth"}}]},
        "metadata": {"org_id": tenants.a["org"], "plan_code": "growth"},
    })
    assert _post_event(anon, event).status_code == 200

    db.expire_all()
    sub = db.get(Subscription, tenants.a["org"])
    assert sub.status == "active"
    assert sub.plan_code == "growth"
    assert sub.included_minutes == 8000        # taken from plans.py, not Stripe
    assert sub.stripe_price_id == "price_growth"


def test_a_replayed_event_is_not_processed_twice(anon, db, tenants, stripe_configured):
    """Stripe retries on any non-2xx. Re-running a handler could open a second
    billing period or post a duplicate invoice item."""
    event = _event("customer.subscription.updated", {
        "id": "sub_1", "status": "active", "customer": "cus_1",
        "metadata": {"org_id": tenants.a["org"], "plan_code": "starter"},
    })
    assert _post_event(anon, event).status_code == 200

    # Same event id, but claiming a different plan — if it were reprocessed the
    # subscription would move, which is exactly the corruption to prevent.
    event["data"]["object"]["metadata"]["plan_code"] = "growth"
    assert _post_event(anon, event).status_code == 200

    db.expire_all()
    assert db.get(Subscription, tenants.a["org"]).plan_code == "starter"
    assert db.query(StripeEvent).filter_by(id="evt_test_1").count() == 1


def test_a_distinct_event_is_processed(anon, db, tenants, stripe_configured):
    """The replay guard must key on the event id, not swallow everything."""
    for i, plan in enumerate(("starter", "growth")):
        _post_event(anon, _event("customer.subscription.updated", {
            "id": "sub_1", "status": "active", "customer": "cus_1",
            "metadata": {"org_id": tenants.a["org"], "plan_code": plan},
        }, event_id=f"evt_{i}"))

    db.expire_all()
    assert db.get(Subscription, tenants.a["org"]).plan_code == "growth"


def test_a_cancellation_is_recorded_and_blocks_uploads(anon, db, tenants, stripe_configured):
    _post_event(anon, _event("customer.subscription.deleted", {
        "id": "sub_1", "status": "canceled", "customer": "cus_1",
        "metadata": {"org_id": tenants.a["org"], "plan_code": "growth"},
    }))
    db.expire_all()

    from models import Organization
    with pytest.raises(usage.Blocked):
        usage.check_can_upload(db, db.get(Organization, tenants.a["org"]))


def test_a_webhook_for_an_unknown_org_is_acknowledged_not_retried(
        anon, db, tenants, stripe_configured):
    """Returning 500 would make Stripe retry forever on an event we can never
    handle. It is acknowledged and logged instead."""
    r = _post_event(anon, _event("customer.subscription.updated", {
        "id": "sub_x", "status": "active", "customer": "cus_unknown",
        "metadata": {},
    }))
    assert r.status_code == 200


def test_the_webhook_is_exempt_from_csrf_but_nothing_else_is(app):
    import csrf
    assert csrf.EXEMPT_ENDPOINTS == {"billing.webhook"}


# ─────────────────────────────── overage ───────────────────────────────

def test_overage_is_computed_from_our_ledger(tenants, db):
    """Charged from usage_events, not from anything Stripe tracks — the ledger
    is the auditable record and the thing a customer can be shown."""
    from plans import PLANS_BY_CODE, overage_micros
    starter = PLANS_BY_CODE["starter"]

    assert overage_micros(starter, 1_500) == 0            # under
    assert overage_micros(starter, 2_000) == 0            # exactly at
    assert overage_micros(starter, 2_500) == 500 * 180_000

    growth = PLANS_BY_CODE["growth"]
    assert overage_micros(growth, 9_000) == 1_000 * 120_000


def test_money_is_integers_all_the_way_down():
    """Floats and money do not mix: $0.12 has no exact binary form, and a
    per-minute rate times thousands of minutes accumulates that into dollars."""
    from plans import PLANS
    for plan in PLANS:
        assert isinstance(plan.price_micros, int)
        assert isinstance(plan.overage_micros_per_minute, int)


def test_a_plan_without_a_stripe_price_cannot_be_bought(monkeypatch):
    """Guards against shipping a pricing page whose buttons 500."""
    from plans import PLANS_BY_CODE
    monkeypatch.delenv("STRIPE_PRICE_GROWTH", raising=False)
    assert PLANS_BY_CODE["growth"].is_purchasable is False

    monkeypatch.setenv("STRIPE_PRICE_GROWTH", "price_123")
    assert PLANS_BY_CODE["growth"].is_purchasable is True
