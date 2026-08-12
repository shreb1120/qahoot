"""Public marketing pages.

These are the only routes in the app a signed-out visitor can reach, so two
things matter more here than anywhere else:

* **No `@org_required`, and no assumption that `g.user` exists.** Every other
  template in this app renders inside a session; these must render for someone
  who has never seen the product.
* **Prices come from plans.py.** The pricing table and Stripe Checkout read the
  same module, because a marketing page that disagrees with the charge is how
  you end up billing someone an amount they never saw.
"""
from flask import (Blueprint, current_app, g, redirect, render_template,
                   url_for)

from plans import TRIAL, purchasable_plans

marketing_bp = Blueprint("marketing", __name__)


def _signed_in() -> bool:
    return bool(getattr(g, "user", None) and getattr(g, "org", None))


@marketing_bp.get("/")
def home():
    """Marketing for visitors, the dashboard for customers.

    A signed-in customer typing the bare domain wants their work, not a sales
    pitch — the redirect is what makes qaboom.io feel like their tool rather
    than a brochure they have to click past.
    """
    if _signed_in():
        return redirect(url_for("dashboard.index"))
    return render_template("marketing/home.html",
                           plans=purchasable_plans(), trial=TRIAL)


@marketing_bp.get("/tour")
def tour():
    """The product walkthrough, over the real report page.

    Public and unauthenticated on purpose: every call to action on this site
    used to require an account, so nobody could see the product before deciding
    whether to want it.

    It renders the same template the app renders, from a synthetic fixture —
    not a seeded database row. Nothing here can reach a customer's data, and a
    screenshot-based tour would have started going stale immediately: this
    report page changed three times in the week the tour was written.
    """
    import demo_tour
    from blueprints.calls_bp import _report_view_model

    rel = demo_tour.ensure_audio(current_app.static_folder)
    audio_url = url_for("static", filename=rel) if rel else None
    call = demo_tour.demo_call(audio_url)

    return render_template(
        "marketing/tour.html",
        call=call, report=call.report, vm=_report_view_model(call),
        agents=[call.agent], reviewer_email=None, audio_src=audio_url,
        outcome_label=None, outcome_blurb=None,
        is_failing=True,
        tour_steps=demo_tour.STEPS,
    )


@marketing_bp.get("/pricing")
def pricing():
    """A URL people can link to and return to. Renders for anyone."""
    return render_template("marketing/pricing.html",
                           plans=purchasable_plans(), trial=TRIAL,
                           signed_in=_signed_in())
