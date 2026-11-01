"""Every statement that touches `config_events`.

Append-only, and there is no update. The whole value of the table is that a row
means "this content existed under this name at this time"; a row that can be
edited afterwards means nothing at all -- the same reason `assertions` is
append-only (contract C1).
"""

from __future__ import annotations

from venona.db import core as db


def record_write(
    conn: db.Conn,
    *,
    event_id: str,
    key: str,
    actor_id: str | None,
    version: str,
    prev: str | None,
    size_bytes: int,
    now: str,
) -> None:
    """Append one row. `actor_id=None` is honest, not missing -- the CLI, the
    setup wizard and the hub's provisioning call all write config with no person
    behind them, and inventing one would be worse than recording none."""
    conn.execute(
        "INSERT INTO config_events (id, key, actor_id, version, prev, bytes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, key, actor_id, version, prev, size_bytes, now),
    )


def recent(conn: db.Conn, *, limit: int = 50, key: str | None = None) -> list[db.Row]:
    """The last N, newest first, with the actor's address resolved.

    LEFT JOIN on the actor for `users.recent_events`' reason: `actor_id` is NULL
    for every change a process made, and an inner join would hide exactly the
    rows whose provenance is most worth reading. Joined here rather than looked
    up per row so a fifty-row panel is one query.

    `id` is the tiebreak, not decoration: two documents saved by one action share
    a timestamp, and ordering by time alone leaves them in whatever order the
    planner returns -- so "what did I change last" answers differently on
    different reads. The ids are uuid7, so they sort by creation.
    """
    where = "WHERE e.key = ?" if key is not None else ""
    params: tuple = (key, limit) if key is not None else (limit,)
    return list(
        conn.execute(
            "SELECT e.id, e.key, e.version, e.prev, e.bytes, e.created_at, "
            "       u.email AS actor_email "
            "FROM config_events e "
            "LEFT JOIN users u ON u.id = e.actor_id "
            f"{where} "
            "ORDER BY e.created_at DESC, e.id DESC "
            "LIMIT ?",
            params,
        ).fetchall()
    )
