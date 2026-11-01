"""Every statement that dedup runs.

Serves `pipeline/dedup.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_documents(conn: db.Conn, p0, p1):
    return conn.execute(
        "SELECT id, cluster_id, title, published_at FROM documents WHERE published_at IS NOT NULL "
        "AND published_at BETWEEN ? AND ?",
        (p0, p1),
    )


def select_documents_by_url_key(conn: db.Conn, p0):
    return conn.execute("SELECT id, cluster_id FROM documents WHERE url_key = ? LIMIT 1", (p0,))


def select_documents_by_content_hash(conn: db.Conn, p0):
    return conn.execute(
        "SELECT id, cluster_id FROM documents WHERE content_hash = ? LIMIT 1", (p0,)
    )


def select_documents_2(conn: db.Conn):
    return conn.execute("SELECT DISTINCT cluster_id FROM documents")


def select_sightings(conn: db.Conn):
    return conn.execute(
        "SELECT cluster_id, COUNT(DISTINCT source_id) AS n FROM sightings GROUP BY cluster_id"
    )


def select_documents_by_cluster_id(conn: db.Conn, p0):
    return conn.execute(
        "SELECT id, source_id, title, published_at FROM documents WHERE cluster_id = ?", (p0,)
    )


def select_clusters_by_id(conn: db.Conn, p0):
    """The cluster as it stands, for the two decisions a refresh makes about it:
    keep its original `first_seen`, and notice whether its canonical document
    is being replaced."""
    return conn.execute("SELECT first_seen, canonical_doc_id FROM clusters WHERE id = ?", (p0,))
