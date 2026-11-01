"""Alembic environment. Contract C1.

TWO THINGS THIS DOES DIFFERENTLY FROM THE TEMPLATE, both deliberate:

  1. **The URL comes from `Settings`, never from `alembic.ini`.** A DSN in an ini
     file is a second place to configure the database, and the one people edit is
     always the other one. `VENONA_DATABASE_URL` is the single answer.
  2. **`target_metadata` is `db/tables.py`, which does NOT own the DDL.** The
     migrations do -- they carry the reasoning a generated `CREATE TABLE` cannot.
     So `--autogenerate` here is a DRAFTING AID: it tells you what changed, and
     the revision you keep should execute reviewed SQL rather than the emitted
     `op.create_table`. `tests/test_tables_metadata.py` is what keeps the two
     honest.

OFFLINE MODE IS NOT SUPPORTED, and that is a real refusal rather than an
omission: `--sql` emits a script against a schema nobody verified, and the whole
argument for Postgres here was that the suite runs against the engine that serves
production.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from venona.db import tables  # noqa: E402
from venona.settings import get_settings  # noqa: E402

config = context.config
# Only the `alembic` command line gets alembic.ini's logging. A programmatic
# `db.core.migrate(conn)` sets `configure_logging` False, because `fileConfig`
# disables every logger that already exists and replaces the root handler with
# alembic's WARNING-level console -- which is how the API's own lines (the
# scheduler's, the circuit breaker's) vanished from production after the first
# migrate, while alembic's kept arriving.
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

target_metadata = tables.metadata


def _url() -> str:
    """The database to migrate.

    `sqlalchemy.url` when a caller set it -- `db.core.migrate(conn)` does, so that
    it acts on the connection it was handed rather than on whatever the process's
    settings happen to point at. Falls back to `Settings` for `alembic` on the
    command line, which has no connection to inherit.
    """
    configured = config.get_main_option("sqlalchemy.url", None)
    url = configured or get_settings().database_url
    return url.replace("postgresql://", "postgresql+psycopg://")


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # `schema_migrations` is the pre-Alembic ledger and stays where it
            # is: it is what tells `0001`..`0003` they have already run on an
            # existing database. Alembic keeps its own in `alembic_version`, and
            # the baseline revision reconciles them.
            version_table="alembic_version",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit(
        "offline mode is not supported: `--sql` would emit a script against a "
        "schema nobody verified, and this project runs its suite against the "
        "engine that serves production precisely so that cannot happen."
    )
run_migrations_online()
