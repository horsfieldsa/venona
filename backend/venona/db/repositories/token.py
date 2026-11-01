"""Every statement that token_service runs.

Serves `services/token_service.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def insert_mcp_tokens(conn: db.Conn, p0, p1, p2, p3, p4, p5, p6, p7):
    return conn.execute(
        "INSERT INTO mcp_tokens "
        "(id, label, token_hash, prefix, created_at, user_id, scope, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (p0, p1, p2, p3, p4, p5, p6, p7),
    )


def update_mcp_tokens_by_last_used_at_id(conn: db.Conn, p0, p1):
    return conn.execute("UPDATE mcp_tokens SET last_used_at = ? WHERE id = ?", (p0, p1))


def update_mcp_tokens_by_revoked_at_id(conn: db.Conn, p0, p1):
    return conn.execute(
        "UPDATE mcp_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (p0, p1)
    )


def select_mcp_tokens_by_id(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM mcp_tokens WHERE id = ?", (p0,))


def select_mcp_tokens_by_token_hash(conn: db.Conn, p0):
    return conn.execute(
        "SELECT id, user_id, scope, expires_at FROM mcp_tokens WHERE token_hash = ? "
        "AND revoked_at IS NULL",
        (p0,),
    )


def listing(conn: db.Conn, *, include_revoked: bool, user_id: str | None) -> list[db.Row]:
    """Tokens, newest first, optionally scoped to one owner.

    The WHERE clause is assembled from two booleans rather than interpolated from
    anything a caller typed; `user_id` is a bound parameter.
    """
    clauses, params = [], []
    if not include_revoked:
        clauses.append("revoked_at IS NULL")
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    sql = "SELECT * FROM mcp_tokens"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"
    return list(conn.execute(sql, tuple(params) if params else None).fetchall())


def listing_with_owner(
    conn: db.Conn,
    *,
    include_revoked: bool = False,
    limit: int | None = None,
    offset: int = 0,
    user_id: str | None = None,
) -> list[db.Row]:
    """Every token, with its owner's ADDRESS and the name of whatever it was
    handed to. `user_id` scopes it to one owner; None is the instance-wide read.

    BOTH HTTP LISTINGS COME THROUGH HERE and the difference between them is this
    one argument. `/api/admin/tokens` passes None and sees the instance;
    `/api/meta/mcp` passes the caller and sees only theirs. They were separate
    queries when the scoped one needed fewer columns, and they stopped being
    different questions the moment both screens grew a Disconnect button -- one
    of them would have gone stale about what a row means.

    LEFT JOIN TWICE, for the same reason both times: a row must never vanish
    because the thing beside it is absent.

      * `users` -- `mcp_tokens.user_id` is NULL for every token minted before
        identity existed. An inner join would silently drop exactly the oldest
        credentials on the instance, which are the ones least likely to be
        remembered. `user_email` is None there and the screen says so.
      * `oauth_clients` -- `client_id` is NULL for a token minted at the CLI, and
        it is deliberately not a foreign key (`0003_oauth.sql`), so it can also
        name a registration that has since been purged. `client_name` is None in
        both cases, which reads as "made in a terminal" and is right for the
        first and harmless for the second.

    Joined in the query rather than looked up per row, which is the same choice
    `recent_events` makes: a `users` read per token is `glossary.py`'s
    201-queries-for-200-rows in a new place.
    """
    sql = (
        "SELECT t.id, t.label, t.prefix, t.scope, t.created_at, t.last_used_at, "
        "t.revoked_at, t.expires_at, t.user_id, u.email AS user_email, "
        "c.client_name AS client_name "
        "FROM mcp_tokens t LEFT JOIN users u ON u.id = t.user_id "
        "LEFT JOIN oauth_clients c ON c.client_id = t.client_id"
    )
    # Assembled from a flag and a None-check, never from anything a caller typed;
    # `user_id` and `limit` are bound parameters.
    clauses, params = [], []
    if not include_revoked:
        clauses.append("t.revoked_at IS NULL")
    if user_id is not None:
        clauses.append("t.user_id = ?")
        params.append(user_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    # `t.id` breaks `created_at` ties so a bounded read is deterministic.
    sql += " ORDER BY t.created_at DESC, t.id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend((limit, offset))
    return list(conn.execute(sql, tuple(params) if params else None).fetchall())


def count_with_owner(conn: db.Conn, *, include_revoked: bool = False, user_id: str | None) -> int:
    """The whole set behind `listing_with_owner`, under the same two filters.

    Shares their assembly so a window and its total cannot come to disagree
    about what they are counting.
    """
    clauses, params = [], []
    if not include_revoked:
        clauses.append("revoked_at IS NULL")
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    sql = "SELECT COUNT(*) AS n FROM mcp_tokens"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return int(conn.execute(sql, tuple(params) if params else None).fetchone()["n"])


def count_live(conn: db.Conn) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM mcp_tokens WHERE revoked_at IS NULL").fetchone()
    return int(row["n"]) if row else 0


def attach_token_to_client(conn: db.Conn, *, token_id: str, client_id: str) -> None:
    """Which OAuth client a token came from. NULL for one minted at the CLI."""
    conn.execute("UPDATE mcp_tokens SET client_id = ? WHERE id = ?", (client_id, token_id))
