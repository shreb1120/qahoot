"""
Auth blueprint — sign-in, sign-up, and sign-out routes.

Actual credential handling is entirely Clerk's responsibility.  These routes
just render the pages that mount Clerk's JS components and handle the
server-side sign-out (clearing the __session cookie).
"""
from flask import (
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
    if g.user:
        return redirect(url_for("dashboard.index"))
    return render_template(
        "auth/login.html",
        clerk_key=current_app.config["CLERK_PUBLISHABLE_KEY"],
    )


@auth_bp.get("/signup")
def signup():
    if g.user:
        return redirect(url_for("dashboard.index"))
    return render_template(
        "auth/signup.html",
        clerk_key=current_app.config["CLERK_PUBLISHABLE_KEY"],
    )


@auth_bp.get("/logout")
def logout():
    # Belt-and-suspenders: delete the __session cookie server-side.
    # Clerk's JS UserButton component also clears it on the frontend.
    resp = make_response(redirect(url_for("auth.login")))
    resp.delete_cookie("__session")
    return resp
