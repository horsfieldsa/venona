"""Every statement over `quota_samples`.

The readings the CLI hands back on calls we were making anyway. See
`db/repositories/__init__.py` for the rules, and `migrations/
0017_quota_samples.sql` for why a reading is a row rather than a column.
"""

from __future__ import annotations

from venona.db import core as db


def record_sample(
    conn: db.Conn,
    *,
    sample_id: str,
    observed_at: str,
    window_name: str,
    utilization: float,
    resets_at: str,
    status: str,
    representative: bool,
    run_id: str | None,
    harness: str | None = None,
) -> None:
    """Idempotent by index, not by check.

    Every call re-observes the same windows, so this runs hundreds of times a
    run and almost always describes a reading already stored. `ON CONFLICT DO
    NOTHING` against `ux_quota_samples_reading` makes the repeat free and, more
    importantly, keeps this a single statement: the read-then-write it replaces
    would be two transactions racing over the same row at READ COMMITTED.

    The consequence worth knowing: a window earns a row only when its reading
    MOVES, so the series is a step function and gaps in it are stillness, not
    missing data.
    """
    conn.execute(
        "INSERT INTO quota_samples "
        "  (id, observed_at, window_name, utilization, resets_at, status, "
        "   representative, run_id, harness) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (harness, window_name, resets_at, utilization) DO NOTHING",
        (
            sample_id,
            observed_at,
            window_name,
            utilization,
            resets_at,
            status,
            representative,
            run_id,
            harness,
        ),
    )


def latest_per_window(conn: db.Conn, *, harness: str | None = None) -> list[db.Row]:
    """The current reading for each window, newest observation per window.

    Filtered to one service, because a window belongs to the service that
    reported it. Unfiltered, an instance that has switched shows both sets
    forever and half of them are frozen at the moment a previous service
    last reported -- which reads as a full window rather than as a stale one.

    `DISTINCT ON` rather than a group-by with a self-join: the ordering the
    index already provides is the answer, and the alternative re-reads the
    table once per window.

    Ordered by `observed_at` and not by `resets_at` -- a window that has just
    rolled over has a NEWER reset time but its reading is the one we took last,
    and sorting on the reset would show the previous week during the first call
    of a new one.
    """
    return list(
        conn.execute(
            "SELECT DISTINCT ON (window_name) "
            "       window_name, utilization, resets_at, status, representative, observed_at "
            "  FROM quota_samples "
            " WHERE harness IS NOT DISTINCT FROM ? "
            " ORDER BY window_name, observed_at DESC",
            (harness,),
        ).fetchall()
    )


def series(conn: db.Conn, *, window_name: str, since: str) -> list[db.Row]:
    """One window's readings over time, oldest first, for a burn curve.

    `since` is an ISO-8601 floor computed by the CALLER: this package is not
    allowed to ask what time it is, and a repository that did would be
    untestable without freezing the clock. Same rule `metrics.cost_by_day`
    follows.
    """
    return list(
        conn.execute(
            "SELECT observed_at, utilization, resets_at, status "
            "  FROM quota_samples "
            " WHERE window_name = ? AND observed_at >= ? "
            " ORDER BY observed_at",
            (window_name, since),
        ).fetchall()
    )
