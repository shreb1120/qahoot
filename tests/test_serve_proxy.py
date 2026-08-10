"""The proxy configuration, tested against the server that actually runs it.

tests/test_proxy_scheme.py checks ProxyFix through Flask's test client, and it
passed while production was still emitting http:// URLs. The test client never
goes through waitress, and waitress was the problem: it defaults to
`clear_untrusted_proxy_headers=True` with `trusted_proxy=None`, so it *deletes*
X-Forwarded-* from the environ before the app runs. ProxyFix found nothing.

Nothing about that is visible from inside the app. It can only be caught by
starting the real server and sending it a real request, which is what this does.
"""
import socket
import threading

import pytest


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    """The app under real waitress, configured exactly as serve.py does."""
    import waitress
    from flask import Flask, url_for
    from werkzeug.middleware.proxy_fix import ProxyFix

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=0)

    @app.get("/whoami")
    def whoami():
        return {"url": url_for("whoami", _external=True)}

    port = _free_port()
    server = waitress.create_server(
        app, host="127.0.0.1", port=port,
        trusted_proxy="127.0.0.1",
        trusted_proxy_headers={"x-forwarded-proto", "x-forwarded-host"},
    )
    threading.Thread(target=server.run, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    server.close()


def _get(base, headers):
    import json
    import urllib.request
    req = urllib.request.Request(base + "/whoami", headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["url"]


def test_the_forwarded_scheme_survives_waitress(live_server):
    """The regression. Without trusted_proxy this comes back http:// — which is
    exactly what production served, while the test-client suite stayed green."""
    url = _get(live_server, {"X-Forwarded-Proto": "https",
                             "X-Forwarded-Host": "qaboom.io"})
    assert url.startswith("https://"), \
        f"waitress stripped X-Forwarded-Proto — got {url}"
    assert "qaboom.io" in url


def test_plain_http_is_unchanged(live_server):
    assert _get(live_server, {}).startswith("http://")


def test_serve_py_actually_passes_the_setting():
    """The fix lives in a call that only runs under `python serve.py`, so the
    live-server test above would keep passing if someone dropped it there."""
    import ast
    import inspect
    import serve

    src = inspect.getsource(serve)
    kwargs = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "serve":
            kwargs |= {kw.arg for kw in node.keywords}
    assert "trusted_proxy" in kwargs, "serve.py no longer trusts the reverse proxy"
    assert "trusted_proxy_headers" in kwargs
