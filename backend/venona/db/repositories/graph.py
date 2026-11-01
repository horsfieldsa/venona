"""Every statement that graph_service runs.

Serves `services/graph_service.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_assertions_by_predicate_subject_id_raw(
    conn: db.Conn, predicate: str, sources: list[str], targets: list[str], *, limit: int
):
    """Every assertion behind one edge, over both endpoints' merge closures.

    The endpoints arrive as closures rather than as single ids because
    assertions keep the ids they were written with (C1). Matching the survivor
    alone would drop everything the loser contributed, so a merge would REDUCE
    an edge's evidence -- and `source_count` is computed from these rows, which
    is how a merge came to make a corroborated edge read single-source.

    BOUNDED. A well-covered edge between two hubs carries every quote ever
    written for it, and the panel shows a handful; the entity page then expands
    one of these per connection row. `a.id` makes the cut total so the same
    `limit` keeps the same quotes rather than a different arbitrary slice each
    time. `source_count` is computed from the rows returned, so a cut deep
    enough to change it would understate corroboration -- which is why the
    caller's ceiling is set well above any real edge and the count it reports is
    checked against `assertion_count`.
    """
    src = ",".join("?" for _ in sources)
    tgt = ",".join("?" for _ in targets)
    return conn.execute(
        f"""
        SELECT a.id, a.article_id, a.evidence_quote, a.confidence, a.method,
               a.model, a.created_at,
               d.title, d.url, d.publisher, d.published_at, d.site_domain
          FROM assertions a
          LEFT JOIN documents d ON d.id = a.article_id
         WHERE a.kind = 'relation' AND a.predicate = ? AND a.retracted_at IS NULL
           AND ((a.subject_id_raw IN ({src}) AND a.object_id_raw IN ({tgt}))
             OR (a.subject_id_raw IN ({tgt}) AND a.object_id_raw IN ({src})))
      -- An undated document must not count as the most recent.
      -- NULLS LAST is explicit: SQLite sorts NULLs first on ASC and so last on
      -- DESC; Postgres does the opposite. Without it a NULL sorts to the TOP,
      -- which is the wrong end and would be invisible.
      ORDER BY d.published_at DESC NULLS LAST, a.created_at DESC, a.id DESC
         LIMIT ?
        """,  # noqa: S608 - marks only
        (predicate, *sources, *targets, *targets, *sources, limit),
    )


def query_briefs_by_predicate_subject_id_raw(
    conn: db.Conn, predicate: str, sources: list[str], targets: list[str]
):
    src = ",".join("?" for _ in sources)
    tgt = ",".join("?" for _ in targets)
    return conn.execute(
        f"""
            -- Same filters as the assertion query above, and for the same
            -- reason: without `kind = 'relation'` an entity assertion sharing
            -- the predicate column value matches, and without `retracted_at IS
            -- NULL` a brief that only ever cited the withdrawn claim is listed
            -- as provenance for an edge that no longer rests on it.
            SELECT DISTINCT b.id, b.title, b.date, b.generated_at
              FROM briefs b
             WHERE EXISTS (
               -- `source_ids` is a TEXT column holding a JSON array, hence the
               -- cast. `_text` rather than `jsonb_array_elements` so the elements
               -- come out as text and compare to `article_id` directly instead of
               -- as quoted JSON scalars. A NULL `source_ids` yields no rows,
               -- which is the right answer for EXISTS.
               SELECT 1 FROM jsonb_array_elements_text(b.source_ids::jsonb) AS j(article_id)
                WHERE j.article_id IN (SELECT article_id FROM assertions
                                   WHERE kind = 'relation' AND predicate = ?
                                     AND retracted_at IS NULL AND
                                     ((subject_id_raw IN ({src})
                                       AND object_id_raw IN ({tgt}))
                                   OR (subject_id_raw IN ({tgt})
                                       AND object_id_raw IN ({src}))))
             )
          ORDER BY b.date DESC, b.generated_at DESC LIMIT 5
        """,  # noqa: S608 - marks only
        (predicate, *sources, *targets, *targets, *sources),
    )


def select_entities_by_id(conn: db.Conn, p0):
    return conn.execute("SELECT id, name, type FROM entities WHERE id = ?", (p0,))


def entity_names(conn: db.Conn, node_ids: list[str]) -> list[db.Row]:
    marks = ",".join("?" for _ in node_ids)
    return list(
        conn.execute(
            f"SELECT id, name FROM entities WHERE id IN ({marks})",  # noqa: S608 - marks only
            tuple(node_ids),
        ).fetchall()
    )


#: The advisory-lock namespace one projection at a time lives in. Distinct from
#: the run lease and the lane gate (`db/repositories/run.py`) because all three
#: can be held on one connection at once, and a shared namespace would let a
#: lane name collide with this constant.
PROJECTION_NAMESPACE = 0x56454E50  # "VENP"
PROJECTION_KEY = 1


def lock_projection(conn: db.Conn, *, wait: bool) -> bool:
    """Take the projection gate. True when this caller holds it.

    The lock is SESSION-scoped, and that choice is the whole design. A projection
    is a long conversation with Neo4j -- minutes on a real corpus -- so a
    transaction-scoped lock would hold a Postgres transaction open for all of it,
    pinning a snapshot and blocking vacuum for the duration. This one is released
    explicitly in a `finally`, and by Postgres if the connection dies, which is
    the same guarantee the run lease relies on.

    `wait=False` is for a caller that would rather say "busy" than queue behind
    a pass that could take minutes -- the curation rebuild button, which has a
    person waiting on the answer.
    """
    statement = "pg_advisory_lock" if wait else "pg_try_advisory_lock"
    row = conn.execute(
        f"SELECT {statement}(?, ?) AS ok",  # noqa: S608 - a literal chosen from two
        (PROJECTION_NAMESPACE, PROJECTION_KEY),
    ).fetchone()
    # The blocking form returns void, which arrives as None: reaching this line
    # at all IS the acquisition.
    return True if wait else bool(row["ok"])


def unlock_projection(conn: db.Conn) -> None:
    conn.execute(
        "SELECT pg_advisory_unlock(?, ?)", (PROJECTION_NAMESPACE, PROJECTION_KEY)
    ).fetchone()
