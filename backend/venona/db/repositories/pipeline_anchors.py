"""Every statement that anchors runs.

Serves `pipeline/anchors.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_assertions(conn: db.Conn):
    return conn.execute(
        "SELECT subject_id_raw s, predicate p, object_id_raw o FROM assertions "
        "WHERE kind = 'relation' AND retracted_at IS NULL "
        "AND subject_id_raw IS NOT NULL AND object_id_raw IS NOT NULL"
    )


def select_entities(conn: db.Conn):
    return conn.execute("SELECT id FROM entities WHERE status = 'active'")


def select_assertions_2(conn: db.Conn):
    return conn.execute(
        """
            SELECT e.id, e.name, e.type, e.mention_count,
                   EXISTS (
                     SELECT 1 FROM assertions a
                      WHERE a.kind = 'relation' AND a.retracted_at IS NULL
                        AND (a.subject_id_raw = e.id OR a.object_id_raw = e.id)
                   ) AS has_assertions
              FROM entities e
             WHERE e.status = 'active'
          ORDER BY e.mention_count DESC, e.id
            
        """
    )


def select_assertions_by_run_id(conn: db.Conn, p0):
    return conn.execute(
        """
        SELECT e.id, e.name, e.type,
               (SELECT COUNT(*) FROM assertions a
                 WHERE a.kind = 'relation' AND a.retracted_at IS NULL
                   AND (a.subject_id_raw = e.id OR a.object_id_raw = e.id)) AS degree,
               (e.type = 'Country' OR e.id IN (
                 SELECT DISTINCT entity_id_raw FROM assertions
                  WHERE run_id = ? AND entity_id_raw IS NOT NULL)) AS declared
          FROM entities e
         WHERE e.status = 'active'
      ORDER BY degree DESC, e.id
        
        """,
        (p0,),
    )


def select_alias_conflicts(conn: db.Conn):
    return conn.execute("SELECT existing_entity_id, proposed_entity_id FROM alias_conflicts")


def select_entities_2(conn: db.Conn, p0, p1):
    return conn.execute("SELECT id, type FROM entities WHERE id IN (?, ?)", (p0, p1))


def select_entities_3(conn: db.Conn):
    return conn.execute("SELECT id, name FROM entities")


def select_assertions_by_subject_id_raw(conn: db.Conn, p0):
    return conn.execute(
        "SELECT object_id_raw AS p FROM assertions WHERE kind = 'relation' "
        "AND retracted_at IS NULL AND predicate = 'PART_OF' AND subject_id_raw = ? "
        "AND object_id_raw IS NOT NULL LIMIT 1",
        (p0,),
    )


def select_assertions_by_subject_id_raw_2(conn: db.Conn, p0):
    return conn.execute(
        "SELECT object_id_raw AS p FROM assertions WHERE kind = 'relation' "
        "AND retracted_at IS NULL AND predicate = 'PART_OF' AND subject_id_raw = ? "
        "AND object_id_raw IN (SELECT id FROM entities WHERE status = 'active')",
        (p0,),
    )
