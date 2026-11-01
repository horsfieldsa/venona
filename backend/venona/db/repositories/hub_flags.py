"""What the hub has told this instance to do. One row (migration 0024).

READ THROUGH `api/authz.billing_enforced()`, never directly on a request path:
the paywall is asked about on every request, and a database round trip per
request to answer a question that changes twice a year is the wrong trade. The
cache and its invalidation live there; this is only the storage.
"""

from __future__ import annotations

from venona.db import core as db


def billing_enforced(conn: db.Conn) -> bool:
    """Whether the hub says this instance charges.

    FALSE when the row is missing. That state means the schema is behind, and
    the honest failure for a paywall whose configuration has gone missing is to
    let readers read -- not to lock out a readership because a migration did not
    run.
    """
    row = conn.execute("SELECT billing_enforced FROM hub_flags WHERE id = 1").fetchone()
    return bool(row["billing_enforced"]) if row else False


def set_billing_enforced(conn: db.Conn, *, enforced: bool, now: str) -> None:
    conn.execute(
        "UPDATE hub_flags SET billing_enforced = ?, updated_at = ? WHERE id = 1",
        (enforced, now),
    )
