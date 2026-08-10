"""Plan definitions — the single source of truth for pricing.

The landing page's pricing table and Stripe Checkout both read from here. Keep
two copies and you will eventually charge someone a price they never saw on the
page, which is the kind of mistake that ends a B2B relationship.

Money is in **micros** (millionths of a dollar) throughout: 1_000_000 = $1.00.
Integers, because floats and money do not mix — $0.12 has no exact binary
representation, and a per-minute overage rate multiplied by thousands of
minutes accumulates that error into real dollars.

Allowances are in **minutes**, because that is what the customer sees. The
ledger stores seconds, and rounds to minutes exactly once at aggregation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

MICROS = 1_000_000

# Trial allowance, in minutes. Sized in *calls*, not round numbers: at the
# measured ~31-minute average, 100 minutes was three calls, which is a sample
# rather than a pattern. 300 is roughly ten — enough for a QA manager to see
# whether the grading holds up across different agents and call types, which
# is the only question a trial exists to answer. Costs us well under a dollar.
TRIAL_MINUTES = 300


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    price_micros: int            # per month
    included_minutes: int
    overage_micros_per_minute: int
    blurb: str
    features: tuple[str, ...]
    # Hard-stops when the allowance runs out instead of billing overage. Only
    # the trial does this: a free plan with soft overage is just a free plan.
    hard_stop: bool = False
    # Set in the environment once the Stripe products exist. The landing page
    # renders without them; Checkout refuses without them.
    stripe_price_env: str | None = None

    @property
    def price_dollars(self) -> float:
        return self.price_micros / MICROS

    @property
    def overage_dollars_per_minute(self) -> float:
        return self.overage_micros_per_minute / MICROS

    @property
    def stripe_price_id(self) -> str | None:
        return os.environ.get(self.stripe_price_env) if self.stripe_price_env else None

    @property
    def is_purchasable(self) -> bool:
        """A plan can only be bought if Stripe knows its price."""
        return bool(self.stripe_price_id)


PLANS: tuple[Plan, ...] = (
    Plan(
        code="trial",
        name="Trial",
        price_micros=0,
        included_minutes=TRIAL_MINUTES,
        overage_micros_per_minute=0,
        hard_stop=True,
        blurb="See it work on your own recordings before you decide anything.",
        features=(
            f"{TRIAL_MINUTES} minutes of audio",
            "Your checklist, yours to edit",
            "Full reports with timestamped evidence",
            "No card, nothing to cancel",
        ),
    ),
    Plan(
        code="solo",
        name="Solo",
        price_micros=99 * MICROS,
        included_minutes=600,
        overage_micros_per_minute=220_000,          # $0.22/min
        stripe_price_env="STRIPE_PRICE_SOLO",
        blurb="One reviewer, a few calls a day.",
        features=(
            "600 minutes a month included",
            "$0.22 a minute after that",
            "Everyone on your team, no seat count",
            "Full reports with timestamped evidence",
        ),
    ),
    Plan(
        code="starter",
        name="Starter",
        price_micros=299 * MICROS,
        included_minutes=2_000,
        overage_micros_per_minute=180_000,          # $0.18/min
        stripe_price_env="STRIPE_PRICE_STARTER",
        blurb="One reviewer keeping up with one team.",
        features=(
            "2,000 minutes a month included",
            "$0.18 a minute after that",
            "Everyone on your team, no seat count",
            "Written warnings, ready to send",
        ),
    ),
    Plan(
        code="growth",
        name="Growth",
        price_micros=799 * MICROS,
        included_minutes=8_000,
        overage_micros_per_minute=120_000,          # $0.12/min
        stripe_price_env="STRIPE_PRICE_GROWTH",
        blurb="A QA function covering every agent, every week.",
        features=(
            "8,000 minutes a month included",
            "$0.12 a minute after that",
            "Everyone on your team, no seat count",
            "Per-agent coaching scorecards",
        ),
    ),
)

PLANS_BY_CODE: dict[str, Plan] = {p.code: p for p in PLANS}
TRIAL = PLANS_BY_CODE["trial"]


def get_plan(code: str | None) -> Plan:
    """Resolve a plan code, falling back to the trial.

    Never raises. An org row carrying a plan code we no longer sell — a
    grandfathered or renamed plan — must still be able to load its own billing
    page rather than 500.
    """
    return PLANS_BY_CODE.get(code or "", TRIAL)


def purchasable_plans() -> tuple[Plan, ...]:
    """Plans a visitor can actually check out with."""
    return tuple(p for p in PLANS if p.price_micros > 0)


def overage_micros(plan: Plan, used_minutes: int) -> int:
    """What the minutes beyond the allowance cost, in micros."""
    over = max(0, used_minutes - plan.included_minutes)
    return over * plan.overage_micros_per_minute
