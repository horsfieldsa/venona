"""Every statement that prompts runs.

Serves `llm/prompts.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_aliases(conn: db.Conn, p0):
    return conn.execute(
        """
        SELECT e.id, e.type, e.name,
               -- `string_agg` replaces SQLite's `GROUP_CONCAT`. The `ORDER BY`
               -- goes INSIDE the aggregate: the inner subquery's ORDER BY picks
               -- WHICH six aliases, but says nothing about the order they are
               -- concatenated in, and Postgres does not promise to preserve it.
               -- This string is part of a prompt whose cache key the docstring
               -- above is about, so a varying order is a silent cache miss on
               -- every call in the run. `a.surface` is the tiebreaker, because
               -- equal-length surfaces were previously ordered arbitrarily.
               (SELECT string_agg(a.surface, ' | '
                                  ORDER BY LENGTH(a.surface) DESC, a.surface)
                  FROM (SELECT surface FROM aliases
                         WHERE entity_id = e.id
                         ORDER BY LENGTH(surface) DESC LIMIT 6) a) AS aliases
        FROM entities e
        WHERE e.status = 'active'
        ORDER BY e.relevance DESC, e.id ASC
        LIMIT ?
        
        """,
        (p0,),
    )
