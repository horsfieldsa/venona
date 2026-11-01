"""The instance-wide session epoch. One row, two statements, read through a
module cache in `api/security.py` because `verify_session` runs on every call.
"""

from __future__ import annotations

from venona.db import core as db


def read(conn: db.Conn) -> float:
    """Fails OPEN: no row means no break-glass logout has happened, and reading
    that as "revoke everything" would sign out a readership over a migration
    that had not run."""
    row = conn.execute("SELECT epoch FROM auth_state WHERE id = 1").fetchone()
    if row is None:
        return 0.0
    try:
        return float(row["epoch"])
    except (TypeError, ValueError):
        return 0.0


def bump(conn: db.Conn, *, epoch: str, now: str) -> None:
    """Revoke every session issued before `epoch`.

    INSERT with a conflict clause rather than UPDATE, so it works where the
    singleton row was never inserted -- this is the write you reach for when
    something is already wrong.
    """
    conn.execute(
        "INSERT INTO auth_state (id, epoch, updated_at) VALUES (1, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET epoch = EXCLUDED.epoch, "
        "updated_at = EXCLUDED.updated_at",
        (epoch, now),
    )
