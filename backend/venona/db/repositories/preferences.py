"""Every statement over `user_preferences`: how a reader wants things shown.

Keyed on `user_id` on both the read and the write, for the reason
`repositories/relevance.py` opens with: the privacy stance is only as strong as
the queries.

See `db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def preferences_row(conn: db.Conn, *, user_id: str) -> db.Row | None:
    return conn.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,)).fetchone()


def upsert_timezone(conn: db.Conn, *, user_id: str, timezone: str | None, now: str) -> None:
    conn.execute(
        "INSERT INTO user_preferences (user_id, timezone, updated_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET timezone = EXCLUDED.timezone, "
        "updated_at = EXCLUDED.updated_at",
        (user_id, timezone, now),
    )
