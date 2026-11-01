"""Every statement that brief runs.

Serves `pipeline/brief.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def delete_brief_blocks_by_brief_id(conn: db.Conn, p0):
    return conn.execute("DELETE FROM brief_blocks WHERE brief_id = ?", (p0,))


def delete_brief_items_by_brief_id(conn: db.Conn, p0):
    return conn.execute("DELETE FROM brief_items WHERE brief_id = ?", (p0,))


def delete_brief_clusters_by_brief_id(conn: db.Conn, p0):
    return conn.execute("DELETE FROM brief_clusters WHERE brief_id = ?", (p0,))


def select_entities(conn: db.Conn, p0):
    return conn.execute(
        "SELECT id FROM entities WHERE status='active' "
        "ORDER BY relevance DESC, mention_count DESC LIMIT ?",
        (p0,),
    )


def update_brief_blocks_by_spans_id(conn: db.Conn, p0, p1):
    return conn.execute("UPDATE brief_blocks SET spans = ? WHERE id = ?", (p0, p1))


def select_clusters(conn: db.Conn, p0, p1, p2, p3):
    return conn.execute(
        """
        SELECT c.*, d.title AS doc_title, d.url, d.publisher, d.published_at AS doc_published
          FROM clusters c
          JOIN documents d ON d.id = c.canonical_doc_id
         WHERE c.analyzed_at IS NOT NULL
           AND c.relevance_overall IS NOT NULL
           AND d.text_chars >= ?
           AND (c.published_at IS NULL OR c.published_at >= ?)
           AND NOT EXISTS (
                 SELECT 1 FROM brief_clusters bc
                  WHERE bc.cluster_id = c.id
                    AND (?::text IS NULL OR bc.brief_id <> ?)
               )
      -- An unscored cluster (`relevance_overall` IS NULL) must never lead the
      -- brief. NULLS LAST is explicit: SQLite sorts NULLs first on ASC and so
      -- last on DESC, while Postgres does the opposite -- so without it an
      -- unscored cluster leads every brief, silently.
      ORDER BY c.relevance_overall DESC NULLS LAST, c.id
        
        """,
        (p0, p1, p2, p3),
    )


def select_window_shape(conn: db.Conn, p0, p1, p2, p3, p4):
    """The three counts that say WHY a retrieval came back empty.

    Each is scoped to the WINDOW, because the operator's question is about the
    window: a corpus full of analysed material from last month says nothing
    about whether there is work to do today.

    `awaiting_analysis` is the only one that names an action, so it has to mean
    exactly "material analyze could still pay for" -- in window, long enough to
    be briefable, and not yet analysed. `briefable` is the same predicate on the
    other side of that line, and matches `select_clusters` above so the
    wording cannot claim a brief covers something retrieval would not have
    taken. `in_window` is
    the widest: anything with text at all, which is what separates a thin day
    from a silent one.
    """
    return conn.execute(
        """
        SELECT COUNT(*) FILTER (WHERE (c.published_at IS NULL OR c.published_at >= ?)
                                  AND d.text_chars >= ?
                                  AND c.analyzed_at IS NULL) AS awaiting_analysis,
               COUNT(*) FILTER (WHERE (c.published_at IS NULL OR c.published_at >= ?)
                                  AND d.text_chars >= ?
                                  AND c.analyzed_at IS NOT NULL
                                  AND c.relevance_overall IS NOT NULL) AS briefable,
               COUNT(*) FILTER (WHERE (c.published_at IS NULL OR c.published_at >= ?)
                                  AND d.text_chars > 0) AS in_window
          FROM clusters c
          JOIN documents d ON d.id = c.canonical_doc_id
        """,
        (p0, p1, p2, p3, p4),
    )


def select_brief_items(conn: db.Conn, p0, p1):
    return conn.execute(
        """
        SELECT i.headline, i.section, b.date, b.generated_at
          FROM brief_items i JOIN briefs b ON b.id = i.brief_id
         WHERE b.generated_at >= ?
      ORDER BY b.generated_at DESC, i.rank
         LIMIT ?
        
        """,
        (p0, p1),
    )


def select_brief_blocks_by_brief_id(conn: db.Conn, p0):
    return conn.execute(
        "SELECT * FROM brief_blocks WHERE brief_id = ? ORDER BY section, ordinal", (p0,)
    )


def select_assertions_by_cluster_id(conn: db.Conn, p0):
    return conn.execute(
        "SELECT DISTINCT evidence_quote FROM assertions WHERE cluster_id = ? "
        "AND kind = 'relation' LIMIT 6",
        (p0,),
    )


def select_documents_by_id(conn: db.Conn, p0):
    return conn.execute("SELECT text_nfc FROM documents WHERE id = ?", (p0,))


def select_entities_2(conn: db.Conn):
    return conn.execute("SELECT id FROM entities WHERE status='active'")


def select_brief_items_by_brief_id(conn: db.Conn, p0):
    return conn.execute(
        """
            SELECT DISTINCT a.entity_id_raw AS id
              FROM brief_items bi
              JOIN documents d ON d.cluster_id = bi.cluster_id
              JOIN assertions a ON a.article_id = d.id
             WHERE bi.brief_id = ?
               AND a.kind = 'entity'
               AND a.retracted_at IS NULL
               AND a.entity_id_raw IS NOT NULL
            
        """,
        (p0,),
    )


def active_entities_in_clusters(conn: db.Conn, cluster_ids: list[str]) -> list[db.Row]:
    """Entities the chosen clusters assert, `active` only: NAMES THE MODEL MAY USE.

    This is a prompt list, capped at `brief_max_entities` and topped up with the
    highest-relevance active entities so the brief can refer to a well-known
    programme today's articles did not name. Provisional and dormant entities
    stay out: 1167 of 1203 provisionals rest on a single article, and filling a
    120-name cap with those would spend it on names instead of on the entities a
    reader recognises.

    **This is not the whole annotation tier.** See
    `provisional_entities_in_clusters` for the half a reader can hover but the
    model is not offered.
    """
    marks = ",".join("?" for _ in cluster_ids)
    return list(
        conn.execute(
            f"""
            SELECT DISTINCT a.entity_id_raw AS id
              FROM assertions a
              JOIN entities e ON e.id = a.entity_id_raw
             WHERE a.cluster_id IN ({marks})
               AND a.entity_id_raw IS NOT NULL
               AND e.status = 'active'
            """,  # noqa: S608 - placeholders only
            tuple(cluster_ids),
        ).fetchall()
    )


def provisional_entities_in_clusters(conn: db.Conn, cluster_ids: list[str]) -> list[db.Row]:
    """The SINGLE-SOURCE entities these clusters assert. The annotation tier's other half.

    "What may the model write about" and "what may a reader hover" are different
    questions, and for a long time both were answered by the active-only list
    above. That answered the second one wrongly. An entity minted from one
    article carries a strictness-1 alias, which matches only inside the
    annotation tier -- so an active-only tier left every provisional entity
    structurally unmatchable, however plainly the brief named it. Measured 2026-08-29 on
    the newest brief: 19 active entities against 19 provisional ones a reader
    could not hover, `DoDNet` among them.

    Admitted and MARKED, never promoted. `single_source` rides through
    `alias_index` onto the span and `TextRuns.vue` draws it as
    `mention--uncorroborated`: "Named by one source; not yet corroborated",
    which is exactly what it is. Promotion still needs two publishers, so the
    graph keeps its ability to say which facts stand on one.

    `dormant` stays out. Thirty days with no new evidence is the corpus saying
    the surface stopped mattering, and re-underlining it says the opposite.
    """
    marks = ",".join("?" for _ in cluster_ids)
    return list(
        conn.execute(
            f"""
            SELECT DISTINCT a.entity_id_raw AS id
              FROM assertions a
              JOIN entities e ON e.id = a.entity_id_raw
             WHERE a.cluster_id IN ({marks})
               AND a.entity_id_raw IS NOT NULL
               AND e.status = 'provisional'
            """,  # noqa: S608 - placeholders only
            tuple(cluster_ids),
        ).fetchall()
    )


def entities_for_prompt(conn: db.Conn, entity_ids: list[str]) -> list[db.Row]:
    """Name and type for a fixed id list, ordered by name.

    `ORDER BY name` is not cosmetic -- this renders into a model prompt, and
    `lint-determinism` exists because an unordered list makes the same corpus
    produce a different prompt on every run.
    """
    marks = ",".join("?" for _ in entity_ids)
    return list(
        conn.execute(
            f"SELECT id, name, type FROM entities WHERE id IN ({marks}) ORDER BY name",  # noqa: S608
            tuple(entity_ids),
        ).fetchall()
    )


#: The population every funnel stage narrows: clusters with analysis, joined to
#: their canonical document so a stage may filter on document text length.
BRIEFABLE_BASE = (
    "SELECT COUNT(*) AS n FROM clusters c JOIN documents d ON d.id = c.canonical_doc_id "
    "WHERE c.analyzed_at IS NOT NULL"
)


def count_briefable(conn: db.Conn, clause: str = "", params: tuple = ()) -> int:
    """Count the briefable population, optionally narrowed by `clause`.

    `clause` is composed by `_emit_metrics` from its own `stages` table -- a
    literal in that module, never anything a request supplies -- because each
    stage has to be counted against the ones before it, which no fixed query can
    express. The values still bind as parameters.
    """
    sql = f"{BRIEFABLE_BASE}{' AND ' + clause if clause else ''}"  # noqa: S608 - literal clauses
    return conn.execute(sql, params).fetchone()["n"]
