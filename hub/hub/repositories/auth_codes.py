"""Authorization codes: mint, and a redeem that IS the single-use guarantee."""

from __future__ import annotations

from typing import Any

from hub.db.core import Conn

CODE_TTL_SECONDS = 300


def mint(conn: Conn, *, code_hash: str, user_id: str, instance_id: str, nonce: str) -> None:
    conn.execute(
        "INSERT INTO auth_codes (code_hash, user_id, instance_id, nonce, expires_at)"
        " VALUES (%s, %s, %s, %s, now() + make_interval(secs => %s))",
        (code_hash, user_id, instance_id, nonce, CODE_TTL_SECONDS),
    )


def redeem(conn: Conn, code_hash: str) -> dict[str, Any] | None:
    """Single-use, atomically: the row flips to redeemed in the same statement
    that reads it, so two concurrent redemptions cannot both win -- at READ
    COMMITTED the second UPDATE waits and then matches zero rows."""
    return conn.execute(
        "UPDATE auth_codes SET redeemed_at = now()"
        " WHERE code_hash = %s AND redeemed_at IS NULL AND expires_at > now()"
        " RETURNING user_id, instance_id, nonce",
        (code_hash,),
    ).fetchone()
