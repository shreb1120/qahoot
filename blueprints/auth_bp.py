"""
Auth blueprint — sign-in, sign-up, and sign-out routes.

Actual credential handling is entirely Clerk's responsibility.  These routes
just render the pages that mount Clerk's JS components and handle the
server-side sign-out (clearing the __session cookie).
"""
from auth import safe_next
from flask import (
    request,
    Blueprint,
    current_app,
    g,
    make_response,
    redirect,
    render_template,
    url_for,
)


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.get("/login")
def login():
    # A signed-in visitor who lands here still deserves their destination.
    nxt = safe_next(request.args.get("next"))
    if g.user:
        return redirect(nxt or url_for("dashboard.index"))
    return render_template(
        "auth/login.html",
        clerk_key=current_app.config["CLERK_PUBLISHABLE_KEY"],
        next_url=nxt,
    )


@auth_bp.get("/signup")
def signup():
    nxt = safe_next(request.args.get("next"))
    if g.user:
        return redirect(nxt or url_for("dashboard.index"))
    return render_template(
        "auth/signup.html",
        clerk_key=current_app.config["CLERK_PUBLISHABLE_KEY"],
        next_url=nxt,
    )


@auth_bp.get("/logout")
def logout():
    # Do NOT delete the cookie here — Clerk needs the session cookie intact
    # so its JS can detect and revoke the session on Clerk's servers.
    # The logout.html page calls Clerk.signOut() which clears everything.
    return render_template(
        "auth/logout.html",
        clerk_key=current_app.config["CLERK_PUBLISHABLE_KEY"],
    )
