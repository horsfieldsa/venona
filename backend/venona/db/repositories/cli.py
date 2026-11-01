"""Every statement that cli runs.

Serves `cli.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_trunk_counts(conn: db.Conn):
    return conn.execute(
        "SELECT (SELECT COUNT(*) FROM entities WHERE status = 'active') AS active_entities, "
        "(SELECT COUNT(*) FROM ambiguous_surfaces) AS ambiguous_surfaces"
    )


def select_aliases_by_entity_id(conn: db.Conn, p0):
    return conn.execute(
        "SELECT surface, strictness, added_by FROM aliases "
        "WHERE entity_id = ? ORDER BY LENGTH(surface) DESC",
        (p0,),
    )


def select_assertions_by_subject_id_raw_object_id_raw(conn: db.Conn, p0, p1):
    return conn.execute(
        """
SELECT predicate, subject_id_raw s, object_id_raw o,
                  COUNT(DISTINCT article_id) n
             FROM assertions
            WHERE kind='relation' AND (subject_id_raw=? OR object_id_raw=?)
         GROUP BY predicate, s, o ORDER BY n DESC LIMIT 12
        """,
        (p0, p1),
    )


def select_assertions(conn: db.Conn):
    return conn.execute(
        "SELECT subject_id_raw s, predicate p, object_id_raw o FROM assertions WHERE kind = "
        "'relation' AND retracted_at IS NULL   AND subject_id_raw IS NOT NULL AND object_id_raw "
        "IS NOT NULL"
    )


def delete_aliases_by_id(conn: db.Conn, p0):
    return conn.execute("DELETE FROM aliases WHERE id = ?", (p0,))


def select_entities_by_id(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM entities WHERE id = ?", (p0,))


def select_documents(conn: db.Conn):
    return conn.execute(
        "SELECT title, url, text_nfc FROM documents ORDER BY published_at DESC NULLS LAST LIMIT 1"
    )


def select_source_health(conn: db.Conn):
    return conn.execute("SELECT * FROM source_health ORDER BY source_id")


def select_documents_2(conn: db.Conn):
    return conn.execute("SELECT COUNT(*) AS n FROM documents")


def select_documents_3(conn: db.Conn):
    return conn.execute("SELECT COUNT(*) AS n FROM documents")


def select_aliases(conn: db.Conn):
    return conn.execute(
        """
            SELECT a.id, a.surface, e.name, e.status
              FROM aliases a JOIN entities e ON e.id = a.entity_id
             WHERE a.added_by = 'extraction' AND a.normalized != e.canonical_key
             ORDER BY e.status DESC, a.surface
            
        """
    )


def select_aliases_by_id(conn: db.Conn, p0):
    return conn.execute(
        "SELECT a.surface, e.name FROM aliases a "
        "JOIN entities e ON e.id = a.entity_id WHERE a.id = ?",
        (p0,),
    )


# `select_access_requests` and `update_access_requests_by_status_decided_at` used
# to live here, and `insert_access_request` lived in `curation.py`. All three moved
# to `db/repositories/access.py` -- one module per aggregate, named after the table
# it is mostly about. The CLI's read did not return `id`, because the CLI names a
# request by ADDRESS; an HTTP queue cannot, so the new projection carries it.
