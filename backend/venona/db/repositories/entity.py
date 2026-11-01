"""Every statement that entity_service runs.

Serves `services/entity_service.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_entities_by_id(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM entities WHERE id = ?", (p0,))


def select_aliases_by_entity_id(conn: db.Conn, p0):
    return conn.execute(
        "SELECT surface FROM aliases WHERE entity_id = ? ORDER BY LENGTH(surface) DESC", (p0,)
    )


def select_entity_definitions_by_entity_id(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM entity_definitions WHERE entity_id = ?", (p0,))


def select_acronym_definitions_by_id(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM acronym_definitions WHERE id = ?", (p0,))


def select_acronym_definitions_by_normalized(conn: db.Conn, p0, p1):
    return conn.execute(
        "SELECT * FROM acronym_definitions WHERE normalized = ? AND id != ? "
        "AND status != 'rejected' ORDER BY confidence DESC",
        (p0, p1),
    )


def select_documents_by_id(conn: db.Conn, p0):
    return conn.execute("SELECT title, url FROM documents WHERE id = ?", (p0,))


def select_acronym_definitions_by_normalized_2(conn: db.Conn, p0):
    return conn.execute(
        "SELECT * FROM acronym_definitions WHERE normalized = ? ORDER BY confidence DESC LIMIT 1",
        (p0,),
    )


def select_documents_by_id_2(conn: db.Conn, p0):
    return conn.execute("SELECT title, url, published_at FROM documents WHERE id = ?", (p0,))


def neighbour_assertions(conn: db.Conn, members: list[str], *, limit: int) -> list[db.Row]:
    """Relations touching any id in an entity's merge closure, either end.

    MERGE-AWARE ON BOTH SIDES. Assertions store the ids they were written with
    (C1), so querying the survivor alone loses every neighbour that arrived via
    the loser -- merging would make a card *less* informative.

    BOUNDED, and ordered before it is bounded: a hub entity sits on tens of
    thousands of relations (2026-08-26) and the card draws `MINI_GRAPH_LIMIT` of
    them. Newest ASSERTION first, not newest article, so the cut keeps the
    recent neighbourhood; `created_at, id` makes the cut itself total rather
    than whatever the heap returned.

    `marks` is one `?` per member, so nothing is interpolated but the count.
    """
    marks = ",".join("?" for _ in members)
    return list(
        conn.execute(
            f"""
            SELECT a.predicate, a.subject_id_raw AS src, a.object_id_raw AS dst, a.article_id
            FROM assertions a
            WHERE a.kind = 'relation' AND a.retracted_at IS NULL
              AND (a.subject_id_raw IN ({marks}) OR a.object_id_raw IN ({marks}))
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,  # noqa: S608 - placeholders only, one per closure member
            (*members, *members, limit),
        ).fetchall()
    )


def neighbour_entities(conn: db.Conn, ids: list[str], *, active_only: bool) -> list[db.Row]:
    """Every neighbour a card needs, in one read.

    `active_only=False` is the CURATION view. A reader must only be shown
    corroborated entities, but a curator deciding whether two provisional
    entities are the same thing needs to see who each is connected to -- and
    those neighbours are provisional too.

    A SET rather than one id: the card is on the hot path for every hover and a
    brief's `cards:batch` asks for as many as sixty at once (2026-08-26), so one
    query per distinct neighbour is a few hundred round trips for one page load.
    """
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    status_filter = " AND status = 'active'" if active_only else ""
    return list(
        conn.execute(
            "SELECT id, name, type, relevance FROM entities "  # noqa: S608
            f"WHERE id IN ({marks}){status_filter}",
            tuple(ids),
        ).fetchall()
    )


def signal_articles(conn: db.Conn, members: list[str], limit: int) -> list[db.Row]:
    """Recent articles behind an entity, across its whole merge closure."""
    marks = ",".join("?" for _ in members)
    return list(
        conn.execute(
            f"""
            SELECT DISTINCT d.id, d.title, d.url, d.publisher, d.published_at,
                   c.relevance_overall
            FROM assertions a
            JOIN documents d ON d.id = a.article_id
            LEFT JOIN clusters c ON c.id = d.cluster_id
            WHERE a.retracted_at IS NULL
              AND (a.entity_id_raw IN ({marks})
                OR a.subject_id_raw IN ({marks})
                OR a.object_id_raw IN ({marks}))
            ORDER BY d.published_at DESC NULLS LAST
            LIMIT ?
            """,  # noqa: S608 - placeholders only, one per closure member
            (*members, *members, *members, limit),
        ).fetchall()
    )


def neighbourhood_assertions(conn: db.Conn, members: list[str], *, limit: int) -> list[db.Row]:
    """Like `neighbour_assertions`, but carrying confidence and timestamps.

    A SEPARATE function rather than widening the other one: the card's mini-graph
    reads four columns on the hover path and this reads seven to aggregate an edge's
    strength and its first/last sighting. Collapsing them cost a `KeyError:
    'confidence'` the moment the narrower query was handed to this caller.

    `site_domain` rides along because an edge's strength is its number of distinct
    DOMAINS, which is what `graph_service.edge_provenance` counts for the same edge
    and for the reason recorded there -- `documents.publisher` is a display byline.
    Counting the articles here instead made the two disagree in the same agent
    turn, one calling an edge corroborated and the other single-source.

    Both endpoints must be non-NULL here -- an edge needs two ends -- where the
    mini-graph tolerates a dangling one and skips it in Python.

    Bounded and ordered for `neighbour_assertions`'s reason, and paid again on
    every "load more" because this is read per connections page rather than per
    hover. `created_at, id` makes the cut total, which is what lets the caller's
    ranking be stable across pages.
    """
    marks = ",".join("?" for _ in members)
    return list(
        conn.execute(
            f"""
            SELECT a.predicate, a.subject_id_raw AS src, a.object_id_raw AS dst,
                   a.confidence, a.article_id, a.created_at, d.site_domain
            FROM assertions a
            LEFT JOIN documents d ON d.id = a.article_id
            WHERE a.kind = 'relation' AND a.retracted_at IS NULL
              AND a.subject_id_raw IS NOT NULL AND a.object_id_raw IS NOT NULL
              AND (a.subject_id_raw IN ({marks}) OR a.object_id_raw IN ({marks}))
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,  # noqa: S608 - placeholders only, one per closure member
            (*members, *members, limit),
        ).fetchall()
    )


def documents_by_ids(conn: db.Conn, ids: list[str]) -> list[db.Row]:
    """Citation shells for a definition's recorded sources."""
    marks = ",".join("?" for _ in ids)
    return list(
        conn.execute(
            "SELECT id, title, url, publisher, published_at FROM documents "  # noqa: S608
            f"WHERE id IN ({marks})",
            tuple(ids),
        ).fetchall()
    )


def active_nodes_by_ids(conn: db.Conn, ids: list[str]) -> list[db.Row]:
    """Graph nodes for a fixed id set, `active` only.

    The status filter is what keeps a provisional endpoint out of the explorer;
    the caller then drops every edge whose endpoints did not survive.
    """
    marks = ",".join("?" for _ in ids)
    return list(
        conn.execute(
            "SELECT id, name, type, relevance, status FROM entities "  # noqa: S608
            f"WHERE id IN ({marks}) AND status = 'active'",
            tuple(ids),
        ).fetchall()
    )


def search_entities(
    conn: db.Conn, *, like: str, like_normalized: str, statuses: tuple[str, ...], limit: int
) -> list[db.Row]:
    """Name-or-alias search, active first, then shortest name.

    Two patterns because the two columns hold two different forms: `e.name` is
    written the way a publication writes it and `a.normalized` has been through
    `normalize`, which collapses punctuation. Matching one pattern against both
    means an alias is only reachable by a query whose punctuation already agrees
    with the normalised form, and `U.S. Navy` is exactly the query that does not.

    The ordering terms are SELECTED in an inner query and ordered on in the outer
    one, because Postgres requires every ORDER BY expression of a `SELECT
    DISTINCT` to appear in its select list and the direct form is a 500 at
    request time rather than a syntax error at import. Ordering in the inner
    query instead would not work either: DISTINCT is applied after ORDER BY, so
    the outer query is free to reorder the survivors.

    The outer projection strips the terms again, so the row shape is what the
    caller expects: it pops a key based on that shape, and `response_model`
    filters against it.
    """
    marks = ",".join("?" * len(statuses))
    return list(
        conn.execute(
            f"""
            SELECT id, name, type, relevance, status FROM (
                SELECT DISTINCT e.id, e.name, e.type, e.relevance, e.status,
                       CASE e.status WHEN 'active' THEN 0 ELSE 1 END AS status_rank,
                       LENGTH(e.name) AS name_length
                FROM entities e
                LEFT JOIN aliases a ON a.entity_id = e.id
                WHERE e.status IN ({marks})
                  AND (LOWER(e.name) LIKE ? OR a.normalized LIKE ?)
            ) ranked
            ORDER BY status_rank, relevance DESC, name_length
            LIMIT ?
            """,  # noqa: S608 - placeholders only
            (*statuses, like, like_normalized, limit),
        ).fetchall()
    )


def alias_surfaces_for_members(conn: db.Conn, members: list[str]) -> list[db.Row]:
    """Alias surfaces across an entity's whole merge closure.

    `graph/projector.py` folds a loser's aliases onto the survivor when it
    projects, and its comment records what breaks otherwise: `/graph/search`
    stops finding a node that was findable before the merge. Reading the
    survivor alone here made the card disagree with graph search about what a
    name goes by -- the survivor's own surfaces only, with every surface the
    absorbed name was known by silently gone.

    The inner query carries the length term for the reason `search_entities`
    records; the outer projection drops it again so the row shape is `surface`.
    """
    marks = ",".join("?" for _ in members)
    return list(
        conn.execute(
            f"""
            SELECT surface FROM (
                SELECT DISTINCT surface, LENGTH(surface) AS surface_length
                FROM aliases WHERE entity_id IN ({marks})
            ) ranked
            ORDER BY surface_length DESC, surface
            """,  # noqa: S608 - placeholders only, one per closure member
            tuple(members),
        ).fetchall()
    )


def signal_articles_page(
    conn: db.Conn, members: list[str], *, limit: int, offset: int
) -> list[db.Row]:
    """One window of the articles behind an entity, across its merge closure.

    `signal_articles` is the card's four and orders on `published_at` alone.
    That is fine for a top-N and wrong for a window: this corpus carries many
    documents published the same day and some with no date at all, so ties
    resolve to whatever the heap returned and a row can repeat on one page and
    vanish from the next. `d.id` makes the order total.

    Both ordering terms are in the select list because a `SELECT DISTINCT`
    requires it (see `search_entities`, which learned this as a 500).
    """
    marks = ",".join("?" for _ in members)
    return list(
        conn.execute(
            f"""
            SELECT DISTINCT d.id, d.title, d.url, d.publisher, d.published_at,
                   c.relevance_overall
            FROM assertions a
            JOIN documents d ON d.id = a.article_id
            LEFT JOIN clusters c ON c.id = d.cluster_id
            WHERE a.retracted_at IS NULL
              AND (a.entity_id_raw IN ({marks})
                OR a.subject_id_raw IN ({marks})
                OR a.object_id_raw IN ({marks}))
            ORDER BY d.published_at DESC NULLS LAST, d.id DESC
            LIMIT ? OFFSET ?
            """,  # noqa: S608 - placeholders only, one per closure member
            (*members, *members, *members, limit, offset),
        ).fetchall()
    )


def count_signal_articles(conn: db.Conn, members: list[str]) -> int:
    """How many articles the window above is a window ONTO.

    `COUNT(DISTINCT d.id)`, and the WHERE is kept identical to
    `signal_articles_page` on purpose: a total counting a different set from the
    one being paged is how a footer tells a reader to load more of nothing.
    """
    marks = ",".join("?" for _ in members)
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT d.id) AS n
        FROM assertions a
        JOIN documents d ON d.id = a.article_id
        WHERE a.retracted_at IS NULL
          AND (a.entity_id_raw IN ({marks})
            OR a.subject_id_raw IN ({marks})
            OR a.object_id_raw IN ({marks}))
        """,  # noqa: S608 - placeholders only, one per closure member
        (*members, *members, *members),
    ).fetchone()
    return int(row["n"] or 0) if row else 0


def aliases_with_provenance(conn: db.Conn, members: list[str], *, limit: int) -> list[db.Row]:
    """Every surface a name is known by, and where each one came from.

    LEFT JOIN, not JOIN: `aliases.evidence_article_id` has no foreign key and is
    NULL for every seeded alias and every surface a curator attaches by hand
    (`glossary.py::upsert_manual_alias` never writes it). An inner join would drop the
    surfaces whose origin is the thing being asked about, leaving a panel that
    looks complete and is not.
    """
    marks = ",".join("?" for _ in members)
    return list(
        conn.execute(
            f"""
            SELECT a.surface, a.strictness, a.is_acronym, a.added_by, a.entity_id,
                   a.evidence_article_id, d.title AS article_title, d.url AS article_url
            FROM aliases a
            LEFT JOIN documents d ON d.id = a.evidence_article_id
            WHERE a.entity_id IN ({marks})
            ORDER BY a.strictness DESC, LENGTH(a.surface) DESC, a.surface
            LIMIT ?
            """,  # noqa: S608 - placeholders only, one per closure member
            (*members, limit),
        ).fetchall()
    )


def merges_for_members(conn: db.Conn, members: list[str], *, limit: int) -> list[db.Row]:
    """The merges that produced this name, with who ruled on each.

    `members` is the caller's merge CLOSURE rather than one id, because
    `resolve_mappings` collapses chains to a fixed point: for A -> B -> C a
    query for `to_id = C` returns B alone, and A is missing from the one screen
    whose whole job is to say where the name came from.

    `merged_by` is LEFT JOINed to an address HERE rather than looked up per row.
    C1 is explicit about why: a lookup per row is the 201-queries-for-200-rows
    shape again. NULL is honest and common -- the CLI and the pipeline both
    record it.
    """
    marks = ",".join("?" for _ in members)
    return list(
        conn.execute(
            f"""
            SELECT m.from_id, m.to_id, m.merge_id, m.reason, m.created_at,
                   m.merged_by, u.email AS merged_by_email,
                   e.name AS from_name, e.type AS from_type
            FROM entity_mappings m
            LEFT JOIN users u ON u.id = m.merged_by
            LEFT JOIN entities e ON e.id = m.from_id
            WHERE m.active = 1 AND m.from_id IN ({marks})
            ORDER BY m.created_at DESC, m.from_id
            LIMIT ?
            """,  # noqa: S608 - placeholders only, one per closure member
            (*members, limit),
        ).fetchall()
    )


def record_totals(conn: db.Conn, members: list[str]) -> dict[str, int]:
    """How many merges and surfaces the closure actually has.

    The panel shows a bounded slice of each; these are what let it say so rather
    than present a cut list as the whole one.
    """
    marks = ",".join("?" for _ in members)
    merges = conn.execute(
        f"SELECT COUNT(*) AS n FROM entity_mappings "  # noqa: S608 - placeholders only
        f"WHERE active = 1 AND from_id IN ({marks})",
        tuple(members),
    ).fetchone()
    aliases = conn.execute(
        f"SELECT COUNT(*) AS n FROM aliases WHERE entity_id IN ({marks})",  # noqa: S608
        tuple(members),
    ).fetchone()
    return {"merges": int(merges["n"]), "aliases": int(aliases["n"])}
