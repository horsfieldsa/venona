"""The hub's own switches: one row, read on the paths that need them.

SEPARATE FROM `config.py` because these are decisions an operator makes and
changes, not configuration a deploy carries. The two were the same thing while
`HUB_SELF_SERVE` was an environment variable, and the cost of that was a deploy
to change the hub's front door.

Per-instance switches are not here -- `billing_enforced` lives on the instance
row, because a domain charging is a fact about that domain (migration 0008).
"""

from __future__ import annotations

from typing import Any

from hub.db.core import Conn


def get(conn: Conn) -> dict[str, Any]:
    """The row. Migration 0008 inserts it, so this cannot be None in a migrated
    database -- and a caller that defended against None would be defending
    against a state that means the schema is wrong, which is not a state to
    paper over."""
    return conn.execute("SELECT * FROM hub_settings WHERE id = 1").fetchone()


def self_serve(conn: Conn) -> bool:
    """Whether a signed-in stranger is granted a domain without a person.

    FAILS CLOSED if the row is somehow absent: a hub that has lost this row
    should queue requests for a human, never open the door. That is the one
    place a defensive default earns its keep, because the failure is silent and
    the thing on the other side of the door is model spend.
    """
    row = get(conn)
    return bool(row["self_serve"]) if row else False


def set_self_serve(conn: Conn, *, enabled: bool, by: str | None) -> None:
    conn.execute(
        "UPDATE hub_settings SET self_serve = %s, updated_at = now(), updated_by = %s WHERE id = 1",
        (enabled, by),
    )
