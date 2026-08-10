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
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from flask import g

import usage
from models import StripeEvent, Subscription, UsagePeriod


# ─────────────────────────────── plan page ───────────────────────────────

@pytest.fixture
def stripe_configured(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_SECRET)


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

TEST_SECRET = "whsec_testonly_do_not_use_anywhere_real"


def _event(kind, obj, event_id="evt_test_1"):
    """A realistically shaped Stripe event.

    `"object": "event"` is not decoration — the SDK reads it to tell a v1
    event from a v2 one, and its absence made an earlier version of this
    helper produce payloads Stripe would never send.
    """
    return {"id": event_id, "object": "event", "type": kind,
            "data": {"object": obj}}


def _sign(body: bytes, secret: str = TEST_SECRET) -> str:
    """Build a Stripe-Signature header exactly as Stripe does."""
    import hashlib, hmac, time
    ts = int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256)
    return f"t={ts},v1={mac.hexdigest()}"


def _post_event(client, event, *, verified=True):
    """Post a genuinely signed webhook and let the real verifier run.

    Deliberately does NOT mock construct_event. The previous version did, and
    returned a plain dict — but Stripe's SDK hands the route a StripeObject
    whose `.get()` raises. Every webhook 500'd in production while this suite
    stayed green, because the tests were the only place a dict ever appeared.
    """
    body = json.dumps(event).encode()
    sig = _sign(body) if verified else _sign(body, "whsec_wrong_secret")
    return client.post("/billing/webhook", data=body,
                       content_type="application/json",
                       headers={"Stripe-Signature": sig})


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


def test_a_portal_upgrade_is_read_from_the_price_not_stale_metadata(
        anon, db, tenants, stripe_configured, monkeypatch):
    """A customer who upgrades in Stripe's billing portal gets a new price on
    their subscription, but the plan_code we wrote at Checkout is never
    rewritten. Trusting metadata billed them against the old plan: on a Solo →
    Growth upgrade they were charged $799 and metered against 600 minutes, then
    invoiced overage on minutes their new plan already included.
    """
    monkeypatch.setenv("STRIPE_PRICE_GROWTH", "price_growth_live")

    _post_event(anon, _event("customer.subscription.updated", {
        "id": "sub_1", "status": "active", "customer": "cus_1",
        # The price says Growth; the metadata still says Solo.
        "items": {"data": [{"price": {"id": "price_growth_live"}}]},
        "metadata": {"org_id": tenants.a["org"], "plan_code": "solo"},
    }))

    db.expire_all()
    sub = db.get(Subscription, tenants.a["org"])
    assert sub.plan_code == "growth", "plan must come from the price"
    assert sub.included_minutes == 8000
    assert sub.overage_micros_per_minute == 120_000


def test_an_unrecognised_price_falls_back_rather_than_failing(
        anon, db, tenants, stripe_configured):
    """A price created outside plans.py — a one-off deal, a legacy plan — must
    not 500 the webhook and stall every later event behind it."""
    r = _post_event(anon, _event("customer.subscription.updated", {
        "id": "sub_1", "status": "active", "customer": "cus_1",
        "items": {"data": [{"price": {"id": "price_some_custom_deal"}}]},
        "metadata": {"org_id": tenants.a["org"], "plan_code": "starter"},
    }))
    assert r.status_code == 200
    db.expire_all()
    assert db.get(Subscription, tenants.a["org"]).plan_code == "starter"


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


# ─────────────── the payload shape Stripe actually sends ───────────────

def test_the_billing_period_is_read_from_the_subscription_item(anon, db, tenants,
                                                               stripe_configured):
    """Stripe moved current_period_start/end from the subscription onto the
    subscription *item* in API version 2025-06-30. On the version this account
    is pinned to the top-level fields are gone entirely, so the old read
    returned None on every webhook and no paid org ever had a period recorded.

    That is a billing defect, not a cosmetic one: usage.current_period() uses
    these dates to decide what "this period" means, so a customer who subscribed
    on the 10th had usage counted 1st-to-1st and was invoiced 10th-to-10th.

    The rest of this suite missed it because every fixture put the dates where
    Stripe used to put them. This one puts them where Stripe puts them now.
    """
    from plans import get_plan
    start, end = 1754611200, 1757289600
    event = _event("customer.subscription.updated", {
        "id": "sub_item_period", "status": "active", "customer": "cus_1",
        "metadata": {"org_id": tenants.a["org"]},
        "items": {"data": [{
            "price": {"id": get_plan("growth").stripe_price_id},
            "current_period_start": start,
            "current_period_end": end,
        }]},
    }, event_id="evt_item_period")

    assert _post_event(anon, event).status_code == 200
    db.expire_all()

    sub = db.get(Subscription, tenants.a["org"])
    assert sub is not None
    assert sub.current_period_start is not None, \
        "the billing period was not recorded — usage will fall back to the calendar month"
    assert int(sub.current_period_start.timestamp()) == start
    assert int(sub.current_period_end.timestamp()) == end


def test_a_legacy_top_level_period_still_resolves(anon, db, tenants, stripe_configured):
    """A replay of an event captured before Stripe moved the fields must still
    work — the fallback is not decorative."""
    start, end = 1754611200, 1757289600
    event = _event("customer.subscription.updated", {
        "id": "sub_legacy", "status": "active", "customer": "cus_1",
        "metadata": {"org_id": tenants.a["org"], "plan_code": "growth"},
        "current_period_start": start, "current_period_end": end,
    }, event_id="evt_legacy_period")

    assert _post_event(anon, event).status_code == 200
    db.expire_all()
    sub = db.get(Subscription, tenants.a["org"])
    assert int(sub.current_period_start.timestamp()) == start


# ═══════════════════ overage invoicing ═══════════════════
#
# This path had no tests and had never run against live Stripe. It is where the
# product actually collects money beyond the flat fee, so the three tests below
# are about the ways it could take the wrong amount — or none.

import time as _time


def _period(db, org_id, *, start, used, included, invoice_id=None):
    p = UsagePeriod(org_id=org_id, period_start=start,
                    period_end=start + timedelta(days=30),
                    included_seconds=included, billable_seconds=used,
                    stripe_invoice_id=invoice_id)
    db.add(p); db.commit()
    return p


def _invoice(org_customer="cus_1", invoice_id="in_1", created=None):
    return {"id": invoice_id, "customer": org_customer, "status": "draft",
            "created": int(created.timestamp()) if created else int(_time.time()),
            "period_start": 0, "period_end": 0}


class _RecordingStripe:
    """Captures InvoiceItem.create calls, and can be told to fail after Stripe
    has already applied one — the case an idempotency key exists for."""

    def __init__(self, explode_after=False):
        self.items = []
        self.explode_after = explode_after
        outer = self

        class InvoiceItem:
            @staticmethod
            def create(**kw):
                outer.items.append(kw)
                if outer.explode_after:
                    raise RuntimeError("connection reset after Stripe applied it")
                return {"id": f"ii_{len(outer.items)}"}

        self.InvoiceItem = InvoiceItem


def test_overage_is_billed_for_the_period_that_ended_not_the_one_now_open(
        anon, db, tenants, stripe_configured, monkeypatch):
    """Stripe guarantees no ordering between customer.subscription.updated and
    invoice.created. When the subscription advances first, "the current period"
    is the new, empty cycle — overage computes to zero and a month of real
    usage is silently never invoiced."""
    import blueprints.billing_bp as bb
    from plans import get_plan

    org_id = tenants.a["org"]
    db.add(Subscription(org_id=org_id, plan_code="growth", status="active",
                        stripe_customer_id="cus_1",
                        included_minutes=8000,
                        overage_micros_per_minute=get_plan("growth").overage_micros_per_minute))
    now = datetime.now(timezone.utc)
    ended = now - timedelta(days=31)
    _period(db, org_id, start=ended, used=9000 * 60, included=8000 * 60)   # 1000 min over
    # The subscription has already rolled to the new, empty cycle.
    _period(db, org_id, start=now - timedelta(minutes=5), used=0, included=8000 * 60)
    db.commit()

    fake = _RecordingStripe()
    monkeypatch.setattr(bb, "_stripe", lambda: fake)

    with anon.application.test_request_context():
        g.db = db
        bb._attach_overage(_invoice())

    assert fake.items, "no overage was billed — a month of usage was written off"
    assert "1,000 additional minutes" == fake.items[0]["description"]


def test_a_retry_after_stripe_already_applied_it_cannot_bill_twice(
        anon, db, tenants, stripe_configured, monkeypatch):
    """webhook() deletes the replay-guard row and 500s so Stripe re-delivers. If
    the create reached Stripe but the response was lost, the retry arrives with
    nothing recording that the item exists. The idempotency key is the only
    thing standing between that and a doubled invoice."""
    import blueprints.billing_bp as bb
    from plans import get_plan

    org_id = tenants.a["org"]
    db.add(Subscription(org_id=org_id, plan_code="growth", status="active",
                        stripe_customer_id="cus_1", included_minutes=8000,
                        overage_micros_per_minute=get_plan("growth").overage_micros_per_minute))
    start = datetime.now(timezone.utc) - timedelta(days=31)
    _period(db, org_id, start=start, used=9000 * 60, included=8000 * 60)
    db.commit()

    # First delivery: Stripe applies it, then the connection dies.
    fake = _RecordingStripe(explode_after=True)
    monkeypatch.setattr(bb, "_stripe", lambda: fake)
    with anon.application.test_request_context():
        g.db = db
        with pytest.raises(RuntimeError):
            bb._attach_overage(_invoice())
    db.rollback()

    # Stripe re-delivers. The guard row is gone; nothing in our DB knows.
    fake2 = _RecordingStripe()
    monkeypatch.setattr(bb, "_stripe", lambda: fake2)
    with anon.application.test_request_context():
        g.db = db
        bb._attach_overage(_invoice())

    assert fake.items and fake2.items, "expected both deliveries to reach Stripe"
    assert fake.items[0].get("idempotency_key"), "no idempotency key — Stripe would bill twice"
    assert fake.items[0]["idempotency_key"] == fake2.items[0]["idempotency_key"], \
        "the retry used a different key, so Stripe would create a second charge"


def test_a_period_already_billed_is_never_billed_again(
        anon, db, tenants, stripe_configured, monkeypatch):
    import blueprints.billing_bp as bb
    org_id = tenants.a["org"]
    db.add(Subscription(org_id=org_id, plan_code="growth", status="active",
                        stripe_customer_id="cus_1", included_minutes=8000,
                        overage_micros_per_minute=120))
    _period(db, org_id, start=datetime.now(timezone.utc) - timedelta(days=31),
            used=9000 * 60, included=8000 * 60, invoice_id="in_old")
    db.commit()

    fake = _RecordingStripe()
    monkeypatch.setattr(bb, "_stripe", lambda: fake)
    with anon.application.test_request_context():
        g.db = db
        bb._attach_overage(_invoice(invoice_id="in_2"))
    assert fake.items == []


def test_paying_closes_the_period_the_invoice_billed(
        anon, db, tenants, stripe_configured):
    """Not whichever period is open now — under the other webhook ordering that
    would settle an unbilled period and leave the paid one open forever."""
    import blueprints.billing_bp as bb
    org_id = tenants.a["org"]
    db.add(Subscription(org_id=org_id, plan_code="growth", status="active",
                        stripe_customer_id="cus_1", included_minutes=8000))
    billed = _period(db, org_id, start=datetime.now(timezone.utc) - timedelta(days=31),
                     used=9000 * 60, included=8000 * 60, invoice_id="in_paid")
    fresh = _period(db, org_id, start=datetime.now(timezone.utc), used=0, included=8000 * 60)
    db.commit()
    # Hold the keys, not the instances — expire_all() below detaches them.
    billed_start, fresh_start = billed.period_start, fresh.period_start

    with anon.application.test_request_context():
        g.db = db
        bb._close_period({"id": "in_paid", "customer": "cus_1"})
        # webhook() commits after the handler returns; this test calls the
        # handler directly, so it has to do the same or the change is discarded.
        db.commit()
    db.expire_all()

    assert db.get(UsagePeriod, (org_id, billed_start)).closed_at is not None
    assert db.get(UsagePeriod, (org_id, fresh_start)).closed_at is None, \
        "closed a period that was never invoiced"
