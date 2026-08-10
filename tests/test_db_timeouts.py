"""Every wait against Postgres is bounded.

None of them were. The app serves on eight Waitress threads against a pool of
five, so one statement that never returns — a lock held by a migration, a query
behind a long-open transaction — took a thread with it. Enough of those and the
site hangs with no error anywhere and needs a manual pg_terminate_backend.

These assert against a live session rather than the config dict, because the
settings only matter if they actually reach the server.
"""
import pytest
from sqlalchemy import text

import db


def test_the_session_really_has_the_timeouts_set(app):
    """The check that matters: what Postgres thinks, not what we passed."""
    s = db.get_db()
    try:
        got = {
            name: s.execute(text(f"SHOW {name}")).scalar()
            for name in ("statement_timeout", "lock_timeout",
                         "idle_in_transaction_session_timeout")
        }
    finally:
        s.close()

    assert got["statement_timeout"] not in ("0", "0ms"), \
        "statement_timeout is unset — one slow query can hold a thread forever"
    assert got["lock_timeout"] not in ("0", "0ms"), \
        "lock_timeout is unset — a blocked statement waits indefinitely"
    assert got["idle_in_transaction_session_timeout"] not in ("0", "0ms"), \
        "a leaked transaction could hold locks indefinitely"


def test_a_statement_that_runs_too_long_is_cancelled(app):
    """Proves the timeout has teeth rather than merely being reported."""
    s = db.get_db()
    try:
        s.execute(text("SET statement_timeout = '250ms'"))
        with pytest.raises(Exception) as exc:
            s.execute(text("SELECT pg_sleep(3)"))
        assert "timeout" in str(exc.value).lower() or "canceling" in str(exc.value).lower()
    finally:
        s.rollback()
        s.close()


def test_a_connection_timeout_is_configured():
    """Without it a dead database host hangs the worker on connect, before any
    statement timeout could apply."""
    assert db._connect_args()["connect_timeout"] > 0


def test_the_timeouts_are_tunable_without_a_code_change(monkeypatch):
    """These are the numbers most likely to need changing under load, and the
    worst time to need a deploy is while the database is struggling."""
    monkeypatch.setenv("PG_STATEMENT_TIMEOUT_MS", "1234")
    monkeypatch.setenv("PG_LOCK_TIMEOUT_MS", "567")
    opts = db._connect_args()["options"]
    assert "statement_timeout=1234" in opts
    assert "lock_timeout=567" in opts


def test_migrations_bound_their_lock_wait_but_not_their_runtime():
    """A migration may legitimately run for minutes; it must never *wait* for a
    lock for minutes, because every request touching that table queues behind
    it. Asserted on the source, since running Alembic here would migrate the
    test database mid-suite."""
    src = open("migrations/env.py").read()
    # The executed statement, not the word: an earlier version of this test
    # checked for "lock_timeout" and passed against a build where only the
    # variable assignment survived and the SET had been removed.
    assert "SET lock_timeout" in src, \
        "migrations do not bound their lock wait — DDL can block the whole site"
    assert "SET statement_timeout" not in src, \
        "a statement timeout would abort legitimately long DDL"
