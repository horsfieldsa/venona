"""Every statement that touches `acronym_definitions`, and the rows it joins.

Serves `api/routers/glossary.py`, which held 28 -- the most of any module
in the app. The router keeps the decisions (what a verdict means, which readings
are live, when a surface stops being ambiguous); this holds only the reads and
writes those decisions are made of.

`ENTRY_SELECT` lives here rather than in the router because five endpoints select
the same shape and `_row_to_entry` depends on the join being present. A projection
that four callers share is a fact about the table, not about any one route.
"""

from __future__ import annotations

from typing import Any

from venona.db import core as db

#: The glossary row, its grounding article, and who ruled on it. LEFT JOIN on
#: both: an undefined entry has no article and must still be listed -- an inner
#: join there would make the backlog invisible, which is the half of the glossary
#: worth working through -- and `decided_by` is NULL for every row nobody has
#: ruled on and for every verdict the CLI or the pipeline reached.
#:
#: The address rather than the id, for the reason `curation.py`'s CONFLICT_SELECT
#: gives: `user:0198…` means nothing on a card, and the screen is the only place
#: this column was ever going to be read.
ENTRY_SELECT = """
    SELECT g.*, d.title AS doc_title, d.url AS doc_url, d.publisher AS doc_publisher,
           u.email AS decided_by_email
      FROM acronym_definitions g
      LEFT JOIN documents d ON d.id = g.grounding_article_id
      LEFT JOIN users u ON u.id = g.decided_by
"""

#: Ambiguous first, then active, then undefined. Sorting by `status` as text put
#: `active` above `ambiguous` and buried the 25 entries worth reviewing under 92
#: undefined ones. `sighting_count` is mutable, so a row can migrate between
#: pages while someone scrolls; the client de-duplicates by id for exactly that.
_LIST_ORDER = (
    " ORDER BY CASE g.status WHEN 'ambiguous' THEN 0 WHEN 'active' THEN 1 "
    " WHEN 'undefined' THEN 2 ELSE 3 END, g.sighting_count DESC, g.surface "
    " LIMIT ? OFFSET ?"
)


def _listing_where(status: list[str] | None, q: str | None) -> tuple[str, list[Any]]:
    """The filter `listing` and `listing_total` must agree on — a `total` from a
    different WHERE than its page would over- or under-promise more rows."""
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        placeholders = ", ".join("?" for _ in status)
        clauses.append(f"g.status IN ({placeholders})")
        params.extend(status)
    if q:
        clauses.append("(g.surface ILIKE ? OR g.expansion ILIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def listing(
    conn: db.Conn,
    *,
    status: list[str] | None,
    q: str | None,
    limit: int,
    offset: int,
) -> list[db.Row]:
    where, params = _listing_where(status, q)
    sql = ENTRY_SELECT + where + _LIST_ORDER
    return list(conn.execute(sql, (*params, limit, offset)).fetchall())


def listing_total(conn: db.Conn, *, status: list[str] | None, q: str | None) -> int:
    where, params = _listing_where(status, q)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM acronym_definitions g" + where, tuple(params)
    ).fetchone()
    return int(row["n"]) if row else 0


def status_counts(conn: db.Conn) -> dict[str, int]:
    return {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM acronym_definitions GROUP BY status"
        )
    }


def by_surface(conn: db.Conn, normalized: str, *, limit: int) -> list[db.Row]:
    """A surface's readings, strongest first and bounded.

    A surface carries a handful of readings, but nothing stops a run adding
    more, and this is on the hover card's path. `g.id` breaks the tie so the
    bound always cuts the same rows.
    """
    return list(
        conn.execute(
            ENTRY_SELECT + " WHERE g.normalized = ? ORDER BY g.confidence DESC, g.id LIMIT ?",
            (normalized, limit),
        ).fetchall()
    )


def live_reading_count(conn: db.Conn, normalized: str) -> int:
    """How many readings of this surface are still standing, whole-set.

    Counted here rather than over the returned rows, because the rows are a
    bounded slice and "is this ambiguous" is a question about all of them. The
    `rejected` exclusion is the load-bearing half: counting every row with an
    expansion included the rejected ones, so a surface a curator had just
    settled reported itself ambiguous for ever. Rejection is what makes a
    verdict durable; it must not also make the verdict invisible.
    """
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM acronym_definitions "
            "WHERE normalized = ? AND expansion IS NOT NULL AND status != 'rejected'",
            (normalized,),
        ).fetchone()["n"]
    )


def by_surface_for_verdict(conn: db.Conn, normalized: str) -> list[db.Row]:
    """Ordered by status, for the response after a verdict has been recorded."""
    return list(
        conn.execute(
            ENTRY_SELECT + " WHERE g.normalized = ? ORDER BY g.status, g.confidence DESC",
            (normalized,),
        ).fetchall()
    )


def by_id(conn: db.Conn, entry_id: str) -> db.Row | None:
    return conn.execute(ENTRY_SELECT + " WHERE g.id = ?", (entry_id,)).fetchone()


def readings(conn: db.Conn, normalized: str) -> list[db.Row]:
    """Every expansion recorded for a surface, strongest first.

    THROUGH `ENTRY_SELECT`, like every other read that feeds `_row_to_entry`.
    This was a bare `SELECT *`, which is invisible until a joined column is
    added: `_row_to_entry` reads absent columns through `_col`, so the four
    verdict responses built from this function served `decided_by_email: null`
    for a verdict that had just been signed -- and `response_model` filters on
    the way out, so nothing anywhere raised.
    """
    return list(
        conn.execute(
            ENTRY_SELECT + " WHERE g.normalized = ? AND g.expansion IS NOT NULL "
            "ORDER BY g.confidence DESC, g.expansion",
            (normalized,),
        ).fetchall()
    )


def expansions(conn: db.Conn, normalized: str) -> list[db.Row]:
    return list(
        conn.execute(
            "SELECT id, expansion FROM acronym_definitions "
            "WHERE normalized = ? AND expansion IS NOT NULL",
            (normalized,),
        ).fetchall()
    )


def expansion_texts(conn: db.Conn, normalized: str) -> list[db.Row]:
    return list(
        conn.execute(
            "SELECT expansion FROM acronym_definitions "
            "WHERE normalized = ? AND expansion IS NOT NULL",
            (normalized,),
        ).fetchall()
    )


def sighting_counts(conn: db.Conn, normalized: str) -> db.Row | None:
    return conn.execute(
        "SELECT sighting_count, article_count FROM acronym_definitions WHERE normalized = ?",
        (normalized,),
    ).fetchone()


def web_grounded_pairs(conn: db.Conn) -> list[db.Row]:
    """Live web-grounded readings, for a corpus-wide corroboration sweep.

    `status IN ('active','ambiguous')` and not `rejected`: a reading a curator has
    already thrown out must not be re-corroborated back into confidence.
    """
    return list(
        conn.execute(
            "SELECT surface, expansion FROM acronym_definitions "
            " WHERE expansion IS NOT NULL AND method = 'web_grounded' "
            "   AND status IN ('active','ambiguous')"
        ).fetchall()
    )


def document_texts(conn: db.Conn) -> list[db.Row]:
    """Every document body, for a corpus-wide corroboration sweep."""
    return list(
        conn.execute("SELECT id, text_nfc FROM documents WHERE text_nfc IS NOT NULL").fetchall()
    )


def lookup_run_costs(conn: db.Conn, *, limit: int, harness: str) -> list[db.Row]:
    """Recent lookup runs that actually spent, for the price estimate on the card.

    SCOPED TO ONE SERVICE, because the figure is quoted to a person about to
    press a control that can spend for hours. Two services do not cost the
    same, and one of them may be metered while the other is not -- so an
    unscoped average would quote the price of work nobody is about to do. A
    switch would make it wrong in whichever direction the two happen to differ.

    ROWS FROM BEFORE THE COLUMN EXISTED COUNT, but only for `claude_code`.
    `runs.harness` is nullable and never backfilled, so on every instance that
    exists today the whole history is NULL -- and excluding it would drop the
    quote in front of a control that can spend for hours from measured back to
    a constant, on precisely the instances with the most evidence. What makes
    including them honest is that the migration's own note is right: every run
    written before it was Claude Code, so the NULL is not an unknown, it is an
    unwritten known. For any other service a NULL row really is somebody
    else's price and stays out.
    """
    where = "(harness = ? OR harness IS NULL)" if harness == "claude_code" else "harness = ?"
    return list(
        conn.execute(
            "SELECT cost_usd, started_at, finished_at FROM runs "
            f"WHERE kind = 'lookup' AND cost_usd > 0 AND {where} "  # noqa: S608
            "ORDER BY started_at DESC LIMIT ?",
            (harness, limit),
        ).fetchall()
    )


# ── the entity side of an attach ─────────────────────────────────────


def entity(conn: db.Conn, entity_id: str) -> db.Row | None:
    return conn.execute(
        "SELECT id, name, type, status FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()


def alias_owner(conn: db.Conn, *, normalized: str, entity_id: str) -> db.Row | None:
    """Whether some OTHER entity already claims this surface."""
    return conn.execute(
        "SELECT a.entity_id, e.name FROM aliases a JOIN entities e ON e.id = a.entity_id "
        "WHERE a.normalized = ? AND a.entity_id <> ? LIMIT 1",
        (normalized, entity_id),
    ).fetchone()


def activate_entity(conn: db.Conn, entity_id: str) -> None:
    conn.execute("UPDATE entities SET status = 'active' WHERE id = ?", (entity_id,))


def _rowcount(cursor) -> int:
    """`rowcount` is -1 when the driver does not know, which is never useful to a
    caller counting what it changed."""
    return max(cursor.rowcount or 0, 0)


# ── verdicts ─────────────────────────────────────────────────────────


# ACTOR IS A PLAIN `str | None` ON EVERY WRITE BELOW, never a Request. `api/` is
# the only layer that may reach for identity (`authz.actor_id`); everything under
# it takes the id it was handed, which is what lets `venona glossary` and the
# pipeline call the same functions with None. NULL means the CLI decided, or the
# pipeline did, or the verdict predates anyone being asked to sign -- and
# migration 0002 refuses to backfill for the same reason: putting a name on a
# verdict nobody signed would be inventing provenance.


def set_status(
    conn: db.Conn, *, entry_id: str, status: str, decided_at: str | None, actor: str | None = None
) -> None:
    conn.execute(
        "UPDATE acronym_definitions SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
        (status, decided_at, actor, entry_id),
    )


def reopen(conn: db.Conn, *, entry_id: str, status: str) -> None:
    """`decided_at = NULL` is the point: reopening must erase the human stamp, or
    the next pipeline pass still defers to a verdict nobody stands behind.

    `decided_by` goes with it, and takes NO actor. Reopening is the one verdict
    whose product is the ABSENCE of one -- recording who un-decided it would
    leave a row that reads as decided by them, which is the opposite of what
    they did.
    """
    conn.execute(
        "UPDATE acronym_definitions SET status = ?, decided_at = NULL, decided_by = NULL "
        "WHERE id = ?",
        (status, entry_id),
    )


def reject_surface(
    conn: db.Conn, *, normalized: str, decided_at: str, actor: str | None = None
) -> int:
    """Returns how many readings were retired -- the router reports it."""
    return _rowcount(
        conn.execute(
            "UPDATE acronym_definitions SET status = 'rejected', decided_at = ?, decided_by = ? "
            "WHERE normalized = ?",
            (decided_at, actor, normalized),
        )
    )


def reject_surface_to_entity(
    conn: db.Conn, *, normalized: str, entity_id: str, decided_at: str, actor: str | None = None
) -> int:
    """Retire every reading of a surface and point it at the entity that owns it.

    `entity_id` set is what drops the row out of `build_index`'s glossary query
    (which requires `entity_id IS NULL`), so the surface cannot match twice.

    Returns how many readings were retired.
    """
    return _rowcount(
        conn.execute(
            "UPDATE acronym_definitions SET status = 'rejected', decided_at = ?, "
            "       decided_by = ?, entity_id = ? "
            "WHERE normalized = ?",
            (decided_at, actor, entity_id, normalized),
        )
    )


def reject_entry(
    conn: db.Conn, *, entry_id: str, decided_at: str, actor: str | None = None
) -> None:
    conn.execute(
        "UPDATE acronym_definitions SET status = 'rejected', decided_at = ?, decided_by = ? "
        "WHERE id = ?",
        (decided_at, actor, entry_id),
    )


def mark_ambiguous(
    conn: db.Conn, *, entry_id: str, decided_at: str, actor: str | None = None
) -> None:
    conn.execute(
        "UPDATE acronym_definitions SET status = 'ambiguous', decided_at = ?, decided_by = ? "
        "WHERE id = ?",
        (decided_at, actor, entry_id),
    )


def fold_together(
    conn: db.Conn,
    *,
    entry_id: str,
    decided_at: str,
    sources: str,
    corroboration: int,
    confidence: float,
    actor: str | None = None,
) -> None:
    """Two spellings of one meaning become one active reading.

    `confidence` is recomputed by the caller from the merged source count and
    written HERE rather than left alone: folding two readings together changes how
    well corroborated the survivor is, and a confidence that still describes one
    of them is the wrong number on the card.
    """
    conn.execute(
        "UPDATE acronym_definitions SET status = 'active', decided_at = ?, decided_by = ?, "
        "       corroborating_sources = ?, corroboration_count = ?, confidence = ? "
        " WHERE id = ?",
        (decided_at, actor, sources, corroboration, confidence, entry_id),
    )


def patch(conn: db.Conn, *, entry_id: str, updates: dict[str, Any]) -> None:
    """A PATCH of whichever fields the caller named.

    The column list is built from `updates` KEYS, which are the pydantic model's
    field names and never anything a caller typed -- `api/routers/glossary.py`
    assembles them from a validated payload. That is the one thing worth checking
    if this ever moves: an interpolated identifier is only safe while its source
    is a closed set.
    """
    if not updates:
        return
    sets = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE acronym_definitions SET {sets} WHERE id = ?",  # noqa: S608 - see docstring
        (*updates.values(), entry_id),
    )


def clear_ambiguous_surface(conn: db.Conn, normalized: str) -> None:
    conn.execute("DELETE FROM ambiguous_surfaces WHERE normalized = ?", (normalized,))


def upsert_manual_alias(
    conn: db.Conn,
    *,
    entity_id: str,
    surface: str,
    normalized: str,
    strictness: int,
    is_acronym: bool,
    now: str,
) -> None:
    """A surface a curator attached to an entity by hand.

    The id is `entity|surface` rather than a uuid7: this is keyed on
    (entity_id, surface) and re-attaching the same pair must land on the same
    row, not mint a second id the conflict clause cannot see.
    """
    db.upsert(
        conn,
        "aliases",
        {
            "id": f"{entity_id}|{surface}",
            "entity_id": entity_id,
            "surface": surface,
            "normalized": normalized,
            "strictness": strictness,
            "is_acronym": int(is_acronym),
            "added_by": "manual",
            "created_at": now,
        },
        key=["entity_id", "surface"],
    )


def mark_surface_ambiguous(conn: db.Conn, *, normalized: str, reason: str, now: str) -> None:
    """Record that a surface has more than one accepted reading.

    A surface listed here is never annotated under any single reading -- the
    index enters it as a placeholder the annotator resolves from surrounding
    text, or drops.
    """
    db.upsert(
        conn,
        "ambiguous_surfaces",
        {"normalized": normalized, "reason": reason, "created_at": now},
        key=["normalized"],
    )
