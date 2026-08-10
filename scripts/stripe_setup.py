#!/usr/bin/env python3
"""Create the Stripe products and prices for the plans in plans.py.

Reproducible on purpose: clicking these into the Stripe dashboard by hand means
test mode and live mode drift, and the price a customer is charged stops
matching the one on the landing page. This script reads plans.py — the single
source of truth — and creates exactly what it describes.

Idempotent. Products are looked up by a stable `qaboom_plan` metadata key, and
an existing price with the right amount is reused rather than duplicated.
Stripe prices are immutable, so a *changed* amount creates a new price and
leaves the old one alone — existing subscribers keep the price they signed up
at, which is what you want.

    export STRIPE_SECRET_KEY=sk_test_...
    python3 scripts/stripe_setup.py --dry-run
    python3 scripts/stripe_setup.py

Prints the env vars to add to .env at the end.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from plans import purchasable_plans        # noqa: E402

# Stripe's "Software as a service (SaaS) — business use" tax code. Required:
# Managed Payments is on by default for new accounts and rejects a Checkout
# session whose product has no tax code. Business-use rather than personal-use
# because Qaboom is sold to organizations, and some jurisdictions tax the two
# differently.
TAX_CODE = "txcd_10103000"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        print("STRIPE_SECRET_KEY is not set.", file=sys.stderr)
        return 1
    if key.startswith("sk_live_"):
        print("This is a LIVE key. Run against sk_test_ first.", file=sys.stderr)
        return 1

    import stripe
    stripe.api_key = key

    env_lines = []
    for plan in purchasable_plans():
        # Stripe has no "get or create", so find by our own metadata key.
        found = stripe.Product.search(query=f"metadata['qaboom_plan']:'{plan.code}'")
        product = found.data[0] if found.data else None

        if product is None:
            print(f"{plan.name}: creating product")
            if not args.dry_run:
                product = stripe.Product.create(
                    name=f"Qaboom {plan.name}",
                    description=plan.blurb,
                    tax_code=TAX_CODE,
                    metadata={"qaboom_plan": plan.code},
                )
        else:
            print(f"{plan.name}: product exists ({product.id})")
            # Backfill on an existing product: Stripe enables Managed Payments
            # by default on new accounts, and a product without a tax code makes
            # every Checkout session fail with a 400. Cheap to set, and the
            # failure it prevents only shows up when a real customer clicks buy.
            if not args.dry_run and getattr(product, "tax_code", None) != TAX_CODE:
                stripe.Product.modify(product.id, tax_code=TAX_CODE)
                print(f"{plan.name}: set tax code")

        if args.dry_run and product is None:
            env_lines.append(f"{plan.stripe_price_env}=price_...   # {plan.name}")
            continue

        cents = plan.price_micros // 10_000
        prices = stripe.Price.list(product=product.id, active=True, limit=100)
        price = next(
            (p for p in prices.data
             if p.unit_amount == cents
             and p.recurring and p.recurring.interval == "month"),
            None,
        )

        if price is None:
            print(f"{plan.name}: creating ${cents / 100:,.2f}/month price")
            if not args.dry_run:
                price = stripe.Price.create(
                    product=product.id,
                    unit_amount=cents,
                    currency="usd",
                    recurring={"interval": "month"},
                    metadata={"qaboom_plan": plan.code,
                              "included_minutes": str(plan.included_minutes)},
                )
        else:
            print(f"{plan.name}: price exists ({price.id})")

        env_lines.append(
            f"{plan.stripe_price_env}={price.id if price else 'price_...'}"
        )

    print("\nAdd to .env:\n")
    for line in env_lines:
        print(f"  {line}")
    print("\nThen point a webhook at https://qaboom.io/billing/webhook for")
    print("customer.subscription.*, invoice.created and invoice.paid, and put")
    print("its signing secret in STRIPE_WEBHOOK_SECRET.")
    if args.dry_run:
        print("\n(dry run — nothing was created)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
