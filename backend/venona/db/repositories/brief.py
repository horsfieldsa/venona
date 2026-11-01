"""Every statement that brief_service runs.

Serves `services/brief_service.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def delete_brief_items_by_brief_id(conn: db.Conn, p0):
    return conn.execute("DELETE FROM brief_items WHERE brief_id = ?", (p0,))


def delete_briefs_by_id(conn: db.Conn, p0):
    return conn.execute("DELETE FROM briefs WHERE id = ?", (p0,))


# ── the archive ceiling ──────────────────────────────────────────────
#
# A lapsed reader keeps every brief published on or before the day their
# subscription ended, and sees nothing after it (ADR 0211). `ceiling` is that
# date, or None for no limit -- an entitled reader, an admin, an instance not
# enforcing billing.
#
# Applied HERE rather than at a route, for two reasons. Route-level refusal
# cannot express "up to a date", and `GET /api/briefs/latest` has to return the
# newest brief they MAY open rather than a 403 -- `/` renders it, so refusing
# would bounce them to the paywall and they would never reach the archive they
# were promised. And a repository is the only place SQL lives.
#
# `briefs.date` is indexed (`ix_briefs_date`), so this rides an existing index.


def _ceiling_clause(ceiling: str | None, *, alias: str = "", first: bool = True) -> str:
    """The SQL fragment, or nothing at all when there is no limit.

    A no-op for an entitled reader rather than a tautology like `date <=
    '9999'`: the common path should produce byte-identical SQL to the query
    that existed before a ceiling was possible.
    """
    if ceiling is None:
        return ""
    column = f"{alias}.date" if alias else "date"
    return f"{'WHERE' if first else 'AND'} {column} <= ?"


def _ceiling_params(ceiling: str | None) -> tuple:
    return (ceiling,) if ceiling is not None else ()


def select_brief_blocks_by_brief_id(conn: db.Conn, p0):
    return conn.execute(
        "SELECT spans FROM brief_blocks WHERE brief_id = ? ORDER BY section, ordinal", (p0,)
    )


def select_briefs_by_id(conn: db.Conn, p0, *, ceiling: str | None = None):
    """One brief by id. The ceiling matters MORE here than on the lists: this is
    a direct fetch, so a bookmarked or guessed id is the way past a filtered
    archive if it is missing."""
    return conn.execute(
        f"SELECT * FROM briefs WHERE id = ? {_ceiling_clause(ceiling, first=False)}",  # noqa: S608
        (p0, *_ceiling_params(ceiling)),
    )


def select_brief_blocks_by_brief_id_2(conn: db.Conn, p0):
    return conn.execute(
        "SELECT * FROM brief_blocks WHERE brief_id = ? ORDER BY section, ordinal", (p0,)
    )


def select_acronym_definitions(conn: db.Conn):
    return conn.execute(
        "SELECT id FROM acronym_definitions WHERE expansion IS NOT NULL "
        "AND status IN ('active', 'ambiguous')"
    )


def select_brief_items_by_brief_id(conn: db.Conn, p0):
    return conn.execute(
        """
        SELECT i.*, d.publisher, d.url, d.published_at
          FROM brief_items i
          LEFT JOIN clusters c ON c.id = i.cluster_id
          LEFT JOIN documents d ON d.id = c.canonical_doc_id
         WHERE i.brief_id = ?
      ORDER BY i.ord
        
        """,
        (p0,),
    )


def select_briefs(conn: db.Conn, *, ceiling: str | None = None):
    """The newest brief this reader may open. NOT a refusal when they are
    lapsed: `/` renders this, and a 403 would send them to the paywall instead
    of to the archive they paid for."""
    return conn.execute(
        "SELECT id FROM briefs "  # noqa: S608
        f"{_ceiling_clause(ceiling)} ORDER BY date DESC, generated_at DESC LIMIT 1",
        _ceiling_params(ceiling),
    )


def select_latest_brief_generated_at(conn: db.Conn):
    """When the newest shared brief was written, for "is there new reading".

    Ordered by `generated_at` alone rather than by `(date, generated_at)` like
    its neighbours above. Those answer "which brief does the archive show
    first", where the editorial date leads; this answers "when did reading last
    land", which is a wall-clock question a backfilled date must not reorder.
    """
    return conn.execute("SELECT generated_at FROM briefs ORDER BY generated_at DESC LIMIT 1")


def select_brief_blocks(conn: db.Conn, p0, p1, *, ceiling: str | None = None):
    return conn.execute(
        f"""
        SELECT b.id, b.date, b.title, b.dek, b.run_id, b.generated_at,
               (SELECT COUNT(*) FROM brief_blocks bb WHERE bb.brief_id = b.id) AS blocks
        FROM briefs b
        {_ceiling_clause(ceiling, alias="b")}
        ORDER BY b.date DESC, b.generated_at DESC
        LIMIT ? OFFSET ?
        """,  # noqa: S608 - a module constant, no caller input
        (*_ceiling_params(ceiling), p0, p1),
    )


def select_brief_blocks_2(conn: db.Conn):
    return conn.execute(
        """
        SELECT b.id, b.date, b.title, b.dek, b.run_id, b.generated_at,
               (SELECT COUNT(*) FROM brief_blocks bb WHERE bb.brief_id = b.id) AS blocks
        FROM briefs b
        ORDER BY b.date DESC, b.generated_at DESC
        
        """
    )


def select_brief_neighbours(conn: db.Conn, p0, *, ceiling: str | None = None):
    """The briefs either side of one, in archive order.

    LAG under a DESC window is the next-NEWER brief, LEAD the next-older —
    the same ORDER BY `select_brief_blocks` pages with, so the arrows walk
    exactly the sequence the archive lists.

    The ceiling goes INSIDE the window, not outside it. Filtering afterwards
    would compute the arrows over every brief and then hide one, handing a
    lapsed reader a working link to the brief immediately past their ceiling --
    which is the one they most want and least may have.
    """
    return conn.execute(
        f"""
        SELECT id, newer_id, older_id FROM (
            SELECT id,
                   LAG(id)  OVER w AS newer_id,
                   LEAD(id) OVER w AS older_id
              FROM briefs
              {_ceiling_clause(ceiling)}
            WINDOW w AS (ORDER BY date DESC, generated_at DESC)
        ) t WHERE id = ?
        """,  # noqa: S608 - a module constant, no caller input
        (*_ceiling_params(ceiling), p0),
    )


def select_briefs_by_id_2(conn: db.Conn, p0):
    return conn.execute("SELECT 1 FROM briefs WHERE id = ?", (p0,))


def select_briefs_2(conn: db.Conn, *, ceiling: str | None = None):
    """The total beside a page. Takes the ceiling too, or a lapsed reader is
    told the archive holds four hundred briefs and shown forty."""
    return conn.execute(
        f"SELECT COUNT(*) AS n FROM briefs {_ceiling_clause(ceiling)}",  # noqa: S608
        _ceiling_params(ceiling),
    )


def select_briefs_by_id_3(conn: db.Conn, p0):
    return conn.execute("SELECT 1 FROM briefs WHERE id = ?", (p0,))


def brief_items_matching(conn: db.Conn, brief_ids: list[str], like: str) -> list[db.Row]:
    """Matching headlines across a whole page of briefs, in one statement.

    The page is already bounded, but one statement per brief still means a round
    trip per row on the page -- and the caller can ask for two hundred. Grouping
    is the caller's job; `brief_id` leads the ORDER BY so each brief's rows stay
    in rank order inside its own group.
    """
    marks = ",".join("?" for _ in brief_ids)
    return conn.execute(
        f"SELECT brief_id, id, headline FROM brief_items "  # noqa: S608 - placeholders only
        f"WHERE brief_id IN ({marks}) AND headline ILIKE ? ORDER BY brief_id, rank",
        (*brief_ids, like),
    ).fetchall()


def brief_blocks_matching(conn: db.Conn, brief_ids: list[str], like: str) -> list[db.Row]:
    """Matching body text across a page of briefs. See `brief_items_matching`."""
    marks = ",".join("?" for _ in brief_ids)
    return conn.execute(
        f"""
        SELECT bb.brief_id, bb.id, bb.text, bb.item_id, bi.headline
          FROM brief_blocks bb
          LEFT JOIN brief_items bi ON bi.id = bb.item_id
         WHERE bb.brief_id IN ({marks}) AND bb.text ILIKE ?
         ORDER BY bb.brief_id, bb.section, bb.ordinal
        """,  # noqa: S608 - placeholders only
        (*brief_ids, like),
    ).fetchall()


def brief_blocks_referencing(conn: db.Conn, brief_ids: list[str], entity: str) -> dict[str, int]:
    """How many blocks of each brief name this entity.

    `spans` is a JSON array of `{start, end, ref, kind, confidence}`, matched by
    EQUALITY on `ref` through `jsonb_array_elements`, never a LIKE over the
    serialised column: a substring test would make `prog:ngc` match `prog:ngc2`,
    silently returning a superset. A brief with no mention has no row here, so
    the caller reads a default rather than a zero.
    """
    marks = ",".join("?" for _ in brief_ids)
    rows = conn.execute(
        f"""
        SELECT bb.brief_id, COUNT(*) AS n
          FROM brief_blocks bb,
               jsonb_array_elements(bb.spans::jsonb) s
         WHERE bb.brief_id IN ({marks}) AND s->>'ref' = ?
         GROUP BY bb.brief_id
        """,  # noqa: S608 - placeholders only
        (*brief_ids, entity),
    ).fetchall()
    return {row["brief_id"]: int(row["n"]) for row in rows}


def documents_by_id(conn: db.Conn, source_ids: list[str]) -> list[db.Row]:
    marks = ",".join("?" for _ in source_ids)
    return list(
        conn.execute(
            f"SELECT id, title, url, publisher, published_at FROM documents WHERE id IN ({marks})",  # noqa: S608 - marks only
            tuple(source_ids),
        ).fetchall()
    )


def count_for_brief(conn: db.Conn, table: str, brief_id: str) -> int:
    """`table` is one of a module-level tuple in `brief_service`, never a caller."""
    return int(
        conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE brief_id = ?",  # noqa: S608
            (brief_id,),
        ).fetchone()["n"]
    )


def sources_by_ids(conn: db.Conn, ids: list[str]) -> list[db.Row]:
    """Citation shells for the documents a brief was written from."""
    marks = ",".join("?" for _ in ids)
    return list(
        conn.execute(
            f"SELECT id, title, url, publisher, published_at FROM documents WHERE id IN ({marks})",  # noqa: S608
            tuple(ids),
        ).fetchall()
    )


#: One SQL predicate, shared by the candidate read and its count so the two can
#: never disagree about what matches. `?::text IS NULL` rather than `? IS NULL`:
#: a parameter whose only use is a NULL test gives Postgres nothing to infer a
#: type from, and it refuses with "could not determine data type".
_SEARCH_WHERE = """
    WHERE (?::text IS NULL OR (
              b.title ILIKE ? OR b.dek ILIKE ?
           OR EXISTS (SELECT 1 FROM brief_items i
                       WHERE i.brief_id = b.id AND i.headline ILIKE ?)
           OR EXISTS (SELECT 1 FROM brief_blocks bl
                       WHERE bl.brief_id = b.id AND bl.text ILIKE ?)
          ))
      AND (?::text IS NULL OR EXISTS (
              SELECT 1 FROM brief_blocks bb, jsonb_array_elements(bb.spans::jsonb) s
               WHERE bb.brief_id = b.id AND s->>'ref' = ?
          ))
"""


def select_briefs_matching(
    conn: db.Conn,
    needle: str | None,
    entity: str | None,
    limit: int,
    *,
    ceiling: str | None = None,
):
    """The briefs a search can return, newest first, bounded.

    The filter belongs in SQL. Read unfiltered, the caller walked EVERY brief and
    ran two or three more queries against each one, so one search cost
    O(briefs)x3 round trips and grew by one brief a day forever -- on an endpoint
    the MCP surface can also reach.
    """
    like = f"%{needle}%" if needle else None
    return conn.execute(
        f"""
        SELECT b.id, b.date, b.title, b.dek, b.run_id, b.generated_at,
               (SELECT COUNT(*) FROM brief_blocks bb WHERE bb.brief_id = b.id) AS blocks
        FROM briefs b
        {_SEARCH_WHERE}
        {_ceiling_clause(ceiling, alias="b", first=False)}
        ORDER BY b.date DESC, b.generated_at DESC
        LIMIT ?
        """,  # noqa: S608 - a module constant, no caller input
        (needle, like, like, like, like, entity, entity, *_ceiling_params(ceiling), limit),
    )


def count_briefs_matching(
    conn: db.Conn, needle: str | None, entity: str | None, *, ceiling: str | None = None
) -> int:
    """How many briefs match, for the honest `total` beside a bounded page."""
    like = f"%{needle}%" if needle else None
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM briefs b "  # noqa: S608 - see above
            f"{_SEARCH_WHERE} {_ceiling_clause(ceiling, alias='b', first=False)}",
            (needle, like, like, like, like, entity, entity, *_ceiling_params(ceiling)),
        ).fetchone()["n"]
    )
