"""Every statement that acronyms runs.

Serves `pipeline/acronyms.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def update_acronym_definitions_by_corroborating_sources_corrobor(conn: db.Conn, p0, p1, p2, p3, p4):
    return conn.execute(
        "UPDATE acronym_definitions    SET corroborating_sources = ?, corroboration_count = ?, "
        "confidence = ?, last_seen = ?  WHERE id = ?",
        (p0, p1, p2, p3, p4),
    )


def update_acronym_definitions_by_normalized(conn: db.Conn, p0):
    return conn.execute(
        "UPDATE acronym_definitions SET status = 'ambiguous' WHERE normalized = ? "
        "AND NOT (decided_at IS NOT NULL AND status = 'rejected')",
        (p0,),
    )


def update_acronym_definitions(conn: db.Conn):
    return conn.execute(
        """
        UPDATE acronym_definitions SET entity_id = (
            SELECT a.entity_id FROM aliases a
            JOIN entities e ON e.id = a.entity_id
            WHERE UPPER(a.surface) = UPPER(acronym_definitions.surface)
              AND e.status = 'active' AND a.strictness > 0
            LIMIT 1
        )
        WHERE entity_id IS NULL AND expansion IS NULL
          AND EXISTS (
            SELECT 1 FROM aliases a
            JOIN entities e ON e.id = a.entity_id
            WHERE UPPER(a.surface) = UPPER(acronym_definitions.surface)
              AND e.status = 'active' AND a.strictness > 0
          )
        
        """
    )


def update_acronym_definitions_by_sighting_count_article_count(conn: db.Conn, p0, p1, p2, p3, p4):
    """Fold the losing rows' evidence onto the surviving one.

    `GREATEST` rather than assignment on the three counts. The caller computed
    them from rows it read, so a grounding that lands between that read and
    this write would be silently reverted -- and evidence, by the rule this
    table is built on, accumulates and does not go backwards.
    """
    return conn.execute(
        "UPDATE acronym_definitions SET "
        "  sighting_count = GREATEST(COALESCE(sighting_count, 0), ?), "
        "  article_count = GREATEST(COALESCE(article_count, 0), ?), "
        "  corroboration_count = GREATEST(COALESCE(corroboration_count, 0), ?), "
        "  corroborating_sources = ? "
        "WHERE id = ?",
        (p0, p1, p2, p3, p4),
    )


def delete_acronym_definitions_by_id(conn: db.Conn, rows):
    return conn.executemany("DELETE FROM acronym_definitions WHERE id = ?", rows)


def delete_ambiguous_surfaces_by_normalized(conn: db.Conn, p0):
    return conn.execute("DELETE FROM ambiguous_surfaces WHERE normalized = ?", (p0,))


def update_acronym_definitions_by_normalized_2(conn: db.Conn, p0):
    return conn.execute(
        "UPDATE acronym_definitions SET status = 'active' WHERE normalized = ? "
        "AND status = 'ambiguous' AND decided_at IS NULL",
        (p0,),
    )


def select_acronym_definitions_by_normalized(conn: db.Conn, p0):
    return conn.execute(
        "SELECT * FROM acronym_definitions WHERE normalized = ? AND status = 'active' "
        "AND expansion IS NOT NULL ORDER BY confidence DESC LIMIT 1",
        (p0,),
    )


def select_acronym_definitions_by_normalized_2(conn: db.Conn, p0):
    return conn.execute(
        "SELECT 1 FROM acronym_definitions WHERE normalized = ? "
        "AND expansion IS NOT NULL AND status IN ('active','ambiguous','rejected') "
        "LIMIT 1",
        (p0,),
    )


def select_acronym_definitions_by_normalized_expansion(conn: db.Conn, p0, p1):
    return conn.execute(
        "SELECT id, method, confidence, corroborating_sources, status, decided_at   FROM "
        "acronym_definitions WHERE normalized = ? AND expansion = ?",
        (p0, p1),
    )


def select_acronym_definitions_by_normalized_3(conn: db.Conn, p0):
    return conn.execute(
        "SELECT id, expansion FROM acronym_definitions WHERE normalized = ? AND expansion IS NOT "
        "NULL",
        (p0,),
    )


def select_acronym_definitions_by_normalized_expansion_2(conn: db.Conn, p0, p1):
    return conn.execute(
        "SELECT id FROM acronym_definitions WHERE normalized = ? AND expansion = ?", (p0, p1)
    )


def select_acronym_definitions_by_normalized_4(conn: db.Conn, p0):
    return conn.execute(
        "SELECT MAX(sighting_count) AS s, MAX(article_count) AS a FROM acronym_definitions WHERE "
        "normalized = ?",
        (p0,),
    )


def select_acronym_definitions_by_id(conn: db.Conn, p0):
    return conn.execute(
        "SELECT status, decided_at, method, confidence, corroboration_count,        "
        "corroborating_sources, grounding_article_id, grounding_url, grounding_quote,        "
        "grounding_span   FROM acronym_definitions WHERE id = ?",
        (p0,),
    )


def select_acronym_definitions(conn: db.Conn):
    return conn.execute(
        "SELECT id, expansion FROM acronym_definitions WHERE expansion IS NOT NULL "
        "AND entity_id IS NULL AND status = 'active'"
    )


def update_acronym_definitions_by_entity_id_id(conn: db.Conn, p0, p1):
    return conn.execute("UPDATE acronym_definitions SET entity_id = ? WHERE id = ?", (p0, p1))


def select_acronym_definitions_2(conn: db.Conn):
    return conn.execute("SELECT * FROM acronym_definitions WHERE expansion IS NOT NULL")


def select_ambiguous_surfaces(conn: db.Conn):
    return conn.execute("SELECT normalized FROM ambiguous_surfaces")


def select_documents(conn: db.Conn):
    """Every analyzed document the stage has not read yet.

    Ordered by id so a scan is byte-identical offline, and scoped by
    `acronyms_scanned_at`, which is what keeps a nightly run off the whole
    corpus. What a document says about a surface is settled once it has been
    read; what moves is the corpus-wide totals, and `surface_sightings` and
    `ordinary_word_df` hold those so they need no rescan to stay right.

    A cluster whose canonical document is replaced comes back into scope:
    `refresh_clusters` clears the marker, because the text this read is no
    longer the text the cluster stands for.
    """
    return conn.execute(
        "SELECT d.id, d.text_nfc, c.id AS cluster_id "
        "  FROM documents d JOIN clusters c ON c.canonical_doc_id = d.id "
        " WHERE c.analyzed_at IS NOT NULL AND c.acronyms_scanned_at IS NULL"
        " ORDER BY d.id"
    )


def mark_clusters_scanned(conn: db.Conn, rows):
    return conn.executemany("UPDATE clusters SET acronyms_scanned_at = ? WHERE id = ?", rows)


def select_surface_sightings(conn: db.Conn):
    """The corpus-wide sighting totals, which the tier gates read.

    Ordered by `normalized` so the candidate sort below has a stable input --
    the tier-3 sort breaks ties by count alone, and a set iterated in hash
    order would spend the per-run cap on a different eight surfaces each night.
    """
    return conn.execute(
        "SELECT normalized, surface, sighting_count, article_count "
        "  FROM surface_sightings ORDER BY normalized"
    )


def bump_surface_sightings(conn: db.Conn, rows):
    """Add this scan's deltas to the running totals.

    `col = col + excluded.col`, not assignment: the totals span every scan
    there has ever been, and this statement only ever knows about one of them.
    """
    return conn.executemany(
        "INSERT INTO surface_sightings "
        "  (normalized, surface, sighting_count, article_count, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(normalized) DO UPDATE SET "
        "  sighting_count = surface_sightings.sighting_count + excluded.sighting_count, "
        "  article_count = surface_sightings.article_count + excluded.article_count, "
        "  last_seen = excluded.last_seen",
        rows,
    )


def delete_surface_sightings(conn: db.Conn, rows):
    return conn.executemany("DELETE FROM surface_sightings WHERE normalized = ?", rows)


def select_ordinary_words(conn: db.Conn, p0):
    """Tokens seen as ordinary words in at least `p0` documents."""
    return conn.execute("SELECT word FROM ordinary_word_df WHERE doc_count >= ?", (p0,))


def bump_ordinary_word_df(conn: db.Conn, rows):
    return conn.executemany(
        "INSERT INTO ordinary_word_df (word, doc_count) VALUES (?, ?) "
        "ON CONFLICT(word) DO UPDATE SET "
        "  doc_count = ordinary_word_df.doc_count + excluded.doc_count",
        rows,
    )


def upsert_undefined_attempt(
    conn: db.Conn,
    *,
    entry_id: str,
    surface: str,
    normalized: str,
    sighting_count: int,
    article_count: int,
    now: str,
    run_id: str,
):
    """Record an undefined surface and count the lookup that failed on it.

    `attempt_count` increments IN THE STATEMENT. Reading it and writing back
    `read + 1` is the lost update `db/core.py::transaction` describes: the
    light grounding lane and the nightly research sweep can both be looking at
    the same surface, and two attempts that both read 3 both write 4. The
    column then under-counts exactly the surfaces being attempted most, which
    are the ones the retry window exists to hold back.

    `first_seen` is absent from the conflict arm on purpose: it is the first
    time this surface was seen, and a column that moves on every write is not
    that. `test_define.py::test_first_seen_survives_a_second_write` pins the
    same rule for the definitions table.
    """
    return conn.execute(
        "INSERT INTO acronym_definitions "
        "  (id, surface, normalized, expansion, method, confidence, status, "
        "   sighting_count, article_count, first_seen, last_seen, "
        "   last_attempt_at, last_attempt_run_id, attempt_count) "
        "VALUES (?, ?, ?, NULL, 'undefined', 0.0, 'undefined', ?, ?, ?, ?, ?, ?, 1) "
        "ON CONFLICT(id) DO UPDATE SET "
        "  surface = excluded.surface, normalized = excluded.normalized, "
        "  expansion = NULL, method = 'undefined', confidence = 0.0, status = 'undefined', "
        "  sighting_count = excluded.sighting_count, article_count = excluded.article_count, "
        "  last_seen = excluded.last_seen, "
        "  last_attempt_at = excluded.last_attempt_at, "
        "  last_attempt_run_id = excluded.last_attempt_run_id, "
        "  attempt_count = COALESCE(acronym_definitions.attempt_count, 0) + 1",
        (entry_id, surface, normalized, sighting_count, article_count, now, now, now, run_id),
    )


def select_acronym_definitions_by_id_2(conn: db.Conn, p0):
    return conn.execute("SELECT attempt_count FROM acronym_definitions WHERE id = ?", (p0,))


def select_acronym_definitions_by_normalized_5(conn: db.Conn, p0):
    return conn.execute(
        "SELECT id, expansion FROM acronym_definitions WHERE normalized = ? AND expansion IS NOT "
        "NULL",
        (p0,),
    )


def select_entities_by_canonical_key(conn: db.Conn, p0):
    return conn.execute(
        "SELECT id FROM entities WHERE canonical_key = ? AND status = 'active' LIMIT 1", (p0,)
    )


def select_acronym_definitions_by_normalized_6(conn: db.Conn, p0):
    return conn.execute(
        "SELECT expansion FROM acronym_definitions WHERE normalized = ? AND expansion IS NOT NULL",
        (p0,),
    )


def select_acronym_definitions_by_normalized_7(conn: db.Conn, p0):
    return conn.execute(
        "SELECT 1 FROM acronym_definitions WHERE normalized = ? "
        "AND decided_at IS NOT NULL AND status = 'ambiguous' LIMIT 1",
        (p0,),
    )


def undefined_backlog(conn: db.Conn, *, retry: bool) -> list[db.Row]:
    """Surfaces with no expansion anywhere, strongest signal first.

    `NOT EXISTS` rather than a status check on the row itself: a surface with one
    undefined reading and one active reading is DEFINED, and looking only at this
    row would queue it forever.

    `retry=False` adds `last_attempt_at IS NULL`, which is what stops the sweep
    paying to re-ask a question that has already been asked and answered with
    nothing.
    """
    sql = """
        SELECT g.surface, g.sighting_count, g.article_count,
               g.attempt_count, g.last_attempt_at
          FROM acronym_definitions g
         WHERE g.status = 'undefined'
           AND g.expansion IS NULL
           AND NOT EXISTS (
                 SELECT 1 FROM acronym_definitions d
                  WHERE d.normalized = g.normalized
                    AND d.expansion IS NOT NULL
                    AND d.status IN ('active', 'ambiguous')
               )
    """
    if not retry:
        sql += " AND g.last_attempt_at IS NULL"
    sql += " ORDER BY g.sighting_count DESC, g.surface ASC"
    return list(conn.execute(sql).fetchall())


def recently_attempted(conn: db.Conn, cutoff: str) -> set[str]:
    """Normalized surfaces a web lookup already failed to ground since `cutoff`.

    `last_attempt_at` is stamped only by a lookup that RAN, so this is the set
    that has been paid for and produced nothing -- not the set that is merely
    still undefined.
    """
    rows = conn.execute(
        """
        SELECT normalized FROM acronym_definitions
         WHERE status = 'undefined'
           AND last_attempt_at IS NOT NULL
           AND last_attempt_at >= ?
        """,
        (cutoff,),
    ).fetchall()
    return {row["normalized"] for row in rows}
