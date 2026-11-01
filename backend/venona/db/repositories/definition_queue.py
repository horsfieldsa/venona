"""Every statement that definition_queue runs.

Serves `services/definition_queue.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_acronym_definitions(conn: db.Conn):
    return conn.execute(
        """
        SELECT surface, normalized, expansion, id, method, confidence, status,
               corroboration_count, sighting_count, article_count, decided_at,
               last_attempt_at, attempt_count, entity_id,
               grounding_quote, grounding_url, grounding_article_id
          FROM acronym_definitions
         WHERE status IN ('active', 'ambiguous', 'undefined') AND entity_id IS NULL
         ORDER BY sighting_count DESC, surface
        
        """
    )
