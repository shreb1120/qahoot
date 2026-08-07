from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Shared declarative base — all models inherit from this.
class Base(DeclarativeBase):
    pass


_SessionLocal: sessionmaker | None = None


def init_db(database_url: str) -> None:
    """Call once at app startup."""
    global _SessionLocal
    engine = create_engine(database_url, pool_pre_ping=True)
    _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """Return a new SQLAlchemy session. Caller must close it."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised — call init_db() first.")
    return _SessionLocal()
