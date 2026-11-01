"""Every statement that annotate_service runs.

Serves `services/annotate_service.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_run_events(conn: db.Conn):
    return conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM run_events)                          AS events,
               (SELECT COUNT(*) FROM aliases)                            AS aliases,
               (SELECT COUNT(*) FROM acronym_definitions)                AS glossary,
               (SELECT COUNT(*) FROM entity_mappings)                    AS merges
        
        """
    )


def select_entities(conn: db.Conn):
    return conn.execute("SELECT id FROM entities WHERE status='active'")
