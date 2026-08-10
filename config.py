import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Flask
    SECRET_KEY: str = os.environ["SECRET_KEY"]
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    SESSION_COOKIE_SECURE: bool = (
        os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    )

    # Postgres
    DATABASE_URL: str = os.environ["DATABASE_URL"]

    # Clerk
    CLERK_PUBLISHABLE_KEY: str = os.environ["CLERK_PUBLISHABLE_KEY"]
    CLERK_JWKS_URL: str = os.environ["CLERK_JWKS_URL"]
    # e.g. https://<your-clerk-frontend-api>/.well-known/jwks.json

    # Who is allowed to have asked Clerk for a token we will accept.
    #
    # A Clerk session JWT carries `azp` — the origin that requested it. Without
    # checking it, a token minted for *any* origin is accepted here, which
    # matters because a development instance answers any origin and the
    # publishable key is public in the page source. Checking `azp` is what stops
    # a token obtained by another site being replayed against this one.
    #
    # `aud` is deliberately not checked: Clerk does not put one in these tokens
    # (verified against a live token), so requiring it would reject every
    # request.
    CLERK_AUTHORIZED_PARTIES: tuple = tuple(
        p.strip() for p in os.environ.get(
            "CLERK_AUTHORIZED_PARTIES",
            "https://qaboom.io,https://www.qaboom.io,http://localhost:5000",
        ).split(",") if p.strip()
    )
    # Derived from the JWKS URL unless overridden — they always share a host.
    CLERK_ISSUER: str = os.environ.get("CLERK_ISSUER", "") or os.environ[
        "CLERK_JWKS_URL"
    ].split("/.well-known/")[0]

    # API keys used in Phase 2 (not required for Phase 1 to start)
    ASSEMBLYAI_API_KEY: str = os.environ.get("ASSEMBLYAI_API_KEY", "")
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

    # File uploads
    UPLOAD_FOLDER: str = os.environ.get("UPLOAD_FOLDER", "/srv/qaboom/uploads")
    MAX_CONTENT_LENGTH: int = 500 * 1024 * 1024  # 500 MB
