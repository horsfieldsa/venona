"""A separate front site's last reported version: one row, read and written."""

from __future__ import annotations

from typing import Any

from hub.db.core import Conn, jdump


def read(conn: Conn) -> dict[str, Any] | None:
    """The row, or None before the first successful read.

    `last_read` is the site's own body, stored verbatim, so a caller picks keys
    out of it the way `ops_api._health` does with an instance's envelope.
    """
    row = conn.execute("SELECT last_read, last_seen_at FROM site_status WHERE id = 1").fetchone()
    return dict(row) if row else None


def record(conn: Conn, *, body: dict[str, Any]) -> None:
    """Store a successful read, stamped by the database like `record_poll_success`.

    NOTHING CLEARS THIS. There is no companion that nulls the row on a failed
    fetch, and its absence is the design: a site that did not answer this cycle
    is not a site with no version, and blanking the page at the first CDN blip
    would lose the only record of what is published out there.
    """
    conn.execute(
        "UPDATE site_status SET last_read = %s::jsonb, last_seen_at = now() WHERE id = 1",
        (jdump(body),),
    )
