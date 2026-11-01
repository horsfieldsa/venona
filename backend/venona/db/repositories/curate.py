"""Every statement that curate runs.

Serves `services/curate.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def settle_open_conflict(
    conn: db.Conn, resolution: str, now: str, conflict_id: str, actor: str | None = None
):
    """Record a verdict on ONE conflict, only if it is still open.

    `AND resolved = 0` is what makes `rowcount` mean "there was an open conflict
    to settle" -- the caller raises on zero, so a second click on a row somebody
    else already decided is an error rather than a silent overwrite of their
    verdict and their name.

    `actor` is a `users.id` or NULL. NULL means the CLI or the pipeline decided,
    which is honest; the column was added by 0002 and nothing wrote it until the
    routers started passing a principal.
    """
    return conn.execute(
        "UPDATE alias_conflicts SET resolved = 1, resolution = ?, resolved_at = ?, "
        "resolved_by = ? WHERE id = ? AND resolved = 0",
        (resolution, now, actor, conflict_id),
    )


def update_entities_by_status_last_seen(conn: db.Conn, p0, p1, p2):
    return conn.execute("UPDATE entities SET status = ?, last_seen = ? WHERE id = ?", (p0, p1, p2))


def settle_conflict(
    conn: db.Conn,
    resolved: int,
    resolution: str,
    now: str,
    conflict_id: str,
    actor: str | None = None,
):
    """Record a verdict on a conflict a MERGE answered, open or not.

    No `resolved = 0` guard, unlike `settle_open_conflict`: a merge settles every
    conflict filed about the pair in either direction, and re-stamping one that was
    already closed is the correct outcome rather than a collision.
    """
    return conn.execute(
        "UPDATE alias_conflicts SET resolved = ?, resolution = ?, resolved_at = ?, "
        "resolved_by = ? WHERE id = ?",
        (resolved, resolution, now, actor, conflict_id),
    )


def update_alias_conflicts_by_resolution_resolved_at_2(conn: db.Conn, p0, p1, p2):
    return conn.execute(
        "UPDATE alias_conflicts SET resolved = 1, resolution = ?, resolved_at = ? WHERE id = ?",
        (p0, p1, p2),
    )


def update_entity_mappings_by_from_id_merge_id(conn: db.Conn, p0, p1):
    return conn.execute(
        "UPDATE entity_mappings SET active = 0  WHERE from_id = ? AND merge_id = ? AND active = 1",
        (p0, p1),
    )


def update_entities_by_last_seen_id(conn: db.Conn, p0, p1, p2):
    return conn.execute(
        "UPDATE entities SET status = 'provisional', last_seen = ? WHERE id = ? AND status = ?",
        (p0, p1, p2),
    )


def update_alias_conflicts_by_resolution_existing_entity_id(conn: db.Conn, p0, p1, p2):
    return conn.execute(
        """
            UPDATE alias_conflicts SET resolved = 0, resolution = NULL, resolved_at = NULL
             WHERE resolution = ? AND (existing_entity_id = ? OR proposed_entity_id = ?)
            
        """,
        (p0, p1, p2),
    )


def select_entities(conn: db.Conn, p0, p1):
    return conn.execute("SELECT id, name, status FROM entities WHERE id IN (?, ?)", (p0, p1))


def select_alias_conflicts(conn: db.Conn):
    return conn.execute(
        "SELECT id, existing_entity_id, proposed_entity_id FROM alias_conflicts WHERE resolved = 0"
    )


def select_alias_conflicts_2(conn: db.Conn):
    return conn.execute(
        "SELECT id, existing_entity_id, proposed_entity_id FROM alias_conflicts WHERE resolved = 0"
    )


def entity_names(conn: db.Conn, ids: list[str]) -> list[db.Row]:
    marks = ",".join("?" for _ in ids)
    return list(
        conn.execute(
            f"SELECT id, name FROM entities WHERE id IN ({marks})",  # noqa: S608 - marks only
            tuple(ids),
        ).fetchall()
    )


def dormant_among(conn: db.Conn, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    marks = ",".join("?" for _ in ids)
    return {
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM entities WHERE status = 'dormant' AND id IN ({marks})",  # noqa: S608 - marks only
            tuple(ids),
        ).fetchall()
    }


def select_conflicts(
    conn: db.Conn,
    *,
    kind: str | None,
    include_resolved: bool,
    limit: int | None = None,
    offset: int = 0,
) -> list[db.Row]:
    """The curation queue, filed order.

    Rows come back with their stored ids -- the caller resolves them through
    `entity_mappings` on read, which is what lets an unmerge restore the original
    pair for free.
    """
    # The actor is JOINED to an email here rather than resolved per row by the
    # router. `_row_to_entry`'s sibling in `glossary.py` learned this the expensive
    # way: one `documents` SELECT per entry was 201 queries to render a 200-row
    # list, and a `users` lookup per conflict would be the same shape.
    #
    # LEFT JOIN, because `resolved_by` is NULL for every pipeline and CLI verdict
    # and for everything decided before migration 0002 -- an INNER join would make
    # those rows vanish from the queue entirely.
    sql = "SELECT c.*, u.email AS resolved_by_email FROM alias_conflicts c "
    sql += "LEFT JOIN users u ON u.id = c.resolved_by"
    where, params = [], []
    if not include_resolved:
        where.append("c.resolved = 0")
        # A DORMANT SIDE IS NOT A QUESTION. The drain (ADR 0054) means
        # uncorroborated and silent for a whole window, and the projection
        # already leaves such an entity out -- so asking a curator whether it is
        # the same as something else is asking about a thing that is not in the
        # graph. Hidden rather than resolved, because nothing has been decided:
        # a revival puts the row straight back in the queue.
        where.append(
            "NOT EXISTS (SELECT 1 FROM entities d"
            " WHERE d.id IN (c.existing_entity_id, c.proposed_entity_id)"
            " AND d.status = 'dormant')"
        )
    if kind:
        where.append("c.kind = ?")
        params.append(kind)
    if where:
        sql += " WHERE " + " AND ".join(where)
    # `c.id` breaks `created_at` ties (a run files many rows in one second), so
    # a bounded read is deterministic about which rows the window holds.
    sql += " ORDER BY c.kind, c.created_at, c.id"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    return list(conn.execute(sql, tuple(params)).fetchall())


def select_merges(
    conn: db.Conn, *, active_only: bool, limit: int | None = None, offset: int = 0
) -> list[db.Row]:
    """Merges newest first, with both names joined in — windowed when asked.

    The joins are LEFT: a merged-away entity keeps its row, but nothing in the
    schema promises the target still exists, and a merge with a missing name is
    still a merge that has to be listable and reversible.

    `merge_id` breaks `created_at` ties (uuid7, so it sorts by creation): a
    curation session records several merges in one second, and a window over an
    ambiguous order would disagree with itself about page two.
    """
    sql = (
        "SELECT m.from_id, m.to_id, m.merge_id, m.active, m.reason, m.created_at, "
        "       f.name AS from_name, t.name AS to_name, u.email AS merged_by_email "
        "  FROM entity_mappings m "
        "  LEFT JOIN entities f ON f.id = m.from_id "
        "  LEFT JOIN entities t ON t.id = m.to_id "
        "  LEFT JOIN users u ON u.id = m.merged_by"
    )
    params: list[object] = []
    if active_only:
        sql += " WHERE m.active = 1"
    sql += " ORDER BY m.created_at DESC, m.merge_id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    return list(conn.execute(sql, tuple(params)).fetchall())


def count_merges(conn: db.Conn, *, active_only: bool) -> int:
    sql = "SELECT COUNT(*) AS n FROM entity_mappings m"
    if active_only:
        sql += " WHERE m.active = 1"
    row = conn.execute(sql).fetchone()
    return int(row["n"]) if row else 0


#: The advisory-lock namespace merges serialise in. Its own, for the reason
#: `users.AUTHORITY_NAMESPACE` gives: a single-argument constant shares a key
#: space with every `hashtext` value, so it could collide with an unrelated
#: lock and make two writers wait on each other for no reason.
MERGE_NAMESPACE = 0x56454E4D  # "VENM"
MERGE_KEY = 1


def lock_merges(conn: db.Conn) -> None:
    """Serialise every merge on the instance, cluster-wide, for this transaction.

    ONE KEY FOR ALL MERGES rather than one per pair, because the invariants are
    about the mapping GRAPH and not about a row. Two merges of different pairs
    break each other:

      * A -> B and B -> A both read a mapping table in which the other has not
        committed, both pass the cycle guard, and both commit. `resolve_mappings`
        then walks a loop, which its own cycle guard breaks arbitrarily -- so
        which entity survives depends on iteration order.
      * A -> B and A -> C both pass, and one `from_id` has two active mappings.
        Unmerge cannot say which to undo.

    A per-pair lock catches the first and misses the second, since the two
    merges touch different pairs. Merges are rare, human-approved actions, so
    serialising all of them costs nothing worth measuring.

    `pg_advisory_xact_lock`: released at COMMIT, so it cannot be leaked by a
    caller that raises -- and every precondition must be re-read INSIDE it, or
    the lock is guarding a decision that was already made.
    """
    conn.execute("SELECT pg_advisory_xact_lock(?, ?)", (MERGE_NAMESPACE, MERGE_KEY))


def select_conflict_sides(conn: db.Conn, conflict_id: str) -> db.Row | None:
    """One conflict with both sides' name, type and status."""
    return conn.execute(
        "SELECT c.id, c.kind, c.resolved, c.existing_entity_id, c.proposed_entity_id, "
        "e.name AS a_name, e.type AS a_type, p.name AS b_name, p.type AS b_type "
        "FROM alias_conflicts c "
        "JOIN entities e ON e.id = c.existing_entity_id "
        "JOIN entities p ON p.id = c.proposed_entity_id "
        "WHERE c.id = ?",
        (conflict_id,),
    ).fetchone()


def select_entity(conn: db.Conn, entity_id: str) -> db.Row | None:
    return conn.execute(
        "SELECT id, name, type, status FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()


def insert_retyped_entity(conn: db.Conn, row: dict) -> None:
    """The entity a type correction lands on, when neither side already is it."""
    db.insert(conn, "entities", row)
