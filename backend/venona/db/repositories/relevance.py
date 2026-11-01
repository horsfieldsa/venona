"""Every statement personal relevance runs (emphasis, follows, personal briefs).

Two properties this module is the enforcement point for:

  * **Every per-user read and write is keyed on `user_id`.** The privacy
    stance -- a reader's emphasis, follows and personal brief are visible to
    that reader alone -- is only as strong as the queries, so no function here
    touches the three per-user tables without a `user_id` parameter.
  * **Follows are matched through `entity_mappings`, one hop.** A followed
    entity that was merged away must keep matching under its survivor, and the
    one-hop approximation is the same deliberate trade `learning.py` makes for
    its degree ranking.

See `db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db

# ── the reader's emphasis ────────────────────────────────────────────────


def settings_row(conn: db.Conn, *, user_id: str) -> db.Row | None:
    return conn.execute("SELECT * FROM relevance_settings WHERE user_id = ?", (user_id,)).fetchone()


def upsert_settings(conn: db.Conn, *, user_id: str, emphasis: str, now: str) -> None:
    conn.execute(
        "INSERT INTO relevance_settings (user_id, emphasis, updated_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET emphasis = EXCLUDED.emphasis, "
        "updated_at = EXCLUDED.updated_at",
        (user_id, emphasis, now),
    )


def delete_settings(conn: db.Conn, *, user_id: str) -> None:
    """Reset is deletion: an absent row IS the instance defaults, so there is
    no second representation of "default" to drift from."""
    conn.execute("DELETE FROM relevance_settings WHERE user_id = ?", (user_id,))


# ── follows ──────────────────────────────────────────────────────────────


def follow_count(conn: db.Conn, *, user_id: str) -> int:
    """How many names this reader follows, whole-set."""
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM entity_follows WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
    )


def follows(conn: db.Conn, *, user_id: str, limit: int | None = None) -> list[db.Row]:
    """The reader's follows with each entity's current name, newest first.

    Resolved through the active merge mapping so a follow placed on an entity
    that was later merged renders (and matches) as its survivor. A follow
    whose entity cannot be resolved returns anyway, name NULL -- dormant, not
    deleted, because the follow is the reader's own record.

    `limit` is optional because two callers want different things: the HTTP read
    shows a screen and bounds itself, while scoring a personal brief needs the
    whole follow set -- a cap there would quietly change which items a reader is
    shown, which is the opposite of a display concern.
    """
    return list(
        conn.execute(
            """
            SELECT f.entity_id,
                   COALESCE(m.to_id, f.entity_id) AS resolved_id,
                   e.name, e.type, e.status,
                   f.created_at
              FROM entity_follows f
              LEFT JOIN entity_mappings m
                     ON m.from_id = f.entity_id AND m.active = 1
              LEFT JOIN entities e ON e.id = COALESCE(m.to_id, f.entity_id)
             WHERE f.user_id = ?
             ORDER BY f.created_at DESC, f.entity_id
            """
            + ("" if limit is None else " LIMIT ?"),
            (user_id,) if limit is None else (user_id, limit),
        ).fetchall()
    )


def add_follow(conn: db.Conn, *, user_id: str, entity_id: str, now: str) -> None:
    conn.execute(
        "INSERT INTO entity_follows (user_id, entity_id, created_at) "
        "VALUES (?, ?, ?) ON CONFLICT (user_id, entity_id) DO NOTHING",
        (user_id, entity_id, now),
    )


def remove_follow(conn: db.Conn, *, user_id: str, entity_id: str) -> None:
    conn.execute(
        "DELETE FROM entity_follows WHERE user_id = ? AND entity_id = ?",
        (user_id, entity_id),
    )


def followed_in_clusters(conn: db.Conn, *, user_id: str, cluster_ids: list[str]) -> list[db.Row]:
    """Which of the reader's follows each cluster mentions.

    Both sides resolve one hop through the mapping before comparing, so a
    follow on either half of a merged pair matches assertions recorded under
    either id. Distinct per (cluster, entity): the chip says "mentions X",
    not how many times.
    """
    if not cluster_ids:
        return []
    marks = ",".join("?" for _ in cluster_ids)
    return list(
        conn.execute(
            f"""
            SELECT DISTINCT a.cluster_id,
                   COALESCE(mf.to_id, f.entity_id) AS entity_id,
                   e.name
              FROM entity_follows f
              LEFT JOIN entity_mappings mf
                     ON mf.from_id = f.entity_id AND mf.active = 1
              JOIN assertions a
                     ON COALESCE(
                          (SELECT ma.to_id FROM entity_mappings ma
                            WHERE ma.from_id = a.entity_id_raw AND ma.active = 1),
                          a.entity_id_raw
                        ) = COALESCE(mf.to_id, f.entity_id)
               AND a.kind = 'entity'
               AND a.retracted_at IS NULL
               AND a.cluster_id IN ({marks})
              LEFT JOIN entities e ON e.id = COALESCE(mf.to_id, f.entity_id)
             WHERE f.user_id = ?
            """,  # noqa: S608 - placeholders only
            (*cluster_ids, user_id),
        ).fetchall()
    )


def entity_name(conn: db.Conn, *, entity_id: str) -> db.Row | None:
    """Existence check for a follow's target -- a typo id would create a
    follow that can never match, silently."""
    return conn.execute("SELECT id, name FROM entities WHERE id = ?", (entity_id,)).fetchone()


def suggestions(conn: db.Conn, *, user_id: str, limit: int) -> list[db.Row]:
    """Active entities the reader does not already follow, most central first
    -- minus the names that appear everywhere.

    Degree over non-retracted assertions, computed here rather than asked of
    Neo4j for the same reason the learning deck does it: the graph is a
    rebuildable projection and the picker must not go dark with it.

    Two exclusions keep the picker honest -- following surfaces every story
    that mentions the name, so offering a name that is everywhere teaches a
    new reader that following does nothing:

      * coverage: an entity asserted in more than a quarter of canonical
        documents (the GREATEST floor keeps a tiny corpus from excluding
        everything);
      * the low-care anchor band: `anchors.yaml` seeds structural scaffolding
        (countries, theater commands) below ~0.45 relevance, deliberately
        under the names the profile actually cares about -- scaffolding is
        graph trunk, not an interest. Organic entities carry 0.0 and pass.

    Either kind is still followable from its own card, where the choice is
    deliberate rather than offered.
    """
    return list(
        conn.execute(
            """
            WITH deg AS (
                SELECT e_id, COUNT(*) AS degree FROM (
                    SELECT COALESCE(ms.to_id, a.subject_id_raw) AS e_id
                      FROM assertions a
                      LEFT JOIN entity_mappings ms
                             ON ms.from_id = a.subject_id_raw AND ms.active = 1
                     WHERE a.kind = 'relation' AND a.retracted_at IS NULL
                    UNION ALL
                    SELECT COALESCE(mo.to_id, a.object_id_raw) AS e_id
                      FROM assertions a
                      LEFT JOIN entity_mappings mo
                             ON mo.from_id = a.object_id_raw AND mo.active = 1
                     WHERE a.kind = 'relation' AND a.retracted_at IS NULL
                ) endpoints GROUP BY e_id
            ),
            coverage AS (
                SELECT a.entity_id_raw AS e_id,
                       COUNT(DISTINCT a.article_id) AS docs
                  FROM assertions a
                 WHERE a.kind = 'entity' AND a.retracted_at IS NULL
                 GROUP BY a.entity_id_raw
            ),
            doc_total AS (
                SELECT COUNT(*) AS n FROM documents WHERE duplicate_of IS NULL
            )
            SELECT e.id, e.name, e.type, e.mention_count,
                   COALESCE(deg.degree, 0) AS degree
              FROM entities e
              LEFT JOIN deg ON deg.e_id = e.id
              LEFT JOIN coverage cov ON cov.e_id = e.id
              LEFT JOIN entity_follows ef
                     ON ef.user_id = ? AND ef.entity_id = e.id
             WHERE e.status = 'active' AND ef.entity_id IS NULL
               AND COALESCE(cov.docs, 0) * 4 <= GREATEST((SELECT n FROM doc_total), 12)
               AND NOT (e.relevance > 0 AND e.relevance < 0.45)
             ORDER BY degree DESC, e.mention_count DESC, e.id
             LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    )


# ── the personal reading window ──────────────────────────────────────────


def window_clusters(conn: db.Conn, *, min_chars: int, cutoff: str) -> list[db.Row]:
    """Every analyzed, scored cluster in the window -- deliberately WITHOUT
    the `brief_clusters` exclusion. That ledger belongs to the shared daily
    brief; the personal list re-ranks the whole window for one reader and
    claims nothing, so it must never starve (or be starved by) the shared
    selection. Ordering is a stable default only: the caller re-ranks by the
    reader's own weights.
    """
    return list(
        conn.execute(
            """
            SELECT c.*, d.title AS doc_title, d.url, d.publisher,
                   d.published_at AS doc_published
              FROM clusters c
              JOIN documents d ON d.id = c.canonical_doc_id
             WHERE c.analyzed_at IS NOT NULL
               AND c.relevance_overall IS NOT NULL
               AND d.text_chars >= ?
               AND (c.published_at IS NULL OR c.published_at >= ?)
             ORDER BY c.relevance_overall DESC NULLS LAST, c.id
            """,
            (min_chars, cutoff),
        ).fetchall()
    )


# ── the reader's own brief ───────────────────────────────────────────────


def latest_personal_brief(conn: db.Conn, *, user_id: str) -> db.Row | None:
    """This reader's newest document, over ALL dates.

    Deliberately not date-scoped. The reader has ONE current brief and the
    question asked of it is always "does this cover the newest reading", which
    a date scope answers wrongly twice over: it loses a document written at
    23:50 the moment the clock passes midnight, and it calls a document
    written at 00:05 from yesterday's reading current for the whole day.
    """
    return conn.execute(
        "SELECT * FROM personal_briefs WHERE user_id = ? ORDER BY generated_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def personal_brief_blocks(conn: db.Conn, *, brief_id: str) -> list[db.Row]:
    return list(
        conn.execute(
            "SELECT * FROM personal_brief_blocks WHERE brief_id = ? ORDER BY section, ordinal",
            (brief_id,),
        ).fetchall()
    )


def personal_brief_items_for(conn: db.Conn, *, brief_id: str) -> list[db.Row]:
    """Items with attribution joined from `documents`, mirroring the shared
    `select_brief_items_by_brief_id`.

    `ORDER BY i.ord` and nothing else. The shared item assembly opens a desk
    the first time an item claims it, so the row order IS the desk order --
    and `ord` was assigned after the desks were sorted into the profile's
    declared order. Ordering by `section` here would re-sort them
    alphabetically and lose that.
    """
    return list(
        conn.execute(
            """
            SELECT i.*, d.publisher, d.url, d.published_at
              FROM personal_brief_items i
              LEFT JOIN clusters c ON c.id = i.cluster_id
              LEFT JOIN documents d ON d.id = c.canonical_doc_id
             WHERE i.brief_id = ?
             ORDER BY i.ord
            """,
            (brief_id,),
        ).fetchall()
    )


def personal_recent_items(
    conn: db.Conn, *, user_id: str, cutoff: str, exclude_date: str, limit: int
) -> list[db.Row]:
    """The reader's own thread history, for the follow-up diff.

    Excludes `exclude_date` (the date being written): the day's document is
    replaced in place, so without the exclusion a same-day regeneration reads
    its own soon-to-be-deleted items as "already briefed" and marks the whole
    document follow_up against a predecessor that ceases to exist. The shared
    brief includes same-day predecessors on purpose -- but its briefs are
    additive and their windows disjoint, and neither premise holds here.

    `cutoff` compares against `generated_at` and must be millisecond-precision
    text (`pipeline.brief._cutoff_ms`), or the boundary second is dropped.
    """
    return list(
        conn.execute(
            """
            SELECT i.headline, b.date, b.generated_at
              FROM personal_brief_items i
              JOIN personal_briefs b ON b.id = i.brief_id
             WHERE b.user_id = ? AND b.generated_at >= ? AND b.date <> ?
             ORDER BY b.generated_at DESC, i.ord
             LIMIT ?
            """,
            (user_id, cutoff, exclude_date, limit),
        ).fetchall()
    )


def delete_personal_brief(conn: db.Conn, *, user_id: str, date: str) -> None:
    """Regeneration replaces the day's document; items and blocks go with it
    via the FK cascades."""
    conn.execute(
        "DELETE FROM personal_briefs WHERE user_id = ? AND date = ?",
        (user_id, date),
    )


def count_personal_brief_runs(conn: db.Conn, *, user_id: str, since: str) -> int:
    """How many `personal_brief` runs this reader has started since `since`.

    Counts runs, not documents: the day's row is replaced in place, so `runs`
    is the only ledger that sees a generation which spent a call and produced
    nothing. Two callers bound it differently and both are right -- the cap
    passes a bare date, which sorts under every stamp inside that day, and the
    auto path passes the newest shared brief's timestamp, because its question
    is "have we already tried for THIS reading".
    """
    # The quoted id alone, no key: `params` is serialized JSON whose spacing
    # is the serializer's business, and a pattern that assumed a space after
    # the colon matched nothing -- the cap read 0 forever. The bare quoted
    # string appears identically under every spelling, and ids cannot
    # contain quotes.
    return conn.execute(
        "SELECT COUNT(*) AS n FROM runs "
        "WHERE kind = 'personal_brief' AND started_at >= ? "
        "AND params LIKE ?",
        (since, f'%"{user_id}"%'),
    ).fetchone()["n"]
