"""Production WSGI server (Waitress). Run with: python serve.py

In Phase 1-3, bind to 127.0.0.1 and sit behind a reverse proxy for
local/staging use. Phase 4 will add nginx + TLS for public exposure.
"""
import os
import sys
import traceback

from waitress import serve
from app import create_app

if __name__ == "__main__":
    application = create_app()

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    threads = int(os.getenv("THREADS", "8"))

    if host == "0.0.0.0":
        print(
            "WARNING: HOST=0.0.0.0 exposes this server on all network interfaces.\n"
            "         This is not recommended until Phase 4 (TLS + hardening) is complete.",
            file=sys.stderr,
        )

    # Re-queue work the previous process abandoned. Here rather than in
    # create_app() because it is a *process* concern: building an app must
    # stay free of side effects, or importing this package touches whatever
    # database the ambient config points at — which is exactly how the test
    # suite ended up connecting to production.
    import pipeline
    aai_key = application.config.get("ASSEMBLYAI_API_KEY", "")
    anthropic_key = application.config.get("ANTHROPIC_API_KEY", "")

    # Two separate try blocks, deliberately.
    #
    # These were one, and it meant the *sweep* and the *sweeper* shared a fate:
    # Postgres a few seconds late accepting connections after a reboot — the
    # single most likely moment for this to run — made the first call raise, and
    # the second one never executed. Recovery was then disabled for the entire
    # life of the process, with one line of stderr and no traceback to say so.
    #
    # The one-off sweep is the expendable half; the sweeper is what makes the
    # failure self-correcting, since its next tick retries the work the startup
    # sweep missed.
    try:
        pipeline.recover_stranded(
            assemblyai_key=aai_key, anthropic_key=anthropic_key,
        )
    except Exception:
        traceback.print_exc()
        print("Startup recovery sweep failed; the periodic sweeper will retry",
              file=sys.stderr)

    try:
        pipeline.start_recovery_sweeper(
            assemblyai_key=aai_key, anthropic_key=anthropic_key,
        )
    except Exception:
        traceback.print_exc()
        print("CRITICAL: recovery sweeper did not start — abandoned calls will "
              "sit unfinished until the next deploy", file=sys.stderr)

    print(f"Qaboom listening on http://{host}:{port} (threads={threads})")
    print("Press Ctrl+C to stop.")

    # `trusted_proxy` is not optional here, and its absence is silent.
    #
    # Waitress defaults to clear_untrusted_proxy_headers=True with no trusted
    # proxy, so it *deletes* X-Forwarded-* from the environ before the app runs.
    # ProxyFix in create_app() then finds nothing, the app concludes it is
    # serving plain http, and every url_for(..., _external=True) yields an
    # http:// URL — Stripe return URLs, and invite links carrying a token in
    # the query string.
    #
    # Nothing about that failure is visible from inside the app, and it cannot
    # be reproduced with Flask's test client, which never goes through waitress.
    # It has to be configured on the server and tested against the real one.
    #
    # x-forwarded-for is deliberately not trusted: remote_addr is unused, so
    # accepting a client-settable header for it is risk without benefit.
    serve(
        application, host=host, port=port, threads=threads,
        trusted_proxy="127.0.0.1",
        trusted_proxy_headers={"x-forwarded-proto", "x-forwarded-host"},
    )
