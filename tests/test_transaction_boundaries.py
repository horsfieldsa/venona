"""What a transaction on this database does and does not survive.

Three failures live here, and all three are silent. A nested `BEGIN` is
ignored with a warning, so the inner block's `COMMIT` ends the outer
transaction and everything after it commits one statement at a time -- every
call succeeds and atomicity is simply gone. A failed statement aborts the
whole transaction, so `except IntegrityError: continue` does not skip a row,
it poisons every statement that follows. The third -- `executemany` committing
as it goes under autocommit -- turned out not to happen on this driver, and the
test that expected it stays as the thing that would notice if it started.
"""

from __future__ import annotations

import psycopg
import pytest
from venona.db import core as db


def _rows(conn: db.Conn, table: str = "run_events") -> int:
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def _make_run(conn: db.Conn, run_id: str) -> None:
    db.insert(
        conn,
        "runs",
        {
            "id": run_id,
            "kind": "on_demand",
            "status": "running",
            "params": "{}",
            "profile_hash": "h",
            "started_at": "t0",
        },
    )


def _event(conn: db.Conn, run_id: str, seq: int) -> None:
    db.insert(
        conn,
        "run_events",
        {
            "run_id": run_id,
            "seq": seq,
            "ts": "t1",
            "type": "log",
            "stage": "fetch",
            "data": "{}",
        },
    )


# ── nesting ──────────────────────────────────────────────────────────


def test_a_nested_transaction_is_refused(conn: db.Conn) -> None:
    """⭐ The one that looks like it works.

    Without the guard the inner `COMMIT` ends the OUTER transaction, so the
    outer block's remaining writes land under autocommit and a later rollback
    keeps them. Nothing raises, so the only symptom is data that survived a
    failure it should not have.
    """
    with pytest.raises(RuntimeError, match="already open"), db.transaction(conn):
        with db.transaction(conn):
            pass


def test_the_guard_does_not_leave_the_connection_in_a_transaction(conn: db.Conn) -> None:
    """A refusal must not cost the caller its connection."""
    with pytest.raises(RuntimeError), db.transaction(conn):
        with db.transaction(conn):
            pass
    assert not conn.in_transaction
    with db.transaction(conn):
        pass


# ── savepoints ───────────────────────────────────────────────────────


def test_a_failed_statement_poisons_the_rest_of_the_transaction(conn: db.Conn) -> None:
    """The behaviour `db.savepoint` exists for, pinned so it stays understood.

    This is Postgres, not a venona rule: after a constraint violation every
    following statement in the same transaction raises `InFailedSqlTransaction`
    until a rollback.
    """
    _make_run(conn, "run:poison")
    with pytest.raises(psycopg.errors.InFailedSqlTransaction), db.transaction(conn):
        _event(conn, "run:poison", 1)
        with pytest.raises(psycopg.IntegrityError):
            _event(conn, "run:poison", 1)
        _event(conn, "run:poison", 2)


def test_a_savepoint_lets_one_statement_fail_without_taking_the_rest(conn: db.Conn) -> None:
    """⭐ The seed loader's case: skip the colliding row, keep the load."""
    _make_run(conn, "run:sp")
    with db.transaction(conn):
        _event(conn, "run:sp", 1)
        with pytest.raises(psycopg.IntegrityError), db.savepoint(conn, "one"):
            _event(conn, "run:sp", 1)
        _event(conn, "run:sp", 2)

    assert _rows(conn) == 2, "the surviving statements must both have committed"


def test_a_savepoint_outside_a_transaction_is_a_no_op(conn: db.Conn) -> None:
    """A caller should not have to know which it is in."""
    _make_run(conn, "run:sp-bare")
    with db.savepoint(conn):
        _event(conn, "run:sp-bare", 1)
    assert _rows(conn) == 1


def test_a_savepoint_name_must_be_an_identifier(conn: db.Conn) -> None:
    """It is interpolated -- an identifier cannot be a bound parameter."""
    _make_run(conn, "run:sp-name")
    with pytest.raises(ValueError, match="identifier"), db.transaction(conn):
        with db.savepoint(conn, "one; DROP TABLE runs"):
            pass


# ── executemany ──────────────────────────────────────────────────────


def test_executemany_is_all_or_nothing(conn: db.Conn) -> None:
    """A CHARACTERISATION TEST, not a regression test.

    The review expected a partial write here -- a violation part way through
    leaving the earlier rows committed under autocommit, which at the call site
    is indistinguishable from a batch that never ran. psycopg 3.3.4 does not
    behave that way: the batch is one operation and none of it lands. So there
    is nothing to fix, and this is here to make a driver upgrade that changes
    the answer fail loudly instead of writing half a batch.
    """
    _make_run(conn, "run:batch")
    _event(conn, "run:batch", 5)

    rows = [("run:batch", seq, "t1", "log", "fetch", "{}") for seq in (1, 2, 5)]
    with pytest.raises(psycopg.IntegrityError):
        conn.executemany(
            "INSERT INTO run_events (run_id, seq, ts, type, stage, data) VALUES (?,?,?,?,?,?)",
            rows,
        )

    assert _rows(conn) == 1, "seq 1 and 2 must have been rolled back with seq 5"


def test_executemany_inside_a_transaction_does_not_nest(conn: db.Conn) -> None:
    """The caller's transaction already provides atomicity; a second BEGIN here
    would end it."""
    _make_run(conn, "run:batch-inner")
    with db.transaction(conn):
        conn.executemany(
            "INSERT INTO run_events (run_id, seq, ts, type, stage, data) VALUES (?,?,?,?,?,?)",
            [("run:batch-inner", seq, "t1", "log", "fetch", "{}") for seq in (1, 2)],
        )
        assert conn.in_transaction, "the caller's transaction must still be open"
    assert _rows(conn) == 2


# ── upsert accumulate ────────────────────────────────────────────────


def test_accumulate_never_lowers_a_stored_count(conn: db.Conn) -> None:
    """⭐ A count derived from a read cannot be assigned back safely: a writer
    that raised it in between would be reverted."""
    row = {
        "id": "gloss:acc",
        "surface": "ACC",
        "normalized": "acc",
        "expansion": "a thing",
        "method": "manual",
        "confidence": 0.9,
        "status": "active",
        "sighting_count": 9,
        "article_count": 4,
        "first_seen": "t0",
        "last_seen": "t0",
    }
    db.upsert(conn, "acronym_definitions", row, key=["id"])

    db.upsert(
        conn,
        "acronym_definitions",
        {**row, "sighting_count": 2, "article_count": 7},
        key=["id"],
        accumulate=("sighting_count", "article_count"),
    )

    stored = conn.execute(
        "SELECT sighting_count, article_count FROM acronym_definitions WHERE id = 'gloss:acc'"
    ).fetchone()
    assert stored["sighting_count"] == 9, "a lower offer must not win"
    assert stored["article_count"] == 7, "a higher offer must"


# ── the indexes on the forever-growing tables ────────────────────────


def test_the_hot_indexes_exist(conn: db.Conn) -> None:
    """Migrations run before this fixture, so their absence is a missed
    migration rather than a slow query nobody measures."""
    names = {
        r["indexname"]
        for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    }
    for expected in (
        "ix_llm_calls_created_at",
        "ix_assertions_active_relations",
        "ix_runs_active",
    ):
        assert expected in names, f"{expected} is missing"


def test_the_partial_index_matches_the_predicate_its_readers_use(conn: db.Conn) -> None:
    """⭐ A partial index Postgres cannot prove covers the query is an index it
    will not use, and the failure is a plan nobody looks at.

    The planner will still choose a sequential scan on a table this small, so
    the assertion is that the index is APPLICABLE -- `pg_get_expr` of the
    predicate must be the one the four readers write.
    """
    row = conn.execute(
        "SELECT pg_get_expr(i.indpred, i.indrelid) AS predicate "
        "  FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        " WHERE c.relname = 'ix_assertions_active_relations'"
    ).fetchone()
    predicate = row["predicate"]
    assert "relation" in predicate and "retracted_at IS NULL" in predicate, predicate
