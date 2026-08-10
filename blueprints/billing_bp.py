"""Billing — Checkout, the customer portal, webhooks, and the plan page.

Stripe is the source of truth for subscription state; the `subscriptions` table
is a local cache so every request doesn't call their API. That direction never
reverses: nothing here decides a subscription is active, it only records what
Stripe said.

Three properties this module has to hold:

* **The webhook is idempotent.** Stripe retries, and will deliver the same
  event more than once. `stripe_events` is inserted first; a conflict means we
  already handled it and the handler is skipped.
* **The webhook is authenticated by signature, not by session.** It is the one
  CSRF-exempt endpoint in the app, and it must reject anything unsigned — an
  unauthenticated public endpoint that writes subscription state would
  otherwise be a way to give yourself a free plan.
* **Overage is computed from our ledger, not Stripe's.** We push one invoice
  item at close; the usage_events table stays authoritative and auditable.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from flask import (
    Blueprint, abort, current_app, flash, g, redirect,
    render_template, request, url_for,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

import usage
from auth import admin_required, org_required
from models import StripeEvent, Subscription, UsagePeriod
from plans import PLANS_BY_CODE, get_plan, plan_for_price, purchasable_plans

logger = logging.getLogger(__name__)

billing_bp = Blueprint("billing", __name__, url_prefix="/billing")


def _stripe():
    """Configured Stripe client, or None when keys are absent.

    Absent keys are a normal state, not an error: the app runs fine without
    billing configured, and every route here degrades to an explanatory
    message rather than a 500.
    """
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        return None
    import stripe
    stripe.api_key = key
    return stripe


def _sub(db, org_id: str) -> Subscription | None:
    return db.get(Subscription, org_id)


def _ts(value) -> datetime | None:
    return datetime.fromtimestamp(value, tz=timezone.utc) if value else None


# ─────────────────────────────── plan page ───────────────────────────────

@billing_bp.get("/")
@org_required
def plan():
    """Current plan, usage this period, and the way to change either."""
    summary = usage.summary_for(g.db, g.org)
    g.db.commit()          # current_period may have opened this period's row
    sub = _sub(g.db, g.org.id)

    return render_template(
        "billing/plan.html",
        summary=summary,
        subscription=sub,
        plans=purchasable_plans(),
        billing_configured=bool(os.environ.get("STRIPE_SECRET_KEY")),
        is_admin=g.user.role == "admin",
    )


# ─────────────────────────────── checkout ───────────────────────────────

@billing_bp.post("/checkout")
@admin_required
def checkout():
    """Send an admin to Stripe Checkout for the chosen plan."""
    stripe = _stripe()
    if stripe is None:
        flash("Billing isn't configured yet. Get in touch and we'll set you up.",
              "error")
        return redirect(url_for("billing.plan"))

    plan_obj = PLANS_BY_CODE.get(request.form.get("plan", ""))
    if plan_obj is None or not plan_obj.is_purchasable:
        flash("That plan isn't available.", "error")
        return redirect(url_for("billing.plan"))

    sub = _sub(g.db, g.org.id)
    customer_id = sub.stripe_customer_id if sub else None
    if not customer_id:
        customer = stripe.Customer.create(
            name=g.org.name,
            email=g.user.email,
            # The org id is how a webhook finds its way back to a tenant. It
            # travels on the customer rather than the session so it survives
            # every later subscription change, including ones made in Stripe's
            # own portal where we never see the request.
            metadata={"org_id": g.org.id},
        )
        customer_id = customer.id
        if sub is None:
            sub = Subscription(org_id=g.org.id)
            g.db.add(sub)
        sub.stripe_customer_id = customer_id
        g.db.commit()

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": plan_obj.stripe_price_id, "quantity": 1}],
        success_url=url_for("billing.plan", _external=True) + "?welcome=1",
        cancel_url=url_for("billing.plan", _external=True),
        client_reference_id=g.org.id,
        subscription_data={"metadata": {"org_id": g.org.id,
                                        "plan_code": plan_obj.code}},
    )
    return redirect(session.url, code=303)


@billing_bp.post("/portal")
@admin_required
def portal():
    """Hand the customer to Stripe's own portal to manage or cancel."""
    stripe = _stripe()
    sub = _sub(g.db, g.org.id)
    if stripe is None or sub is None or not sub.stripe_customer_id:
        flash("There's no billing account to manage yet.", "error")
        return redirect(url_for("billing.plan"))

    session = stripe.billing_portal.Session.create(
        customer=sub.stripe_customer_id,
        return_url=url_for("billing.plan", _external=True),
    )
    return redirect(session.url, code=303)


# ─────────────────────────────── webhook ───────────────────────────────

@billing_bp.post("/webhook")
def webhook():
    """Receive subscription state changes from Stripe.

    CSRF-exempt by necessity — Stripe has no session and cannot send our token
    — so the signature check is the *only* authentication. It is not optional
    and there is no unsigned path through this function.
    """
    stripe = _stripe()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if stripe is None or not secret:
        logger.error("Stripe webhook received but billing is not configured")
        abort(503)

    raw = request.get_data()
    try:
        # The SDK's job here is signature verification, and only that. Its
        # return value is a StripeObject whose attribute access is overloaded
        # — `obj.get(...)` resolves through __getattr__ and raises rather than
        # behaving like dict.get — so every handler below works on plain JSON
        # parsed from the same bytes we just authenticated. Verify with the
        # library; read with the standard library.
        stripe.Webhook.construct_event(
            raw, request.headers.get("Stripe-Signature", ""), secret
        )
        event = json.loads(raw)
    except Exception as exc:
        logger.warning("Rejected Stripe webhook: %s", exc)
        abort(400)

    # Replay guard. Insert first: if the id is already present Stripe is
    # retrying something we handled, and re-running a handler could open a
    # duplicate billing period or double-post an invoice item.
    inserted = g.db.execute(
        pg_insert(StripeEvent)
        .values(id=event["id"], type=event["type"],
                received_at=datetime.now(timezone.utc))
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(StripeEvent.id)
    ).first()
    if inserted is None:
        logger.info("Stripe event %s already processed", event["id"])
        return "", 200
    g.db.commit()

    try:
        _handle(event)
    except Exception:
        # 500 so Stripe retries; the replay guard row is removed first so the
        # retry is not mistaken for a duplicate.
        logger.exception("Failed handling Stripe event %s", event["id"])
        g.db.rollback()
        g.db.query(StripeEvent).filter_by(id=event["id"]).delete()
        g.db.commit()
        abort(500)

    g.db.query(StripeEvent).filter_by(id=event["id"]).update(
        {"processed_at": datetime.now(timezone.utc)}
    )
    g.db.commit()
    return "", 200


def _handle(event) -> None:
    kind = event["type"]
    obj = event["data"]["object"]

    if kind.startswith("customer.subscription."):
        _sync_subscription(obj)
    elif kind == "invoice.created":
        _attach_overage(obj)
    elif kind == "invoice.paid":
        _close_period(obj)
    else:
        logger.info("Ignoring Stripe event type %s", kind)


def _org_id_for(obj) -> str | None:
    """Find the tenant a Stripe object belongs to."""
    meta = obj.get("metadata") or {}
    if meta.get("org_id"):
        return meta["org_id"]
    sub = g.db.query(Subscription).filter_by(
        stripe_customer_id=obj.get("customer")).first()
    return sub.org_id if sub else None


def _sync_subscription(obj) -> None:
    org_id = _org_id_for(obj)
    if not org_id:
        logger.warning("Stripe subscription %s has no org", obj.get("id"))
        return

    sub = _sub(g.db, org_id)
    if sub is None:
        sub = Subscription(org_id=org_id)
        g.db.add(sub)

    # Price first, metadata second. Stripe rewrites the subscription's price
    # item when a customer changes plan in the billing portal; it does NOT
    # rewrite the metadata we set at Checkout. Reading metadata alone billed an
    # upgraded customer against their old plan — the wrong allowance and the
    # wrong overage rate, in the customer's favour on a downgrade and very much
    # not on an upgrade.
    items = (obj.get("items") or {}).get("data") or []
    price_id = (items[0].get("price") or {}).get("id") if items else None

    plan_obj = plan_for_price(price_id)
    if plan_obj is None:
        plan_obj = get_plan((obj.get("metadata") or {}).get("plan_code")
                            or sub.plan_code)
        if price_id:
            logger.warning(
                "Stripe price %s matches no plan in plans.py; falling back to "
                "metadata (%s). Check STRIPE_PRICE_* env vars.",
                price_id, plan_obj.code,
            )

    sub.stripe_subscription_id = obj.get("id")
    sub.stripe_customer_id = obj.get("customer") or sub.stripe_customer_id
    sub.status = obj.get("status") or sub.status
    sub.plan_code = plan_obj.code
    sub.included_minutes = plan_obj.included_minutes
    sub.overage_micros_per_minute = plan_obj.overage_micros_per_minute
    # The billing period lives on the subscription *item*, not the subscription.
    # Stripe moved it there in API version 2025-06-30; on 2026-07-29 (what this
    # account is on) the top-level fields are gone entirely, so reading them
    # returned None on every webhook and no paid org ever had a period recorded.
    #
    # That is not cosmetic: usage.current_period() uses these dates to decide
    # what "this period" means, so every paid org silently fell back to the
    # calendar month while Stripe invoiced on the subscription's own anniversary.
    # A customer signing up on the 10th would have had usage counted 1st-to-1st
    # and billed 10th-to-10th — two different windows for one invoice.
    #
    # The top-level read stays as a fallback so an older payload, or a replay of
    # an event captured before the move, still resolves.
    item = items[0] if items else {}
    sub.current_period_start = _ts(item.get("current_period_start")
                                   or obj.get("current_period_start"))
    sub.current_period_end = _ts(item.get("current_period_end")
                                 or obj.get("current_period_end"))
    sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    sub.trial_ends_at = _ts(obj.get("trial_end"))

    if price_id:
        sub.stripe_price_id = price_id

    logger.info("Org %s subscription is now %s on %s", org_id, sub.status, sub.plan_code)


def _period_to_bill(org_id: str, invoice):
    """The usage period this invoice should carry overage for.

    Not `usage.current_period()`, for two independent reasons.

    **Ordering.** That function answers "which period are we in *now*", derived
    from `subscriptions.current_period_start`. Stripe guarantees no ordering
    between `customer.subscription.updated` and `invoice.created`, so when the
    subscription row is advanced first, "now" is the new, empty cycle: overage
    computes to zero, the guard is never set, and a month of real overage is
    silently never invoiced. A coin flip, every month, per customer.

    **Side effects.** `current_period()` inserts a period row if none exists. A
    webhook has no business opening a billing period as a side effect of
    reading one.

    Neither `invoice.period_start` nor the line period can be used directly:
    on a first invoice the two top-level fields are both the creation instant,
    and on a renewal the line period describes the cycle being *paid for in
    advance*, while the overage belongs to the cycle that just ended.

    So the question is asked of our own ledger: the most recent period that has
    **ended** by the time this invoice was cut, and has not yet been billed.

    "Ended" rather than "begun" is the load-bearing word. Selecting the latest
    period that had merely started picks the new, empty cycle whenever the
    subscription was advanced first — reproducing the very bug this function
    exists to fix. A cycle still in progress is not yet billable; only the one
    that closed is.

    A first invoice matches nothing, because no period has ended yet. That is
    correct: there is no overage to collect on a subscription's first day.
    """
    from models import UsagePeriod

    cut = invoice.get("created")
    cut_at = (datetime.fromtimestamp(cut, tz=timezone.utc) if cut
              else datetime.now(timezone.utc))

    return (
        g.db.query(UsagePeriod)
        .filter(UsagePeriod.org_id == org_id,
                UsagePeriod.stripe_invoice_id.is_(None),
                UsagePeriod.period_end <= cut_at)
        .order_by(UsagePeriod.period_end.desc())
        .first()
    )


def _attach_overage(invoice) -> None:
    """Bill the minutes beyond the allowance, once, as a single line item.

    Computed from our ledger rather than streamed to Stripe as meter events:
    the ledger is the auditable record, and one line item is far easier for a
    customer to query than thousands of usage records.
    """
    stripe = _stripe()
    org_id = _org_id_for(invoice)
    if not org_id or invoice.get("status") != "draft":
        return

    from models import Organization
    org = g.db.get(Organization, org_id)
    sub = _sub(g.db, org_id)
    if org is None or sub is None:
        return

    period = _period_to_bill(org_id, invoice)
    if period is None or period.stripe_invoice_id:
        return                                    # nothing owed, or already billed

    over = max(0, usage.minutes_from_seconds(period.billable_seconds)
                  - usage.minutes_from_seconds(period.included_seconds))
    if over <= 0:
        return

    amount_cents = round(over * sub.overage_micros_per_minute / 10_000)
    if amount_cents <= 0:
        return

    # The idempotency key is what stops a retry double-billing.
    #
    # On any failure `webhook()` deletes the replay-guard row and returns 500 so
    # Stripe re-delivers. If this call reached Stripe but the response was lost —
    # a timeout, a dropped connection — or anything after it raised, the rollback
    # discards `stripe_invoice_id` and the retry arrives with nothing recording
    # that the item exists. Without a key it posts a second one: a Growth org
    # owing $480 of overage is invoiced $960.
    #
    # Keyed on org and period rather than on the event, because the retry is a
    # different delivery of the same logical charge, and because two different
    # events must never collapse into one charge.
    stripe.InvoiceItem.create(
        customer=invoice["customer"],
        invoice=invoice["id"],
        amount=amount_cents,
        currency="usd",
        description=f"{over:,} additional minutes",
        idempotency_key=f"qaboom:overage:{org_id}:{period.period_start.isoformat()}",
    )
    period.stripe_invoice_id = invoice["id"]
    logger.info("Org %s billed %d overage minutes (%d cents) for period starting %s",
                org_id, over, amount_cents, period.period_start)


def _close_period(invoice) -> None:
    """Mark the period settled once its invoice is paid.

    Closes the period this invoice actually billed, found by the invoice id we
    recorded when attaching the overage — not whichever period happens to be
    open now. Under the ordering where the subscription advances first, "now"
    is the next cycle, and closing that would mark an unbilled period settled
    while leaving the paid one open forever.

    A paid invoice that carried no overage has no period pointing at it, and
    there is correspondingly nothing to close.
    """
    from models import UsagePeriod

    org_id = _org_id_for(invoice)
    if not org_id:
        return

    period = (
        g.db.query(UsagePeriod)
        .filter(UsagePeriod.org_id == org_id,
                UsagePeriod.stripe_invoice_id == invoice["id"])
        .first()
    )
    if period is None:
        return
    period.closed_at = datetime.now(timezone.utc)
