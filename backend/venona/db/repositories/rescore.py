"""Every statement that rescore runs.

Serves `services/rescore.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def update_clusters_by_relevance_overall_tier(conn: db.Conn, p0, p1, p2):
    return conn.execute(
        "UPDATE clusters SET relevance_overall = ?, tier = ? WHERE id = ?", (p0, p1, p2)
    )


def select_clusters(conn: db.Conn):
    return conn.execute(
        "SELECT id, axes, relevance_overall, tier FROM clusters WHERE axes IS NOT NULL"
    )
