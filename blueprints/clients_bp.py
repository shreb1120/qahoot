"""Client pages: every call with one person, in one place.

Grouped on the phone number the dialer writes into every filename, so this
works backwards over calls uploaded long before the feature existed — an
upload-time grouping could only ever see a single batch.
"""
from flask import Blueprint, abort, g, render_template

import clients
from auth import org_required

clients_bp = Blueprint("clients", __name__, url_prefix="/clients")


@clients_bp.get("/")
@org_required
def index():
    return render_template("clients/list.html",
                           clients=clients.list_clients(g.db, g.org.id))


@clients_bp.get("/<key>")
@org_required
def detail(key):
    # org_id is inside the query rather than a check applied to its result, so
    # another org's client is not found rather than found-then-refused.
    data = clients.roll_up(g.db, g.org.id, clients.client_key(key) or key)
    if data is None:
        abort(404)
    return render_template("clients/detail.html", c=data)
