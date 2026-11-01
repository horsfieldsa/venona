"""Every statement that analyze runs.

Serves `pipeline/analyze.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def update_clusters_by_relevance_overall_tier(
    conn: db.Conn, p0, p1, p2, p3, p4, p5, p6, p7, p8, p9
):
    return conn.execute(
        """
        UPDATE clusters
           SET relevance_overall = ?, tier = ?, relevance_note = ?, so_what = ?,
               axes = ?, profile_hooks = ?, key_facts = ?, flags = ?, analyzed_at = ?
         WHERE id = ?
        
        """,
        (p0, p1, p2, p3, p4, p5, p6, p7, p8, p9),
    )


def update_clusters_by_analyzed_at_id(conn: db.Conn, p0, p1):
    return conn.execute("UPDATE clusters SET analyzed_at = ? WHERE id = ?", (p0, p1))


def select_clusters(conn: db.Conn, p0, p1, p2, p3, p4):
    return conn.execute(
        """
        SELECT COUNT(*) AS backlog,
               SUM(CASE WHEN (?::text IS NULL OR c.published_at IS NULL OR c.published_at >= ?)
                        THEN 1 ELSE 0 END) AS in_window,
               SUM(CASE WHEN (?::text IS NULL OR c.published_at IS NULL OR c.published_at >= ?)
                         AND d.text_chars >= ?
                        THEN 1 ELSE 0 END) AS eligible
          FROM clusters c
          JOIN documents d ON d.id = c.canonical_doc_id
         WHERE c.analyzed_at IS NULL AND d.text_chars > 0
        
        """,
        (p0, p1, p2, p3, p4),
    )


def select_alias_conflicts(conn: db.Conn):
    return conn.execute("SELECT COUNT(*) AS n FROM alias_conflicts WHERE resolved = 0")


def select_trunk_counts(conn: db.Conn):
    return conn.execute(
        "SELECT (SELECT COUNT(*) FROM entities WHERE status = 'active') AS active_entities, "
        "(SELECT COUNT(*) FROM ambiguous_surfaces) AS ambiguous_surfaces"
    )


def pending_clusters(
    conn: db.Conn,
    *,
    cluster_id: str | None,
    cutoff: str | None,
    now: str | None,
    limit: int | None,
    min_chars: int,
) -> list[db.Row]:
    """Clusters with a canonical document and no analysis yet, newest first.

    Undated clusters sort as `now` rather than as `'0000'`: the brief treats a
    missing `published_at` as always in-window, so sinking them to the bottom of
    this queue guaranteed the ones the brief most wanted were the ones it never
    got.

    `cluster_id` narrows to one and SUSPENDS both bounds -- an ingest run was
    asked for a single article by name, and refusing it for being old or short
    would make the button silently do nothing.

    `limit is not None`, not truthiness: 0 is a number someone typed (fetch but
    analyze nothing, a legitimate dry run) and `if limit` would turn it into
    analyze everything, which is the most expensive thing this stage can do.
    """
    params: list[object] = []
    sql = """
        SELECT c.id AS cluster_id, d.id AS doc_id, d.title, d.url,
               d.publisher, d.published_at, d.text_nfc
          FROM clusters c
          JOIN documents d ON d.id = c.canonical_doc_id
         WHERE c.analyzed_at IS NULL AND d.text_chars > 0
    """
    if cluster_id is not None:
        sql += " AND c.id = ?"
        params.append(cluster_id)
    elif cutoff is not None:
        sql += " AND (c.published_at IS NULL OR c.published_at >= ?)"
        sql += " AND d.text_chars >= ?"
        params.extend([cutoff, min_chars])
    # `c.id` breaks the tie, and it is what makes the LIMIT below mean
    # something. Clusters published within the same second sort equal, so an
    # analyze run capped at 120 of 900 was picking whichever of the tied rows
    # the heap returned -- a different set on a replay, and this is the stage
    # that spends money per row.
    sql += " ORDER BY COALESCE(c.published_at, ?) DESC, c.id DESC"
    params.append(now or "0000")
    if limit is not None:
        sql += f" LIMIT {max(0, int(limit))}"  # noqa: S608 - int()-coerced
    return list(conn.execute(sql, params).fetchall())
