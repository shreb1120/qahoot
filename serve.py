"""Production WSGI server (Waitress). Run with: python serve.py

In Phase 1-3, bind to 127.0.0.1 and sit behind a reverse proxy for
local/staging use. Phase 4 will add nginx + TLS for public exposure.
"""
import os
import sys

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
    try:
        import pipeline
        pipeline.recover_stranded(
            assemblyai_key=application.config.get("ASSEMBLYAI_API_KEY", ""),
            anthropic_key=application.config.get("ANTHROPIC_API_KEY", ""),
        )
    except Exception:
        print("Startup recovery failed; continuing", file=sys.stderr)

    print(f"Qaboom listening on http://{host}:{port} (threads={threads})")
    print("Press Ctrl+C to stop.")
    serve(application, host=host, port=port, threads=threads)
