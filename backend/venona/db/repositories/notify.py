"""Every statement over `notify_destinations`, `notify_subscriptions` and
`notify_outbox`.

Two rules specific to this module, both about who may see what:

  * **A reader's destinations are read by `owner_id` and nothing else.** Same
    stance as `repositories/relevance.py` and `user_preferences`: rows are
    created only by a route whose subject is always the acting user, and there
    is no admin surface over them. The privacy is only as strong as the
    queries, so every reader-scoped function takes `user_id` and puts it in the
    `WHERE`.
  * **`target` is a credential and leaves here only for the sender.**
    `destinations_for_owner` and the admin listing both select an explicit
    column set that omits it; `claim_batch`'s join is the one read that carries
    it, because posting is the one thing that needs it.

See `db/repositories/__init__.py` for the rules that apply everywhere.
"""

from __future__ import annotations

from venona.db import core as db

#: The columns any surface may see. `target` is deliberately absent: a Slack
#: incoming URL is a bearer token in URL clothing, and a `SELECT *` here is how
#: it would reach a response model without anybody deciding to send it.
_PUBLIC = (
    "id, owner_kind, owner_id, kind, label, status, failures, last_error, "
    "created_by, created_at, updated_at"
)


# ── destinations ────────────────────────────────────────────────────


def insert_destination(
    conn: db.Conn,
    *,
    destination_id: str,
    owner_kind: str,
    owner_id: str | None,
    kind: str,
    label: str,
    target: str,
    created_by: str | None,
    now: str,
) -> None:
    conn.execute(
        "INSERT INTO notify_destinations "
        "(id, owner_kind, owner_id, kind, label, target, status, failures, "
        " created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)",
        (destination_id, owner_kind, owner_id, kind, label, target, created_by, now, now),
    )


def destination(conn: db.Conn, *, destination_id: str) -> db.Row | None:
    """One destination, without its target."""
    return conn.execute(
        f"SELECT {_PUBLIC} FROM notify_destinations WHERE id = ?", (destination_id,)
    ).fetchone()


def destination_target(conn: db.Conn, *, destination_id: str) -> str | None:
    """The credential, for a send or a test and nothing else."""
    row = conn.execute(
        "SELECT target FROM notify_destinations WHERE id = ?", (destination_id,)
    ).fetchone()
    return row["target"] if row else None


def instance_destinations(conn: db.Conn) -> list[db.Row]:
    return conn.execute(
        f"SELECT {_PUBLIC} FROM notify_destinations "
        "WHERE owner_kind = 'instance' ORDER BY created_at"
    ).fetchall()


def reader_destinations(conn: db.Conn, *, user_id: str) -> list[db.Row]:
    return conn.execute(
        f"SELECT {_PUBLIC} FROM notify_destinations "
        "WHERE owner_kind = 'reader' AND owner_id = ? ORDER BY created_at",
        (user_id,),
    ).fetchall()


def reader_destination_of_kind(conn: db.Conn, *, user_id: str, kind: str) -> db.Row | None:
    """A reader's destination of one kind, if they have one.

    The email case: flipping a delivery switch has to find or create the row
    addressed at the account's own address, and the reader never types it.
    """
    return conn.execute(
        f"SELECT {_PUBLIC} FROM notify_destinations "
        "WHERE owner_kind = 'reader' AND owner_id = ? AND kind = ? ORDER BY created_at LIMIT 1",
        (user_id, kind),
    ).fetchone()


def update_target(conn: db.Conn, *, destination_id: str, target: str, now: str) -> None:
    """Replace the address, and clear the strike history with it.

    A new address is a different destination in every way that matters, so a
    failure count carried across would strike out a working URL on its first
    mistake.
    """
    conn.execute(
        "UPDATE notify_destinations "
        "SET target = ?, status = 'active', failures = 0, last_error = NULL, updated_at = ? "
        "WHERE id = ?",
        (target, now, destination_id),
    )


def set_status(conn: db.Conn, *, destination_id: str, status: str, now: str) -> None:
    conn.execute(
        "UPDATE notify_destinations SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, destination_id),
    )


def clear_strikes(conn: db.Conn, *, destination_id: str, now: str) -> None:
    """The way back from `struck_out`, reachable from the app.

    Deliberately NOT a success: it clears the counter and the last error and
    does not claim anything was delivered. `sources/registry.py::clear_strikes`
    draws the same line, and ADR 0041 is the record of what it cost to have the
    only exit be a CLI incantation printed in a banner.
    """
    conn.execute(
        "UPDATE notify_destinations "
        "SET status = 'active', failures = 0, last_error = NULL, updated_at = ? "
        "WHERE id = ?",
        (now, destination_id),
    )


def record_failure(conn: db.Conn, *, destination_id: str, error: str, limit: int, now: str) -> int:
    """One more consecutive failure. Returns the new count.

    An atomic `UPDATE ... RETURNING` rather than a read, a decision and a write:
    at READ COMMITTED two drainer passes can both read the same count and both
    write it back, and the second one loses. `run_service.append_event` is the
    pattern this copies.
    """
    row = conn.execute(
        "UPDATE notify_destinations "
        "SET failures = failures + 1, "
        "    last_error = ?, "
        "    status = CASE WHEN failures + 1 >= ? THEN 'struck_out' ELSE status END, "
        "    updated_at = ? "
        "WHERE id = ? "
        "RETURNING failures",
        (error, limit, now, destination_id),
    ).fetchone()
    return int(row["failures"]) if row else 0


def record_success(conn: db.Conn, *, destination_id: str, now: str) -> None:
    conn.execute(
        "UPDATE notify_destinations "
        "SET failures = 0, last_error = NULL, updated_at = ? WHERE id = ?",
        (now, destination_id),
    )


def delete_destination(conn: db.Conn, *, destination_id: str) -> None:
    """Both subscriptions and queued deliveries go with it, by CASCADE."""
    conn.execute("DELETE FROM notify_destinations WHERE id = ?", (destination_id,))


def struck_out(conn: db.Conn) -> list[db.Row]:
    """Instance destinations venona has given up on.

    What the rail's mark and the banner read. Reader rows are excluded: a
    reader's broken webhook is the reader's to see, on their own page.
    """
    return conn.execute(
        f"SELECT {_PUBLIC} FROM notify_destinations "
        "WHERE owner_kind = 'instance' AND status = 'struck_out' ORDER BY updated_at DESC"
    ).fetchall()


# ── subscriptions ───────────────────────────────────────────────────


def subscriptions(conn: db.Conn, *, destination_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT notice_type FROM notify_subscriptions WHERE destination_id = ?",
        (destination_id,),
    ).fetchall()
    return [row["notice_type"] for row in rows]


def subscribe(conn: db.Conn, *, destination_id: str, notice_type: str, now: str) -> None:
    conn.execute(
        "INSERT INTO notify_subscriptions (destination_id, notice_type, created_at) "
        "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
        (destination_id, notice_type, now),
    )


def unsubscribe(conn: db.Conn, *, destination_id: str, notice_type: str) -> None:
    conn.execute(
        "DELETE FROM notify_subscriptions WHERE destination_id = ? AND notice_type = ?",
        (destination_id, notice_type),
    )


def subscribed_destinations(
    conn: db.Conn, *, notice_type: str, owner_id: str | None
) -> list[db.Row]:
    """Every active destination that asked to hear about this.

    `owner_id` is the addressing: None means the instance's own destinations,
    and a user id means that reader's. There is deliberately no "everybody"
    case. A reader notice fans out one reader at a time, so a bug in the caller
    cannot mail one person's brief to the whole readership.
    """
    if owner_id is None:
        return conn.execute(
            "SELECT d.id, d.kind, d.label, d.target FROM notify_destinations d "
            "JOIN notify_subscriptions s ON s.destination_id = d.id "
            "WHERE s.notice_type = ? AND d.owner_kind = 'instance' AND d.status = 'active'",
            (notice_type,),
        ).fetchall()
    return conn.execute(
        "SELECT d.id, d.kind, d.label, d.target FROM notify_destinations d "
        "JOIN notify_subscriptions s ON s.destination_id = d.id "
        "WHERE s.notice_type = ? AND d.owner_kind = 'reader' AND d.owner_id = ? "
        "AND d.status = 'active'",
        (notice_type, owner_id),
    ).fetchall()


def readers_subscribed(
    conn: db.Conn,
    *,
    notice_type: str,
    entitled_only: bool = False,
    now: str | None = None,
) -> list[str]:
    """Every reader with an active destination asking for this notice.

    THE READERSHIP, not the recently-seen readership. `users.last_seen_at`
    bounds the personal-brief WARM PASS because generating one costs a model
    call, and warming a brief nobody will read is pure spend. Telling somebody
    the brief has landed costs one POST, and the reader who has not visited for
    a week is precisely the one a message is for. Two different questions that a
    shared `recently_seen` would have quietly merged.

    ENTITLEMENT IS THE ONE THING BOTH QUESTIONS SHARE. `announce_brief` does not
    merely announce -- its docstring is "Say the shared brief has landed, AND
    SEND IT" -- so an unentitled reader with an address configured would keep
    receiving new briefs in full, straight past the paywall. That is a delivery
    path rather than a UI path, so no amount of front-end gating finds it, and
    it would have made the entire feature decorative for anyone who had ever set
    up email.

    This gates EVERY reader notice type, which is right while every one of them
    is about the paid product (a brief landing, their own brief being written).
    A notice about the subscription ITSELF would have to bypass this -- there is
    none today, because billing mail is Stripe's, and in-app dunning is a banner
    rather than a notice.
    """
    # The join and its predicate are added only when this instance charges. An
    # instance that takes no money has every account reading `none`, so an
    # unconditional filter would silently stop delivering to everybody.
    gate = " JOIN users u ON u.id = d.owner_id" if entitled_only else ""
    # `u.role = 'admin'` for the same reason `recently_seen` carries it: a
    # column predicate cannot see a role, and an administrator whose row says
    # `none` would stop being told their own instance had published.
    entitled = (
        " AND (u.role = 'admin' OR"
        " u.entitlement_status IN ('trialing', 'active', 'past_due', 'comped'))"
        if entitled_only
        else ""
    )
    # `now` is THE LOCAL BRAKE, and it is the same one `users_repo.recently_seen`
    # carries for the warm pass. The status column is pushed by the hub, so a
    # hub that is down -- or an instance dropped from the push sweep by its miss
    # count -- leaves an expired trial reading `trialing` here forever. Without
    # a date this instance can check for itself, that reader is mailed the whole
    # brief every day, indefinitely, on a path no screen shows.
    #
    # `comped` and admin carry no end date, so the brake must not catch them.
    brake = ""
    params: tuple[object, ...] = (notice_type,)
    if entitled_only and now is not None:
        brake = (
            " AND (u.role = 'admin' OR u.entitlement_status = 'comped'"
            " OR u.entitled_through IS NULL OR u.entitled_through >= ?)"
        )
        params = (notice_type, now)
    rows = conn.execute(
        "SELECT DISTINCT d.owner_id FROM notify_destinations d "  # noqa: S608 - literals above
        "JOIN notify_subscriptions s ON s.destination_id = d.id" + gate + " "
        "WHERE s.notice_type = ? AND d.owner_kind = 'reader' AND d.status = 'active' "
        "  AND d.owner_id IS NOT NULL" + entitled + brake,
        params,
    ).fetchall()
    return [row["owner_id"] for row in rows]


# ── the outbox ──────────────────────────────────────────────────────


def enqueue(
    conn: db.Conn,
    *,
    delivery_id: str,
    notice_type: str,
    destination_id: str,
    payload: str,
    dedupe_key: str | None,
    status: str,
    now: str,
) -> bool:
    """Queue one delivery. False if this notice already reached this destination.

    `ON CONFLICT DO NOTHING` against the partial unique index, so the dedupe is
    the database's answer rather than a read followed by a write that races it.
    """
    row = conn.execute(
        "INSERT INTO notify_outbox "
        "(id, notice_type, destination_id, payload, dedupe_key, status, attempts, "
        " next_attempt_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?) "
        "ON CONFLICT DO NOTHING RETURNING id",
        (
            delivery_id,
            notice_type,
            destination_id,
            payload,
            dedupe_key,
            status,
            now,
            now,
            now,
        ),
    ).fetchone()
    return row is not None


def claim_batch(conn: db.Conn, *, limit: int, now: str) -> list[db.Row]:
    """Take up to `limit` due deliveries, marking them `sending` in the same step.

    THE CHECK AND THE CLAIM ARE ONE STATEMENT, for the reason
    `run_service.create_run_guarded` gives: a select followed by an update lets
    two passes claim the same row, and the second send is a duplicate message to
    a real person. `FOR UPDATE SKIP LOCKED` in the subquery so a second drainer
    takes the next rows rather than blocking on these.

    Carries `target` because posting needs it; this is the only read that does.
    """
    return conn.execute(
        # The destination's columns are carried by the SUBQUERY, not by a join
        # beside it: in `UPDATE ... FROM`, the updated table is not in scope for
        # a second FROM item's ON clause, so `JOIN ... ON dest.id =
        # o.destination_id` out here is a planner error rather than a slow path.
        "UPDATE notify_outbox o "
        "SET status = 'sending', attempts = o.attempts + 1, updated_at = ? "
        "FROM ( "
        "  SELECT x.id, d.kind, d.target, d.label FROM notify_outbox x "
        "  JOIN notify_destinations d ON d.id = x.destination_id "
        "  WHERE x.status = 'queued' AND x.next_attempt_at <= ? AND d.status = 'active' "
        "  ORDER BY x.created_at "
        "  LIMIT ? FOR UPDATE OF x SKIP LOCKED "
        ") due "
        "WHERE o.id = due.id "
        "RETURNING o.id, o.notice_type, o.payload, o.attempts, o.destination_id, "
        "          due.kind, due.target, due.label",
        (now, now, limit),
    ).fetchall()


def mark_delivered(conn: db.Conn, *, delivery_id: str, now: str) -> None:
    conn.execute(
        "UPDATE notify_outbox "
        "SET status = 'delivered', delivered_at = ?, last_error = NULL, updated_at = ? "
        "WHERE id = ?",
        (now, now, delivery_id),
    )


def mark_retry(
    conn: db.Conn, *, delivery_id: str, error: str, next_attempt_at: str, now: str
) -> None:
    conn.execute(
        "UPDATE notify_outbox "
        "SET status = 'queued', last_error = ?, next_attempt_at = ?, updated_at = ? "
        "WHERE id = ?",
        (error, next_attempt_at, now, delivery_id),
    )


def mark_failed(conn: db.Conn, *, delivery_id: str, error: str, now: str) -> None:
    conn.execute(
        "UPDATE notify_outbox SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
        (error, now, delivery_id),
    )


def pending_counts(conn: db.Conn) -> dict[str, int]:
    """How many deliveries are waiting, by why they are waiting.

    `queued` is ordinary backlog and `blocked` is waiting on a transport that
    does not exist yet. Two different sentences on the admin screen, so two
    numbers rather than one total that would read as the same problem.
    """
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM notify_outbox "
        "WHERE status IN ('queued', 'blocked') GROUP BY status"
    ).fetchall()
    return {row["status"]: int(row["n"]) for row in rows}


def mark_blocked(conn: db.Conn, *, delivery_id: str, error: str, now: str) -> None:
    """Parked, not failed. Nothing is wrong with the address: there is no way to
    send to it yet, and `release_blocked` puts every one of these back on the
    queue the day there is."""
    conn.execute(
        "UPDATE notify_outbox SET status = 'blocked', last_error = ?, updated_at = ? WHERE id = ?",
        (error, now, delivery_id),
    )


def reclaim_stalled(conn: db.Conn, *, before: str, now: str) -> int:
    """Put `sending` rows that nobody is sending back on the queue.

    Every deploy of the api is stop then start, so a claim held when the process
    died is a row that would sit in `sending` forever. This is the same question
    the run lease answers -- how do you tell in flight from the process died --
    with the same answer a single-replica service can afford: nothing has been
    in flight for minutes, so anything that looks like it has, is not.
    """
    rows = conn.execute(
        "UPDATE notify_outbox "
        "SET status = 'queued', next_attempt_at = ?, updated_at = ? "
        "WHERE status = 'sending' AND updated_at < ? RETURNING id",
        (now, now, before),
    ).fetchall()
    return len(rows)


def release_blocked(conn: db.Conn, *, kind: str, now: str) -> int:
    """Return `blocked` deliveries of one destination kind to the queue.

    What happens the moment an email transport is configured: everything that
    parked because there was no way to send it becomes sendable, rather than
    staying parked until someone notices.
    """
    rows = conn.execute(
        "UPDATE notify_outbox o "
        "SET status = 'queued', next_attempt_at = ?, updated_at = ? "
        "FROM notify_destinations d "
        "WHERE d.id = o.destination_id AND o.status = 'blocked' AND d.kind = ? "
        "RETURNING o.id",
        (now, now, kind),
    ).fetchall()
    return len(rows)


def recent(conn: db.Conn, *, limit: int, offset: int = 0, owner_id: str | None) -> list[db.Row]:
    """The history panel. Instance rows when `owner_id` is None, else a reader's.

    Joined to the destination so a row can say where it went by NAME. The target
    is not selected: the history is the most-read surface here and the least
    reason to carry a credential.
    """
    if owner_id is None:
        return conn.execute(
            "SELECT o.id, o.notice_type, o.status, o.attempts, o.last_error, "
            "       o.created_at, o.delivered_at, d.label, d.kind "
            "FROM notify_outbox o JOIN notify_destinations d ON d.id = o.destination_id "
            "WHERE d.owner_kind = 'instance' "
            "ORDER BY o.created_at DESC, o.id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return conn.execute(
        "SELECT o.id, o.notice_type, o.status, o.attempts, o.last_error, "
        "       o.created_at, o.delivered_at, d.label, d.kind "
        "FROM notify_outbox o JOIN notify_destinations d ON d.id = o.destination_id "
        "WHERE d.owner_kind = 'reader' AND d.owner_id = ? "
        "ORDER BY o.created_at DESC, o.id DESC LIMIT ? OFFSET ?",
        (owner_id, limit, offset),
    ).fetchall()


def recent_count(conn: db.Conn, *, owner_id: str | None) -> int:
    """The whole set behind a history window, scoped the same way `recent` is."""
    if owner_id is None:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM notify_outbox o "
                "JOIN notify_destinations d ON d.id = o.destination_id "
                "WHERE d.owner_kind = 'instance'"
            ).fetchone()["n"]
        )
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM notify_outbox o "
            "JOIN notify_destinations d ON d.id = o.destination_id "
            "WHERE d.owner_kind = 'reader' AND d.owner_id = ?",
            (owner_id,),
        ).fetchone()["n"]
    )


def freshness_terms(conn: db.Conn) -> db.Row | None:
    """One row: how many deliveries there are, and when one last moved.

    Both halves are the point. A `COUNT(*)` alone cannot see a row going
    `queued` to `delivered`, which is the only change the history panel exists
    to show, and `services/freshness.py` documents that trap for every domain.
    """
    return conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(MAX(updated_at), '') AS latest FROM notify_outbox"
    ).fetchone()
