"""`db.snapshot` is one moment, and it is not the run's connection.

Two separate claims, and the export depends on both.

The first is the reason it exists. A vault writer runs a dozen whole-table
reads, and at READ COMMITTED each one takes a fresh snapshot -- so a research
run committing between the names read and the relationships read yields a
folder containing a link to a note the same folder declined to write.

The second is the trap. `run_service.append_event` opens its own
`db.transaction`, and that one refuses to nest. A snapshot held on a run's own
connection turns every progress event into a `RuntimeError`, and the failure
does not appear until something emits -- which, for a run whose whole job takes
minutes, is long after it started.
"""

from __future__ import annotations

import pytest
from venona.db import core as db


def test_a_snapshot_does_not_see_a_later_write(conn: db.Conn, database_url: str) -> None:
    """The whole point: one moment, however many reads."""
    writer = db.connect(database_url)
    try:
        before = conn.execute("SELECT count(*) AS n FROM runs").fetchone()["n"]
        with db.snapshot(conn):
            assert conn.execute("SELECT count(*) AS n FROM runs").fetchone()["n"] == before

            writer.execute(
                "INSERT INTO runs (id, kind, status, started_at) VALUES (?, ?, ?, ?)",
                ("run:snapshot-probe", "export", "succeeded", "2026-09-10T12:00:00.000Z"),
            )
            writer.commit()

            # Still `before`, on a second read inside the same snapshot. Under
            # READ COMMITTED this is where the count would move.
            assert conn.execute("SELECT count(*) AS n FROM runs").fetchone()["n"] == before

        assert conn.execute("SELECT count(*) AS n FROM runs").fetchone()["n"] == before + 1
    finally:
        writer.close()


def test_a_snapshot_refuses_to_write(conn: db.Conn) -> None:
    """READ ONLY, so a stray write is an error rather than a silent rollback."""
    with pytest.raises(Exception):  # noqa: B017 -- the driver's own class, not ours
        with db.snapshot(conn):
            conn.execute(
                "INSERT INTO runs (id, kind, status, started_at) VALUES (?, ?, ?, ?)",
                ("run:should-not-exist", "export", "succeeded", "2026-09-10T12.00Z"),
            )


def test_a_snapshot_refuses_to_nest_on_a_connection_that_is_emitting(conn: db.Conn) -> None:
    """The trap, stated as a test.

    A run's connection is inside a transaction whenever it is writing an event.
    Opening a snapshot on it would issue a second BEGIN, which Postgres ignores
    with a warning -- so the inner COMMIT would end the OUTER transaction and
    everything after it would commit one statement at a time. Refused instead.
    """
    with db.transaction(conn):
        with pytest.raises(RuntimeError, match="second connection"):
            with db.snapshot(conn):
                pass  # pragma: no cover -- the raise is the assertion
