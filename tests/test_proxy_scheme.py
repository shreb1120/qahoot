"""External URLs must be https behind the reverse proxy.

nginx terminates TLS and forwards to 127.0.0.1:5000 over plain HTTP. Without
ProxyFix the app believed every request was http://, and every
url_for(..., _external=True) produced an http URL.

Found by reading the success_url off a real Stripe Checkout Session created
through the live billing page: it came back as http://qaboom.io/billing/.

The Stripe URLs are the visible symptom. The one that matters is the invite
link, which carries a single-use token *in the URL* — an http:// invite puts
that token on the network in cleartext on the first hop, before nginx redirects.

These go through the real WSGI stack on purpose. ProxyFix is middleware, so a
test built on `test_request_context` never runs it and would pass against the
unfixed code.
"""
import pytest
from flask import url_for


@pytest.fixture(scope="module")
def probe():
    """A fresh app carrying a probe route.

    Built here rather than borrowing the session-scoped `app` fixture: Flask
    refuses to register a route on an app that has already served a request,
    and the shared one has.
    """
    from app import create_app
    from conftest import TestConfig

    application = create_app(TestConfig)

    @application.get("/__probe_external")
    def _probe():
        return {
            "billing": url_for("billing.plan", _external=True),
            "invite": url_for("org.accept_invite", token="tok_secret", _external=True),
        }

    @application.get("/__probe_ip")
    def _probe_ip():
        from flask import request
        return {"addr": request.remote_addr or ""}

    return application.test_client()


PROXY = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "qaboom.io"}


def test_external_urls_are_https_when_the_proxy_says_so(probe):
    urls = probe.get("/__probe_external", headers=PROXY).get_json()
    assert urls["billing"].startswith("https://"), urls["billing"]


def test_an_invite_link_is_never_plaintext(probe):
    """The invite token travels in the URL. This is the one with teeth."""
    urls = probe.get("/__probe_external", headers=PROXY).get_json()
    assert urls["invite"].startswith("https://"), \
        "an invite token would cross the network in cleartext"
    assert "tok_secret" in urls["invite"]


def test_the_forwarded_host_is_honoured(probe):
    urls = probe.get("/__probe_external", headers=PROXY).get_json()
    assert "qaboom.io" in urls["billing"], urls["billing"]


def test_without_the_header_nothing_is_assumed(probe):
    """Local development over plain http must keep working — the scheme comes
    from the proxy, it is not hardcoded."""
    urls = probe.get("/__probe_external").get_json()
    assert urls["billing"].startswith("http://")


def test_the_client_ip_header_is_not_trusted(probe):
    """x_for stays off: remote_addr is unused, so trusting a client-settable
    header for it would add spoofing surface and buy nothing."""
    r = probe.get("/__probe_ip", headers={**PROXY, "X-Forwarded-For": "1.2.3.4"})
    assert r.get_json()["addr"] != "1.2.3.4"
