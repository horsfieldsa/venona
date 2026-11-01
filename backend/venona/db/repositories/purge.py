"""Every statement that purge runs.

Serves `services/purge.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_information_schema_by_table_name(conn: db.Conn, p0):
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = ?",
        (p0,),
    )


def count_rows(conn: db.Conn, table: str) -> int:
    """`table` comes from `purge.CORPUS_TABLES`, a module constant, and is checked
    against `information_schema` before this is called -- never from a caller."""
    return int(conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"])  # noqa: S608


def delete_all(conn: db.Conn, table: str) -> None:
    """Same constraint as `count_rows`: a module constant, existence-checked."""
    conn.execute(f'DELETE FROM "{table}"')  # noqa: S608
