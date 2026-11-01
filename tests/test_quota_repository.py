"""The quota ledger's one interesting property: it does not grow without moving.

Every model call re-observes the same windows, so a research run offers this
table several hundred readings that are almost all identical. What stops that
becoming several hundred rows is a unique index, not a check-then-insert -- and
the difference matters, because a check-then-insert here is two transactions
racing over the same row at READ COMMITTED.
"""

from __future__ import annotations

from venona.db import core as db
from venona.db.repositories import quota as repo

READING = {
    "observed_at": "2026-08-30T12:00:00Z",
    "window_name": "seven_day",
    "utilization": 0.8,
    "resets_at": "2026-08-30T21:00:00Z",
    "status": "allowed_warning",
    "representative": True,
    "run_id": "run:abc",
}


def test_an_unmoved_window_does_not_grow_the_table(conn: db.Conn) -> None:
    for i in range(50):
        repo.record_sample(conn, sample_id=f"call{i}:seven_day", **READING)

    assert len(repo.series(conn, window_name="seven_day", since="2026-01-01T00:00:00Z")) == 1


def test_a_moved_window_earns_a_row(conn: db.Conn) -> None:
    repo.record_sample(conn, sample_id="a:seven_day", **READING)
    repo.record_sample(
        conn,
        sample_id="b:seven_day",
        **{**READING, "utilization": 0.81, "observed_at": "2026-08-30T13:00:00Z"},
    )

    rows = repo.series(conn, window_name="seven_day", since="2026-01-01T00:00:00Z")

    assert [r["utilization"] for r in rows] == [0.8, 0.81]


def test_a_new_period_is_a_new_row_at_the_same_level(conn: db.Conn) -> None:
    """A window that rolls over reads 0.8 again against a LATER reset. Keying
    the index on the reset as well as the level is what keeps last week's
    reading from swallowing this week's."""
    repo.record_sample(conn, sample_id="a:seven_day", **READING)
    repo.record_sample(
        conn,
        sample_id="b:seven_day",
        **{**READING, "resets_at": "2026-09-06T21:00:00Z", "observed_at": "2026-09-01T12:00:00Z"},
    )

    assert len(repo.series(conn, window_name="seven_day", since="2026-01-01T00:00:00Z")) == 2


def test_latest_per_window_takes_the_newest_observation(conn: db.Conn) -> None:
    """Newest by when it was OBSERVED, not by when it resets: during the first
    call of a new period the newest reading has the later reset, but during the
    rollover itself ordering on the reset would show the previous period."""
    for util, at in ((0.2, "2026-08-30T10:00:00Z"), (0.9, "2026-08-30T18:00:00Z")):
        repo.record_sample(
            conn, sample_id=f"{at}:seven_day", **{**READING, "utilization": util, "observed_at": at}
        )
    repo.record_sample(
        conn,
        sample_id="x:five_hour",
        **{**READING, "window_name": "five_hour", "utilization": 0.1, "representative": False},
    )

    latest = {r["window_name"]: r for r in repo.latest_per_window(conn)}

    assert set(latest) == {"seven_day", "five_hour"}
    assert latest["seven_day"]["utilization"] == 0.9
    assert latest["seven_day"]["representative"] is True
    assert latest["five_hour"]["representative"] is False


def test_series_is_bounded_by_the_callers_floor(conn: db.Conn) -> None:
    """`since` is computed by the caller. This package is not allowed to ask what
    time it is, and a repository that did would be untestable without freezing
    the clock."""
    for util, at in ((0.1, "2026-08-01T00:00:00Z"), (0.5, "2026-08-30T00:00:00Z")):
        repo.record_sample(
            conn, sample_id=f"{at}:seven_day", **{**READING, "utilization": util, "observed_at": at}
        )

    recent = repo.series(conn, window_name="seven_day", since="2026-08-15T00:00:00Z")

    assert [r["utilization"] for r in recent] == [0.5]


def test_nothing_observed_reports_nothing(conn: db.Conn) -> None:
    """The state a fresh instance is in, and the one a credential without the
    scope stays in. It must be distinguishable from a window at zero."""
    assert repo.latest_per_window(conn) == []
