"""Every statement that types runs.

Serves `pipeline/types.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_briefs(conn: db.Conn):
    return conn.execute("SELECT COUNT(*) AS n FROM briefs")
