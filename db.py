import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Shared declarative base — all models inherit from this.
class Base(DeclarativeBase):
    pass


_SessionLocal: sessionmaker | None = None


# Every wait against Postgres is bounded, because none of them were.
#
# The app serves on eight Waitress threads against a pool of five. One statement
# that never returns — a lock held by a migration, a query behind a long
# transaction — took a thread with it, and with nothing to stop it the whole
# site would hang with no error anywhere and need a manual pg_terminate_backend.
#
# statement_timeout is sized for web requests. It is not a constraint on the
# pipeline, whose long waits are vendor API calls rather than SQL, and it does
# not apply to migrations: Alembic builds its own engine (migrations/env.py),
# where a long DDL statement is legitimate but a long *lock wait* is not.
#
# idle_in_transaction_session_timeout is the one that stops a leaked transaction
# holding locks indefinitely and taking the rest of the app down with it.
# Defaults, overridable per-deployment. Read at call time rather than import
# time so they can be changed without reimporting this module — which resets the
# session factory and is the sort of thing you do not want to discover while the
# database is already struggling.
DEFAULTS = {
    "PG_CONNECT_TIMEOUT": "10",        # seconds
    "PG_STATEMENT_TIMEOUT_MS": "30000",
    "PG_LOCK_TIMEOUT_MS": "5000",
    "PG_IDLE_TX_TIMEOUT_MS": "60000",
}


def _setting(name: str) -> int:
    return int(os.environ.get(name) or DEFAULTS[name])


def _connect_args() -> dict:
    return {
        "connect_timeout": _setting("PG_CONNECT_TIMEOUT"),
        "options": (
            f"-c statement_timeout={_setting('PG_STATEMENT_TIMEOUT_MS')} "
            f"-c lock_timeout={_setting('PG_LOCK_TIMEOUT_MS')} "
            f"-c idle_in_transaction_session_timeout={_setting('PG_IDLE_TX_TIMEOUT_MS')}"
        ),
    }


def init_db(database_url: str) -> None:
    """Call once at app startup."""
    global _SessionLocal
    engine = create_engine(
        database_url, pool_pre_ping=True, connect_args=_connect_args()
    )
    _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """Return a new SQLAlchemy session. Caller must close it."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised — call init_db() first.")
    return _SessionLocal()
