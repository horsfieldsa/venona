"""Every statement that resolve runs.

Serves `pipeline/resolve.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_assertions(conn: db.Conn):
    return conn.execute(
        """
        SELECT a.entity_id_raw AS id, a.article_id, d.site_domain
          FROM assertions a
          LEFT JOIN documents d ON d.id = a.article_id
         WHERE a.retracted_at IS NULL
           AND a.method != 'seed'
           AND a.entity_id_raw IS NOT NULL
        
        """
    )


def update_entities(conn: db.Conn):
    return conn.execute("UPDATE entities SET mention_count = 0, source_count = 0")


def select_aliases(conn: db.Conn):
    """Ordered, because the resolver's FUZZY tie-break is insertion order.

    These rows build `_by_alias` and `_names`, which `process.extract` scores
    against; two aliases that score equally are separated by whichever came
    first, and `fuzzy_runnerup_margin` then decides whether the match is safe
    to take at all. Without an ORDER BY that is Postgres heap order, so the
    same corpus can resolve a surface to a different entity on a replay --
    which is the byte-identical-offline invariant failing in the one place
    nothing would look.
    """
    return conn.execute(
        "SELECT a.normalized, e.id, e.type, e.name FROM aliases a JOIN entities e ON e.id = "
        "a.entity_id ORDER BY e.id, a.normalized"
    )


def update_entities_by_status_last_seen(conn: db.Conn, p0, p1, p2, p3, p4):
    return conn.execute(
        "UPDATE entities SET status=?, last_seen=?, mention_count=?, source_count=? WHERE id=?",
        (p0, p1, p2, p3, p4),
    )


def update_entities_by_mention_count_source_count(conn: db.Conn, p0, p1, p2):
    return conn.execute(
        "UPDATE entities SET mention_count = ?, source_count = ? WHERE id = ?", (p0, p1, p2)
    )


def select_assertions_2(conn: db.Conn):
    return conn.execute("SELECT DISTINCT entity_id_raw FROM assertions WHERE method = 'seed'")


def select_aliases_2(conn: db.Conn):
    return conn.execute("SELECT DISTINCT entity_id FROM aliases WHERE added_by = 'manual'")


def select_entities(conn: db.Conn):
    return conn.execute(
        """
        SELECT e.id, e.status, a.article_id, a.created_at, d.site_domain
          FROM entities e
          LEFT JOIN assertions a
                 ON a.entity_id_raw = e.id AND a.retracted_at IS NULL
                AND a.method != 'seed'
          LEFT JOIN documents d ON d.id = a.article_id
        
        """
    )


def select_ambiguous_surfaces(conn: db.Conn):
    return conn.execute("SELECT normalized, readings FROM ambiguous_surfaces")


def select_reading_evidence(conn: db.Conn):
    """For every active entity: its name, and the name of the country it is
    based in when it has one.

    The two things a document can say that name WHICH reading it means. The home
    country rides along because the distinguishing word is usually the
    nationality rather than the organisation: an article about the Australian
    one says "Australia" throughout and may never spell out the full title.
    """
    return conn.execute(
        """
        SELECT e.id, e.name, c.name AS home
          FROM entities e
          LEFT JOIN assertions a
            ON a.subject_id_raw = e.id
           AND a.predicate = 'BASED_IN'
           AND a.kind = 'relation'
           AND a.retracted_at IS NULL
          LEFT JOIN entities c ON c.id = a.object_id_raw
         WHERE e.status = 'active'
        """
    )


def select_entities_by_id(conn: db.Conn, p0):
    return conn.execute("SELECT id, status FROM entities WHERE id = ?", (p0,))


def select_entities_by_id_2(conn: db.Conn, p0):
    return conn.execute("SELECT type FROM entities WHERE id = ?", (p0,))


def entity_evidence(conn: db.Conn, mappings: dict[str, str]) -> list[db.Row]:
    """Every entity's evidence, aggregated per SURVIVING id, in one scan.

    Replaces reading `entities LEFT JOIN assertions LEFT JOIN documents` into
    Python and folding it there -- one row per (entity, assertion) pair,
    materialised twice per run by two callers that both wanted the same three
    numbers.

    THE MERGE MAP IS PASSED IN, NOT DERIVED HERE, and that is the whole safety
    argument. `resolve_mappings` walks chains to a fixed point with a cycle
    guard; a one-hop JOIN would mis-credit A->B->C to B, and a recursive CTE
    reproducing that walk -- cycle break included -- would be a second
    implementation of the rule that decides whether an entity is in the graph
    at all. Instead the already-resolved map arrives as two arrays and the
    database only does the counting.

    The three columns, each the thing a caller actually asked for:

      articles  distinct `article_id`.
      sources   distinct publisher DOMAINS, never feeds. Two feeds from one
                outlet are one source of corroboration; counting `source_id`
                read them as two and promoted on evidence that was not there.
      newest    the newest ASSERTION timestamp, never `entities.last_seen` --
                which `promote_provisionals` itself rewrites, so reading it
                would reset the dormancy clock on every run and nothing would
                ever go quiet.

    `method != 'seed'` and `retracted_at IS NULL` scope it exactly as the
    Python did: seeded identity is not evidence, and a retraction removes it.
    """
    froms = list(mappings)
    tos = [mappings[key] for key in froms]
    return list(
        conn.execute(
            """
            SELECT COALESCE(m.to_id, a.entity_id_raw)   AS id,
                   COUNT(DISTINCT a.article_id)         AS articles,
                   COUNT(DISTINCT d.site_domain)        AS sources,
                   MAX(a.created_at)                    AS newest
              FROM assertions a
              LEFT JOIN unnest(?::text[], ?::text[]) AS m(from_id, to_id)
                     ON m.from_id = a.entity_id_raw
              LEFT JOIN documents d ON d.id = a.article_id
             WHERE a.retracted_at IS NULL
               AND a.method != 'seed'
               AND a.entity_id_raw IS NOT NULL
             GROUP BY 1
             ORDER BY 1
            """,
            (froms, tos),
        ).fetchall()
    )


def select_entity_statuses(conn: db.Conn):
    """The rows a promotion decision may be ABOUT.

    Narrower than the evidence above on purpose: evidence is gathered over
    every entity including merged-away ones -- their assertions are exactly
    what the survivor inherits -- while only these three statuses are ever
    re-decided.
    """
    return conn.execute(
        "SELECT id, status FROM entities "
        "WHERE status IN ('provisional', 'active', 'dormant') ORDER BY id"
    )


def update_entity_counts(conn: db.Conn, rows: list[tuple[str, int, int]]) -> None:
    """Every entity's mention and source counts, in ONE statement.

    The per-entity loop this replaces issued one UPDATE per entity with evidence
    -- thousands of round trips on a real corpus, to write values Python had
    already finished computing. The numbers written are identical; what changes
    is how many times the process waits for the database.

    `unnest` over three arrays rather than a generated VALUES list: the arrays go
    as parameters, so the statement text is fixed however many entities there
    are, and nothing has to be interpolated into SQL.
    """
    if not rows:
        return
    ids = [row[0] for row in rows]
    mentions = [row[1] for row in rows]
    sources = [row[2] for row in rows]
    conn.execute(
        """
        UPDATE entities AS e
           SET mention_count = v.mentions, source_count = v.sources
          FROM unnest(?::text[], ?::int[], ?::int[]) AS v(id, mentions, sources)
         WHERE e.id = v.id
        """,
        (ids, mentions, sources),
    )


def update_entity_statuses(conn: db.Conn, now: str, rows: list[tuple[str, str, int, int]]) -> None:
    """Every promotion, demotion and dormancy decision, in ONE statement.

    Same shape and same reason as `update_entity_counts`: the decisions are
    already made in Python, and issuing them one row at a time is a round trip
    per entity for no gain in correctness.
    """
    if not rows:
        return
    ids = [row[0] for row in rows]
    statuses = [row[1] for row in rows]
    mentions = [row[2] for row in rows]
    sources = [row[3] for row in rows]
    conn.execute(
        """
        UPDATE entities AS e
           SET status = v.status,
               last_seen = ?,
               mention_count = v.mentions,
               source_count = v.sources
          FROM unnest(?::text[], ?::text[], ?::int[], ?::int[])
               AS v(id, status, mentions, sources)
         WHERE e.id = v.id
        """,
        (now, ids, statuses, mentions, sources),
    )
