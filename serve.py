"""Production WSGI server using Waitress. Run with: python serve.py

HOST defaults to 127.0.0.1 — listen only on loopback unless an admin explicitly
opts in to LAN binding via the HOST env var. Combined with the Windows Firewall
rule documented in the README, this keeps the service off any public interface.
"""
import os
import sys
from waitress import serve
from app import app, init_db

if __name__ == '__main__':
    init_db()
    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', '5000'))
    threads = int(os.getenv('THREADS', '8'))

    if host == '0.0.0.0':
        print(
            "WARNING: HOST=0.0.0.0 binds to every interface on this machine,\n"
            "         including any public or VPN interface. Set HOST to the\n"
            "         server's specific LAN IP, or rely on the Windows Firewall\n"
            "         rule from README.md to restrict access.",
            file=sys.stderr,
        )

    print(f"Call QA Analyzer listening on http://{host}:{port} (threads={threads})")
    print("Press Ctrl+C to stop.")
    serve(app, host=host, port=port, threads=threads)
