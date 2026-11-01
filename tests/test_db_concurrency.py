"""The database under the concurrency contract C11 actually creates.

Up to one research run plus three light runs execute at once, each on its own
connection, and every stage of every one of them appends to `run_events`. That is
the hottest write path in the system and the one with the read-then-write shape
that starting a transaction cannot make safe -- on SQLite it produced
`SQLITE_BUSY_SNAPSHOT`, which `busy_timeout` did not cover, and on Postgres it
produces a primary-key collision between two writers that read the same
`MAX(seq)`. `append_event` serialises per run with an advisory lock; the tests at
the bottom of this file are what say so.
"""

from __future__ import annotations

import threading

import psycopg
import pytest
from venona.db import core as db
from venona.db import retry
from venona.services import run_service


def _run(conn: db.Conn, run_id: str) -> str:
    run_service.create_run(
        conn, run_id=run_id, kind="on_demand", params={}, profile_hash="h", now="t0"
    )
    return run_id


# ── the classifier ───────────────────────────────────────────────────


def _pg_error(sqlstate: str, message: str = "boom") -> psycopg.Error:
    """A psycopg error carrying a server SQLSTATE.

    `sqlstate` is read-only on a real `psycopg.Error`, so this subclasses rather
    than assigning an attribute. The SQLite version of these tests set
    `exc.sqlite_errorname` on a plain instance -- which worked, and became fiction
    the moment the driver changed: the attribute existed only because the test put
    it there.
    """

    class _Error(psycopg.Error):
        @property
        def sqlstate(self) -> str:
            return sqlstate

    return _Error(message)


def test_a_serialization_failure_is_retryable() -> None:
    """40001. Two transactions touched the same rows and the server could not
    order them; retrying is the documented response, not a workaround."""
    assert retry.is_retryable(_pg_error("40001", "could not serialize access"))


def test_a_deadlock_is_retryable() -> None:
    """40P01. The server already picked a victim, so the loser retries."""
    assert retry.is_retryable(_pg_error("40P01", "deadlock detected"))


@pytest.mark.negative
def test_a_real_error_is_not_retryable() -> None:
    """Retrying a schema error just fails four times more slowly."""
    assert not retry.is_retryable(_pg_error("42P01", 'relation "nope" does not exist'))


@pytest.mark.negative
def test_a_unique_violation_is_not_retryable() -> None:
    """23505 is deliberately absent from `RETRYABLE`.

    The read-then-write races that produce it look transient, but retrying treats
    a collision as normal and would equally mask a real duplicate-key bug.
    `append_event` takes an advisory lock so the collision cannot happen at all --
    the fix rather than the mitigation.
    """
    assert not retry.is_retryable(_pg_error("23505", "duplicate key value"))


@pytest.mark.negative
def test_classification_is_by_sqlstate_not_by_message() -> None:
    """`llm/budget.py` classifies retryability by substring and matches "500"
    inside any message containing those digits. This does not repeat that: a
    message that reads exactly like contention, with a code saying otherwise, is
    not retryable."""
    assert not retry.is_retryable(
        _pg_error("XX001", "could not serialize access due to concurrent update")
    )


@pytest.mark.negative
def test_a_non_database_error_is_not_retryable() -> None:
    assert not retry.is_retryable(ValueError("could not serialize access"))


def test_with_retry_gives_up_on_a_permanent_failure() -> None:
    calls = {"n": 0}

    def always_broken():
        calls["n"] += 1
        raise _pg_error("42P01", "relation does not exist")

    with pytest.raises(psycopg.Error):
        retry.with_retry(always_broken)

    assert calls["n"] == 1, "a permanent failure must not be retried at all"


def test_with_retry_succeeds_after_transient_failures() -> None:
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _pg_error("40001", "could not serialize access")
        return "ok"

    assert retry.with_retry(flaky) == "ok"
    assert calls["n"] == 3


def test_concurrent_appends_never_collide_on_seq(database_url) -> None:
    """⭐ The case this was written for.

    `append_event` reads `MAX(seq)+1` then INSERTs. At READ COMMITTED two writers
    can read the same maximum, and the second INSERT then violates the
    (run_id, seq) primary key. `append_event` takes an advisory lock keyed on the
    run to make that impossible; this is the test that says so.

    It was written against SQLite, where the same read-then-write failed
    differently -- a DEFERRED transaction upgrading mid-block got
    SQLITE_BUSY_SNAPSHOT, which busy_timeout would not retry. The hazard survived
    the port; only its error code changed.

    Four connections, mirroring C11's one research plus three light runs, all
    appending to the same run. Every seq must be distinct and contiguous.
    """
    path = database_url
    setup = db.connect(path)
    _run(setup, "run:concurrent")
    setup.close()

    per_thread = 25
    errors: list[BaseException] = []

    def writer(worker: int) -> None:
        conn = db.connect(path)
        try:
            for i in range(per_thread):
                run_service.append_event(
                    conn,
                    run_id="run:concurrent",
                    type_="log",
                    stage="fetch",
                    data={"worker": worker, "i": i},
                    now="t1",
                )
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent appends raised: {errors[:3]}"

    check = db.connect(path)
    try:
        seqs = [
            r["seq"]
            for r in check.execute(
                "SELECT seq FROM run_events WHERE run_id = 'run:concurrent' ORDER BY seq"
            )
        ]
    finally:
        check.close()

    expected = 4 * per_thread
    assert len(seqs) == expected, f"lost writes: {len(seqs)} of {expected}"
    assert seqs == list(range(1, expected + 1)), "seq must be distinct and contiguous"


def test_the_event_log_is_the_reason_this_matters(database_url) -> None:
    """A duplicated or missing `seq` is not a cosmetic problem: the SSE stream
    is a view over this log and `Last-Event-ID` resumes by seq. A gap silently
    truncates a reconnecting client's history; a duplicate makes it replay."""
    conn = db.connect(database_url)
    try:
        _run(conn, "run:resume")
        for i in range(5):
            run_service.append_event(
                conn, run_id="run:resume", type_="log", stage=None, data={"i": i}, now="t"
            )

        resumed = run_service.events_since(conn, "run:resume", after_seq=2)
        assert [e["seq"] for e in resumed] == [3, 4, 5]
    finally:
        conn.close()
