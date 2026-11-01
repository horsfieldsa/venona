"""Destinations, subscriptions and the outbox. The only SQL the hub's notices use.

`target` IS A CREDENTIAL and this module is where that is enforced rather than
remembered. Every read a page can reach omits the column; exactly one function
returns it, and it is named so that a reviewer can see at the call site which
one is which. A `SELECT *` here would put a live Slack address into the console
markup and nothing would look wrong.

Timestamps are `now()` in the statement, not a parameter, for the reason the
rest of the hub does it: there is one writer and no replay, so the database
clock is the only clock that matters and a caller cannot get it wrong. The
instance passes ISO strings because its offline replay has to reproduce them.
"""

from __future__ import annotations

from typing import Any

from hub.db.core import Conn

#: Everything a page may see. `target` is deliberately absent -- see the module
#: docstring. Kept as one string so a new column has to be added here on
#: purpose, rather than arriving through a `*`.
_PUBLIC = "id, kind, label, status, failures, last_error, created_by, created_at, updated_at"

#: Consecutive failures before the hub stops sending to a destination. Three,
#: the same number and the same reasoning as `instances.MISS_LIMIT`: one
#: transient blip disables nothing, a moved endpoint disables itself.
STRIKE_LIMIT = 3


# ── destinations ─────────────────────────────────────────────────────


def destinations(conn: Conn) -> list[dict[str, Any]]:
    return conn.execute(
        f"SELECT {_PUBLIC} FROM notify_destinations ORDER BY created_at"  # noqa: S608 - literal
    ).fetchall()


def destination(conn: Conn, destination_id: str) -> dict[str, Any] | None:
    return conn.execute(
        f"SELECT {_PUBLIC} FROM notify_destinations WHERE id = %s",  # noqa: S608 - literal
        (destination_id,),
    ).fetchone()


def destination_target(conn: Conn, destination_id: str) -> str | None:
    """The credential itself. The ONLY read that returns it.

    Two callers, both of which are about to dial it: the sender, and a test
    send. Anything rendering a destination wants `target_hint` instead.
    """
    row = conn.execute(
        "SELECT target FROM notify_destinations WHERE id = %s", (destination_id,)
    ).fetchone()
    return row["target"] if row else None


def insert(
    conn: Conn,
    *,
    destination_id: str,
    kind: str,
    label: str,
    target: str,
    created_by: str | None,
) -> bool:
    """False when this address is already registered.

    A duplicate would double every message rather than widen anything, so it is
    refused at the index and reported as a sentence in the form.
    """
    row = conn.execute(
        "INSERT INTO notify_destinations (id, kind, label, target, created_by)"
        " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (kind, target) DO NOTHING RETURNING id",
        (destination_id, kind, label, target, created_by),
    ).fetchone()
    return row is not None


def delete(conn: Conn, destination_id: str) -> None:
    """The subscriptions and the queued deliveries go with it, by CASCADE."""
    conn.execute("DELETE FROM notify_destinations WHERE id = %s", (destination_id,))


def rename(conn: Conn, *, destination_id: str, label: str) -> None:
    conn.execute(
        "UPDATE notify_destinations SET label = %s, updated_at = now() WHERE id = %s",
        (label, destination_id),
    )


def clear_strikes(conn: Conn, destination_id: str) -> None:
    """Put a struck-out destination back in service.

    The way back is a button, which is what ADR 0041 cost to learn the first
    time: a destination that disables itself and cannot be re-enabled from the
    app is a destination somebody edits the database to fix.
    """
    conn.execute(
        "UPDATE notify_destinations"
        " SET status = 'active', failures = 0, last_error = NULL, updated_at = now()"
        " WHERE id = %s",
        (destination_id,),
    )


def record_success(conn: Conn, destination_id: str) -> None:
    conn.execute(
        "UPDATE notify_destinations"
        " SET failures = 0, last_error = NULL, updated_at = now() WHERE id = %s",
        (destination_id,),
    )


def record_failure(conn: Conn, *, destination_id: str, error: str) -> int:
    """Count the strike and strike the destination out on the third. Returns
    the new count.

    One statement, so the read and the write cannot disagree: two senders
    counting the same failure would otherwise both read two and both write
    three.
    """
    row = conn.execute(
        "UPDATE notify_destinations"
        " SET failures = failures + 1,"
        "     last_error = %s,"
        "     status = CASE WHEN failures + 1 >= %s THEN 'struck_out' ELSE status END,"
        "     updated_at = now()"
        " WHERE id = %s RETURNING failures",
        (error[:400], STRIKE_LIMIT, destination_id),
    ).fetchone()
    return int(row["failures"]) if row else 0


# ── subscriptions ────────────────────────────────────────────────────


def subscriptions(conn: Conn, destination_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT notice_type FROM notify_subscriptions WHERE destination_id = %s",
        (destination_id,),
    ).fetchall()
    return {row["notice_type"] for row in rows}


def subscribe(conn: Conn, *, destination_id: str, notice_type: str) -> None:
    conn.execute(
        "INSERT INTO notify_subscriptions (destination_id, notice_type)"
        " VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (destination_id, notice_type),
    )


def unsubscribe(conn: Conn, *, destination_id: str, notice_type: str) -> None:
    conn.execute(
        "DELETE FROM notify_subscriptions WHERE destination_id = %s AND notice_type = %s",
        (destination_id, notice_type),
    )


def record_offer(conn: Conn, *, destination_id: str, notice_type: str) -> bool:
    """Note that this destination has been shown this switch. False if it already
    had been.

    The insert IS the question, so two boots racing each other cannot both
    decide a type is new and both tick it back on over a deliberate no.
    """
    row = conn.execute(
        "INSERT INTO notify_offered (destination_id, notice_type) VALUES (%s, %s)"
        " ON CONFLICT DO NOTHING RETURNING destination_id",
        (destination_id, notice_type),
    ).fetchone()
    return row is not None


def destination_ids(conn: Conn) -> list[str]:
    """Every destination, whatever its status. A struck-out one still holds the
    operator's choices and comes back when they fix the address, so it is
    offered a new type like any other."""
    rows = conn.execute("SELECT id FROM notify_destinations ORDER BY id").fetchall()
    return [row["id"] for row in rows]


def subscribed(conn: Conn, notice_type: str) -> list[dict[str, Any]]:
    """Every ACTIVE destination that asked to hear about this.

    `status = 'active'` here and again in `claim_batch`, deliberately: the first
    stops a struck-out destination from accumulating a queue it will never
    drain, and the second stops one that struck out after a notice was already
    queued from being dialled anyway.
    """
    return conn.execute(
        "SELECT d.id, d.kind, d.label FROM notify_destinations d"
        " JOIN notify_subscriptions s ON s.destination_id = d.id"
        " WHERE s.notice_type = %s AND d.status = 'active'",
        (notice_type,),
    ).fetchall()


# ── the outbox ───────────────────────────────────────────────────────


def enqueue(
    conn: Conn,
    *,
    delivery_id: str,
    notice_type: str,
    destination_id: str,
    payload: str,
    dedupe_key: str | None,
) -> bool:
    """Queue one delivery. False if this notice already reached this destination.

    `ON CONFLICT DO NOTHING` against the partial unique index, so the dedupe is
    the database's answer rather than a read followed by a write that races it.
    """
    row = conn.execute(
        "INSERT INTO notify_outbox (id, notice_type, destination_id, payload, dedupe_key)"
        " VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
        (delivery_id, notice_type, destination_id, payload, dedupe_key),
    ).fetchone()
    return row is not None


def claim_batch(conn: Conn, limit: int) -> list[dict[str, Any]]:
    """Take up to `limit` due deliveries, marking them `sending` in one step.

    THE CHECK AND THE CLAIM ARE ONE STATEMENT. A select followed by an update
    lets two passes take the same row, and the second send is a duplicate
    message to a real person. `FOR UPDATE SKIP LOCKED` in the subquery so a
    second pass takes the next rows rather than blocking on these.

    Carries `target` because posting needs it; this is the only other read that
    does, and it is not reachable from a page.
    """
    return conn.execute(
        # The destination's columns come from the SUBQUERY, not a join beside
        # it: in `UPDATE ... FROM`, the updated table is not in scope for a
        # second FROM item's ON clause.
        "UPDATE notify_outbox o"
        " SET status = 'sending', attempts = o.attempts + 1, updated_at = now()"
        " FROM ("
        "   SELECT x.id, d.kind, d.target, d.label FROM notify_outbox x"
        "   JOIN notify_destinations d ON d.id = x.destination_id"
        "   WHERE x.status = 'queued' AND x.next_attempt_at <= now() AND d.status = 'active'"
        "   ORDER BY x.created_at"
        "   LIMIT %s FOR UPDATE OF x SKIP LOCKED"
        " ) due"
        " WHERE o.id = due.id"
        " RETURNING o.id, o.notice_type, o.payload, o.attempts, o.destination_id,"
        "           due.kind, due.target, due.label",
        (limit,),
    ).fetchall()


def mark_delivered(conn: Conn, delivery_id: str) -> None:
    conn.execute(
        "UPDATE notify_outbox"
        " SET status = 'delivered', delivered_at = now(), updated_at = now(), last_error = NULL"
        " WHERE id = %s",
        (delivery_id,),
    )


def mark_retry(conn: Conn, *, delivery_id: str, error: str, delay_seconds: float) -> None:
    """Park it for another go. The delay is computed by the sender's backoff."""
    conn.execute(
        "UPDATE notify_outbox"
        " SET status = 'queued',"
        "     last_error = %s,"
        "     next_attempt_at = now() + make_interval(secs => %s),"
        "     updated_at = now()"
        " WHERE id = %s",
        (error[:400], delay_seconds, delivery_id),
    )


def mark_failed(conn: Conn, *, delivery_id: str, error: str) -> None:
    conn.execute(
        "UPDATE notify_outbox SET status = 'failed', last_error = %s, updated_at = now()"
        " WHERE id = %s",
        (error[:400], delivery_id),
    )


def reclaim_stalled(conn: Conn, older_than_seconds: int) -> int:
    """Put claims nobody is holding back on the queue.

    Every deploy of the hub is stop then start, so a claim held when the process
    died is the ordinary case rather than a crash path.
    """
    rows = conn.execute(
        "UPDATE notify_outbox SET status = 'queued', updated_at = now()"
        " WHERE status = 'sending' AND updated_at < now() - make_interval(secs => %s)"
        " RETURNING id",
        (older_than_seconds,),
    ).fetchall()
    return len(rows)


def recent(conn: Conn, limit: int) -> list[dict[str, Any]]:
    """The history panel: what was sent, where, and whether it landed."""
    return conn.execute(
        "SELECT o.id, o.notice_type, o.status, o.attempts, o.last_error, o.created_at,"
        "       o.delivered_at, d.label"
        " FROM notify_outbox o JOIN notify_destinations d ON d.id = o.destination_id"
        " ORDER BY o.created_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
