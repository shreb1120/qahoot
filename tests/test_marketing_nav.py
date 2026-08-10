"""The public header, seen by someone who is already a customer.

/pricing is deliberately public — it is a URL people link to and come back to.
But it rendered the logged-out header unconditionally, so a signed-in customer
saw "Sign in" and "Start free" sitting directly above their own sidebar, which
was showing their org name and how many minutes they had left.

The page body already adapted (its footer CTA reads "Go to your plan"). Only the
nav did not, because it depended on a `signed_in` variable that /pricing passes
and nothing else does.
"""


def test_a_signed_in_customer_is_not_offered_sign_in(tenants):
    body = tenants.a_admin.get("/pricing").get_data(as_text=True)
    nav = body.split("</header>")[0]
    assert "Sign in" not in nav, "a signed-in customer was offered 'Sign in'"
    assert "Start free" not in nav, "a signed-in customer was offered a free trial"


def test_a_signed_in_customer_gets_a_way_back_into_the_app(tenants):
    nav = tenants.a_admin.get("/pricing").get_data(as_text=True).split("</header>")[0]
    assert "/dashboard" in nav
    assert "/billing/" in nav


def test_a_visitor_still_gets_the_sales_header(anon):
    """The whole point of the page. Do not fix the above by removing the CTA."""
    nav = anon.get("/pricing").get_data(as_text=True).split("</header>")[0]
    assert "Sign in" in nav
    assert "Start free" in nav


def test_the_nav_does_not_depend_on_a_variable_each_route_must_remember():
    """It reads g, so a page that forgets to pass a flag cannot regress."""
    src = open("templates/marketing/_nav.html").read()
    assert "g.user" in src
