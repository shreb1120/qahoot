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

    # API keys used in Phase 2 (not required for Phase 1 to start)
    ASSEMBLYAI_API_KEY: str = os.environ.get("ASSEMBLYAI_API_KEY", "")
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

    # File uploads
    UPLOAD_FOLDER: str = os.environ.get("UPLOAD_FOLDER", "/srv/qaboom/uploads")
    MAX_CONTENT_LENGTH: int = 500 * 1024 * 1024  # 500 MB
