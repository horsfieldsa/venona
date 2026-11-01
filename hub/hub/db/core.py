"""Postgres access, hub edition.

Much smaller than the instance's `venona/db/core.py` because the two reasons
that shim exists do not apply here: there is no SQLite heritage to translate
(`%s` is written natively) and no 314-site migration to stage (SQL is born in
`hub/repositories/`). psycopg's own connection is the right surface.

AUTOCOMMIT IS STILL LOAD-BEARING, same as the instance: without it every write
outside an explicit `conn.transaction()` block opens an implicit transaction
that is discarded when the per-request connection closes -- silent, total
write loss, invisible to any test that reads its own uncommitted writes back.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg.rows import DictRow, dict_row

Conn = psycopg.Connection[DictRow]


def connect(dsn: str) -> Conn:
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)


def ensure_database(dsn: str) -> bool:
    """Create the DSN's database if it does not exist. Returns True if created.

    CREATE DATABASE cannot run inside a transaction and cannot run from a
    connection to the database being created, so this talks to the same
    server's `postgres` maintenance database. A convenience for `hub migrate`
    and the local rehearsal; production compose creates the database via
    POSTGRES_DB and never takes this path.
    """
    parts = urlsplit(dsn)
    name = parts.path.lstrip("/")
    admin_dsn = urlunsplit(parts._replace(path="/postgres"))
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if exists:
            return False
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
        return True


def jdump(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
