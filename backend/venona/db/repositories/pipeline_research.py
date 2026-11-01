"""Every statement that research runs.

Serves `pipeline/research.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def insert_source_health(conn: db.Conn, p0):
    return conn.execute(
        "INSERT INTO source_health (source_id) VALUES (?) ON CONFLICT DO NOTHING", (p0,)
    )


def select_documents(conn: db.Conn):
    return conn.execute("SELECT url_key FROM documents")


def stuck_entities(conn: db.Conn, *, entity_ids: list[str], limit: int) -> list[db.Row]:
    """Entities holding relationships out of the projection, worst first.

    `ready` counts the edges that would become visible the moment THIS name is
    confirmed -- the ones whose other end already is -- and it is what the order
    is on. `blocked` counts every edge the name sits on and breaks the tie. The
    two correlated subqueries count how much independent evidence the corpus
    already has, so a sweep spends on the name holding seven edges rather than
    the one mentioned once.

    `dormant` is included deliberately: an entity blocking five relationships is a
    research target, not a retired one, and the 30-day dormancy rule would
    otherwise remove exactly the entities most worth chasing.

    `entity_ids` narrows to a named set; empty means "whatever is worst".
    """
    sql = """
        WITH t AS (
            SELECT DISTINCT subject_id_raw AS s, predicate AS p, object_id_raw AS o
              FROM assertions
             WHERE kind = 'relation' AND retracted_at IS NULL
               AND subject_id_raw IS NOT NULL AND object_id_raw IS NOT NULL
        ),
        -- Each end of each edge, carrying the STATUS OF THE OTHER END. That
        -- second column is the whole ranking: an edge appears the moment BOTH
        -- its names are confirmed, so confirming a name whose partner is still
        -- unconfirmed reveals nothing at all.
        ends AS (
            SELECT t.s AS id, oe.status AS other_status
              FROM t JOIN entities oe ON oe.id = t.o
            UNION ALL
            SELECT t.o AS id, se.status AS other_status
              FROM t JOIN entities se ON se.id = t.s
        ),
        blocked AS (
            SELECT id, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE other_status = 'active') AS ready
              FROM ends GROUP BY id
        )
        SELECT e.id, e.name, e.type, e.status, b.n AS blocked, b.ready AS ready,
               (SELECT COUNT(DISTINCT a.article_id) FROM assertions a
                 WHERE a.entity_id_raw = e.id AND a.retracted_at IS NULL
                   AND a.method != 'seed') AS articles,
               (SELECT COUNT(DISTINCT d.site_domain) FROM assertions a
                  JOIN documents d ON d.id = a.article_id
                 WHERE a.entity_id_raw = e.id AND a.retracted_at IS NULL
                   AND a.method != 'seed') AS publishers
          FROM blocked b JOIN entities e ON e.id = b.id
         WHERE e.status IN ('provisional', 'dormant')
    """
    params: list = []
    if entity_ids:
        marks = ",".join("?" for _ in entity_ids)
        sql += f" AND e.id IN ({marks})"  # noqa: S608 - placeholders only
        params += entity_ids
    # READY FIRST, and the measurement behind that order. On the live corpus
    # only 893 of 2,079 blocked-edge slots have a confirmed partner, so an order
    # on total blocked spends at most 43% of a sweep on edges that could appear
    # at all: its top 500 names reach 429 such edges where this order's reach
    # 620.
    #
    # `b.n` breaks the tie rather than being absent. A name with no
    # confirmed partner is a second step and not a dead end: confirm it and its
    # neighbours become one-away in their turn, so it sorts last and stays
    # reachable by a run that asks for enough names.
    sql += " ORDER BY b.ready DESC, b.n DESC, e.relevance DESC, e.id LIMIT ?"
    params.append(limit)
    return list(conn.execute(sql, tuple(params)).fetchall())


def sweep_costs(conn: db.Conn, *, limit: int, harness: str) -> list[db.Row]:
    """What one corroboration SEARCH has actually cost lately.

    Per CALL, not per run: a run's cost divided by nothing is meaningless here
    because the number a person chooses is names, and names become searches at a
    fixed ten to one. `stage = 'fetch'` is the discovery call -- the sweep
    itself -- and excludes the analyze calls that follow it, which are priced
    per cluster and scale with what came back rather than with what was asked.

    THE DAILY'S AVERAGE IS THE WRONG NUMBER HERE, by a factor of 2.5. A search
    in this job asks for forty URL slots across ten names and measures about
    $2.50; the daily's narrower eight-tag sweeps measure about $0.97. Quoting
    the second in front of the first is how a run estimated at $30 spent $138.

    Scoped to one service for the reason `lookup_run_costs` gives: two services
    do not cost the same, and one may be metered while the other is not. Rows
    from before `runs.harness` existed are NULL and count as `claude_code`,
    which is the same allowance and the same argument.
    """
    return conn.execute(
        """
        SELECT l.cost_usd
          FROM llm_calls l JOIN runs r ON r.id = l.run_id
         WHERE r.kind = 'research' AND l.stage = 'fetch' AND l.cost_usd > 0
           AND (r.harness = ? OR (r.harness IS NULL AND ? = 'claude_code'))
      ORDER BY l.created_at DESC
         LIMIT ?
        """,
        (harness, harness, limit),
    ).fetchall()


def blocked_name_count(conn: db.Conn) -> int:
    """How many unconfirmed names are holding at least one relationship back.

    The population the sweep draws from, so the modal can say what fraction of
    it a number covers rather than leaving the reader to guess whether 500 is
    most of the problem or a twentieth of it.
    """
    row = conn.execute(
        """
        WITH t AS (
            SELECT DISTINCT subject_id_raw AS s, object_id_raw AS o
              FROM assertions
             WHERE kind = 'relation' AND retracted_at IS NULL
               AND subject_id_raw IS NOT NULL AND object_id_raw IS NOT NULL
        ),
        ends AS (SELECT s AS id FROM t UNION ALL SELECT o FROM t)
        SELECT COUNT(DISTINCT e.id) AS n
          FROM ends JOIN entities e ON e.id = ends.id
         WHERE e.status IN ('provisional', 'dormant')
        """
    ).fetchone()
    return int(row["n"] or 0) if row else 0
