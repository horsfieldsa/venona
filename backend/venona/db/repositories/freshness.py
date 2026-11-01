"""Every statement behind the per-domain freshness stamps.

Serves `services/freshness.py`. See `db/repositories/__init__.py`.
"""

from __future__ import annotations

from venona.db import core as db


def domain_stamps(conn: db.Conn, labelled: list[tuple[str, str]]) -> db.Row:
    """One SELECT of every domain's fingerprint terms, each aliased.

    The terms are SQL FRAGMENTS from `freshness.DOMAIN_TERMS`, a module constant
    -- never anything a caller supplies. Aliases are `{domain}_{index}`, generated
    here from that same table, and both halves matter: unaliased scalar subqueries
    do not get distinct column names, so a dict-shaped row would collapse them
    into one key and every domain after the first would hash the wrong values,
    silently and identically on every call.
    """
    sql = "SELECT " + ", ".join(f"{term} AS {alias}" for alias, term in labelled)
    return conn.execute(sql).fetchone()  # noqa: S608 - see docstring
