"""Access requests: the silent-dedup insert, and the triage the CLI drives."""

from __future__ import annotations

from typing import Any

from hub.db.core import Conn

#: What a row asked for. `access` is a person at a locked door; `launch` is an
#: address left on a separate front site against the day there is a door. The
#: operator page buckets on this, and the two are graded differently: a launch
#: signup is not waiting on a decision.
KINDS = ("access", "launch")


def submit(
    conn: Conn,
    *,
    request_id: str,
    email: str,
    note: str,
    wanted_instance_id: str | None,
    kind: str = "access",
) -> bool:
    """True when a row was actually written. ON CONFLICT DO NOTHING is the
    non-echoing property's other half: a duplicate changes nothing AND says
    nothing, so the reply can be byte-identical without lying.

    `kind` is part of the conflict target (migration 0004), so the same
    address can sit on the launch list and still raise an access request that
    a person has to answer.

    THE RETURN IS FOR THE OPERATOR'S CHANNEL, not for the caller's reply, which
    stays identical either way. A duplicate submission is somebody pressing the
    button twice; notifying on it would let a stranger ring an operator's phone
    as often as they liked.
    """
    row = conn.execute(
        "INSERT INTO access_requests (id, email, note, wanted_instance_id, kind)"
        " VALUES (%s, %s, %s, %s, %s)"
        " ON CONFLICT (email, wanted_instance_id, kind) DO NOTHING RETURNING id",
        (request_id, email.strip().lower(), note, wanted_instance_id, kind),
    ).fetchone()
    return row is not None


def has_pending(conn: Conn, email: str) -> bool:
    """Existence only, no row: the portal tells a signed-in visitor THAT a
    request for their email is pending, never what it says -- the anonymous
    form takes any email, so the note and wanted instance may be someone
    else's words about this address.

    Scoped to `access`: an address on the launch list is not a request anyone
    owes an answer to, and reporting one as pending would tell a visitor their
    ask is in hand when nothing is waiting on a decision.
    """
    row = conn.execute(
        "SELECT 1 AS one FROM access_requests"
        " WHERE email = %s AND status = 'pending' AND kind = 'access' LIMIT 1",
        (email.strip().lower(),),
    ).fetchone()
    return row is not None


def by_id(conn: Conn, request_id: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM access_requests WHERE id = %s", (request_id,)).fetchone()


def count_pending(conn: Conn, *, kind: str | None = None) -> int:
    """How many are waiting, for the operator dashboard. Its own query rather
    than `len()` of a listing, which is one page and would report the page
    size as the total."""
    sql = "SELECT count(*) AS n FROM access_requests WHERE status = 'pending'"
    params: tuple[Any, ...] = ()
    if kind is not None:
        sql += " AND kind = %s"
        params = (kind,)
    return conn.execute(sql, params).fetchone()["n"]


def list_pending(
    conn: Conn, *, kind: str | None = None, limit: int | None = None, offset: int = 0
) -> list[dict[str, Any]]:
    """Every pending row, or one bucket of them, or one page of either.
    `kind=None` keeps the whole queue in one list for callers that show it
    undivided; `limit=None` returns all of them, which is what the CSV wants.

    Ordered by `created_at, id`. The timestamp alone is not a total order --
    two rows written in the same transaction share it -- and without one, two
    pages can share a row and skip another, which reads as a request that
    disappeared from the queue.
    """
    sql = (
        "SELECT r.*, i.slug AS wanted_slug FROM access_requests r"
        " LEFT JOIN instances i ON i.id = r.wanted_instance_id"
        " WHERE r.status = 'pending'"
    )
    params: tuple[Any, ...] = ()
    if kind is not None:
        sql += " AND r.kind = %s"
        params = (kind,)
    sql += " ORDER BY r.created_at, r.id"
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        params = (*params, limit, offset)
    return conn.execute(sql, params).fetchall()


def set_status(conn: Conn, request_id: str, status: str) -> None:
    conn.execute("UPDATE access_requests SET status = %s WHERE id = %s", (status, request_id))
