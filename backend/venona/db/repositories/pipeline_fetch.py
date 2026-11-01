"""Every statement that fetch runs.

Serves `pipeline/fetch.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_documents(conn: db.Conn):
    return conn.execute("SELECT url_key FROM documents")


def select_documents_by_url_key(conn: db.Conn, p0):
    return conn.execute("SELECT id, cluster_id FROM documents WHERE url_key = ? LIMIT 1", (p0,))


def select_documents_2(conn: db.Conn):
    return conn.execute("SELECT COUNT(*) AS n FROM documents")
