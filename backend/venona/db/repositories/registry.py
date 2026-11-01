"""Every statement that registry runs.

Serves `sources/registry.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def update_source_health_by_last_success_at_last_attempt_at(
    conn: db.Conn, p0, p1, p2, p3, p4, p5, p6
):
    return conn.execute(
        """
        UPDATE source_health
           SET last_success_at = ?, last_attempt_at = ?,
               consecutive_failures = 0, auto_disabled = 0, last_error = NULL,
               etag = ?, last_modified = ?,
               last_item_at = CASE WHEN ? > 0 THEN ? ELSE last_item_at END
         WHERE source_id = ?
        
        """,
        (p0, p1, p2, p3, p4, p5, p6),
    )


def update_source_health_by_source_id(conn: db.Conn, p0):
    return conn.execute(
        """
        UPDATE source_health
           SET auto_disabled = 0, consecutive_failures = 0, last_error = NULL
         WHERE source_id = ?
        
        """,
        (p0,),
    )


def update_source_health_by_last_attempt_at_last_error(conn: db.Conn, p0, p1, p2, p3):
    return conn.execute(
        """
        UPDATE source_health
           SET last_attempt_at = ?, last_error = ?,
               consecutive_failures = consecutive_failures + 1,
               auto_disabled = CASE
                 WHEN consecutive_failures + 1 >= ? THEN 1 ELSE auto_disabled END
         WHERE source_id = ?
        
        """,
        (p0, p1, p2, p3),
    )


def insert_source_health(conn: db.Conn, p0):
    return conn.execute(
        "INSERT INTO source_health (source_id) VALUES (?) ON CONFLICT DO NOTHING", (p0,)
    )


def select_source_health_by_source_id(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM source_health WHERE source_id = ?", (p0,))


def select_source_health_by_source_id_2(conn: db.Conn, p0):
    return conn.execute("SELECT consecutive_failures FROM source_health WHERE source_id = ?", (p0,))


def select_source_health(conn: db.Conn):
    return conn.execute("SELECT source_id FROM source_health WHERE auto_disabled = 1")
