"""Every statement that ingest runs.

Serves `pipeline/ingest.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_documents_by_id(conn: db.Conn, p0):
    return conn.execute("SELECT cluster_id FROM documents WHERE id = ?", (p0,))
