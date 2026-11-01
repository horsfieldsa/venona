"""Migration 0018's backfill, against rows shaped like the ones it found.

The column arrived on a database with 78 runs on it, 44 of them lookups
mislabelled `succeeded_partial` for having found nothing. Leaving those NULL
would have left the history ambiguous forever, so the migration derives what it
can from `run_stages.counts` -- which is where `run_launcher` already puts each
unit of work's returned metadata.

What is tested is the SHIPPED SQL, re-executed rather than restated: the `DO`
block is lifted out of the migration file and run again. It is safe to re-run
by construction -- it only ever assigns an outcome, and every row this suite
gives it starts with none.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from venona.db import core as db

MIGRATION = (
    Path(__file__).resolve().parents[1] / "backend/venona/db/migrations/0018_run_outcome.sql"
)


def _backfill_sql() -> str:
    """The `DO $$ ... END $$;` block alone. The `ALTER TABLE` around it has
    already run on every test database, and `ADD COLUMN` is not idempotent."""
    match = re.search(r"DO \$\$.*?END \$\$;", MIGRATION.read_text(), re.S)
    assert match, "migration 0018 no longer has a DO block"
    return match.group(0)


def _run(conn: db.Conn, run_id: str, *, kind: str, counts: str | None = None, **columns) -> None:
    conn.execute(
        "INSERT INTO runs (id, kind, status, cost_usd, started_at, brief_id, counters) "
        "VALUES (?, ?, ?, 0, '2026-08-30T06:00:00Z', ?, ?)",
        (
            run_id,
            kind,
            columns.get("status", "succeeded_partial"),
            columns.get("brief_id"),
            columns.get("counters", "{}"),
        ),
    )
    if counts is not None:
        stage = "lookup" if kind in ("lookup", "lookup_all", "definitions") else kind
        conn.execute(
            "INSERT INTO run_stages (run_id, stage, status, counts, started_at) "
            "VALUES (?, ?, 'ok', ?, '2026-08-30T06:00:00Z')",
            (run_id, stage, counts),
        )


CASES = [
    # ⭐ The 44. A perfect run whose answer was no.
    ("lookup", '{"grounded": false}', {}, "nothing_found"),
    ("lookup", '{"grounded": true, "expansion": "Modular Open Systems Approach"}', {}, "delivered"),
    # The alternatives branch reports a COUNT under the same key.
    ("lookup", '{"alternatives": true, "grounded": 0}', {}, "nothing_found"),
    ("lookup", '{"alternatives": true, "grounded": 2}', {}, "delivered"),
    ("lookup_all", '{"considered": 182, "grounded": 40}', {}, "some"),
    ("lookup_all", '{"considered": 182, "grounded": 0}', {}, "nothing_found"),
    ("lookup_all", '{"considered": 182, "grounded": 182}', {}, "delivered"),
    ("lookup_all", '{"considered": 0, "grounded": 0}', {}, "nothing_to_do"),
    ("definitions", '{"considered": 10, "grounded": 3}', {}, "some"),
    ("ingest", '{"is_new": true, "analyzed": true}', {}, "delivered"),
    ("ingest", '{"is_new": false, "analyzed": false}', {}, "nothing_to_do"),
    ("questions", '{"generated": 12}', {}, "delivered"),
    ("questions", '{"generated": 0, "reason": "no_articles"}', {}, "nothing_to_do"),
    ("questions", '{"generated": 0}', {}, "nothing_found"),
    ("personal_brief", '{"brief_id": "pbrief:x"}', {}, "delivered"),
    ("personal_brief", '{"already_current": true}', {}, "nothing_to_do"),
    # Pipeline kinds answer from the run row: the brief IS the artifact.
    ("daily", None, {"brief_id": "brief:2026-08-30:x"}, "delivered"),
    ("daily", None, {}, "nothing_to_do"),
    ("rewrite", None, {"brief_id": "brief:2026-08-30:y"}, "delivered"),
    # A corroboration sweep writes no brief, so it answers from its counters.
    ("research", None, {"counters": '{"documents_new": 4}'}, "delivered"),
    ("research", None, {"counters": '{"documents_new": 0}'}, "nothing_found"),
]


@pytest.mark.parametrize(
    ("kind", "counts", "columns", "expected"),
    CASES,
    ids=[f"{k}-{e}-{i}" for i, (k, _c, _o, e) in enumerate(CASES)],
)
def test_the_backfill_reads_what_the_work_reported(conn, kind, counts, columns, expected) -> None:
    _run(conn, "run:backfill", kind=kind, counts=counts, **columns)
    conn.execute(_backfill_sql())
    row = conn.execute("SELECT outcome FROM runs WHERE id='run:backfill'").fetchone()
    assert row["outcome"] == expected


def test_a_failed_run_is_left_alone(conn) -> None:
    """It never reached an answer, and a guessed one is exactly the dishonesty
    this column replaces. The migration filters on `status LIKE 'succeeded%'`
    for that reason."""
    _run(conn, "run:failed", kind="lookup", counts='{"grounded": false}', status="failed")
    conn.execute(_backfill_sql())
    assert (
        conn.execute("SELECT outcome FROM runs WHERE id='run:failed'").fetchone()["outcome"] is None
    )


def test_a_kind_the_backfill_cannot_read_keeps_its_null(conn) -> None:
    """`domain_setup` launches WITHOUT a stage, so the drafted/failed counts its
    work returns were never written anywhere this query can see."""
    _run(conn, "run:setup", kind="domain_setup")
    conn.execute(_backfill_sql())
    assert (
        conn.execute("SELECT outcome FROM runs WHERE id='run:setup'").fetchone()["outcome"] is None
    )


def test_unparseable_counts_do_not_abort_the_migration(conn) -> None:
    """`counts` is TEXT. One row of half-written JSON must cost that row its
    outcome, not cost every later row the whole backfill -- which is why the
    block catches per row rather than casting in a WHERE clause."""
    _run(conn, "run:broken", kind="lookup", counts='{"grounded": tr')
    _run(conn, "run:fine", kind="lookup", counts='{"grounded": true}')
    conn.execute(_backfill_sql())
    rows = {
        r["id"]: r["outcome"]
        for r in conn.execute("SELECT id, outcome FROM runs WHERE id LIKE 'run:%'").fetchall()
    }
    assert rows["run:broken"] is None
    assert rows["run:fine"] == "delivered"
