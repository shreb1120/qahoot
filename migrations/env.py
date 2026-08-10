import os
import sys
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# Make the project root importable so models can be resolved.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

load_dotenv()

# Import Base and all models so autogenerate can see the full schema.
from db import Base   # noqa: E402
import models         # noqa: E402, F401

config = context.config

# Inject DATABASE_URL from environment, overriding the ini placeholder.
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL environment variable is not set.")
config.set_main_option("sqlalchemy.url", database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Bound how long DDL will *wait for a lock*, but not how long it may
        # run. A migration that takes two minutes to rewrite a table is fine; a
        # migration that sits waiting for ACCESS EXCLUSIVE behind an open
        # transaction is not, because every web request touching that table
        # queues behind it and the site stops with nothing in the logs.
        #
        # Failing fast turns "the site is down and nobody knows why" into "the
        # migration errored, run it again in a moment" — and the app's own
        # idle_in_transaction_session_timeout means the blocker clears itself.
        lock_timeout = os.environ.get("PG_MIGRATION_LOCK_TIMEOUT", "10s")
        connection.exec_driver_sql(f"SET lock_timeout = '{lock_timeout}'")

        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
