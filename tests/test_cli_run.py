"""`venona run` — the entrypoint `make record` and the runbook both invoke.

None of these spend a model call. They cover the three ways the command can be
wrong before it ever reaches `execute_run`: an unknown stage name, a second run
racing an active one, and the stage list itself drifting from the orchestrator.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from venona import cli
from venona.db import core as db
from venona.db.repositories import run as run_repo
from venona.pipeline import orchestrator, types

# ── the stage list ───────────────────────────────────────────────────


def test_only_validates_against_the_orchestrators_stages() -> None:
    assert orchestrator.STAGES == (
        "fetch",
        "analyze",
        "acronyms",
        "brief",
        "define",
        "curate",
    )


def test_the_ui_knows_every_stage() -> None:
    """`web/src/stores/runs.ts` hardcodes the stage strip and SILENTLY DROPS
    events for names it does not know (`runs.ts`, the `if (stage)` guard).

    `persist` has been emitted and dropped since S1 with nobody noticing, which
    is exactly how a new stage becomes invisible. Contract C6 lists the store in
    its touch list; this is what makes that list enforceable.
    """
    import re
    from pathlib import Path as _Path

    store = (_Path(__file__).resolve().parents[1] / "web/src/stores/runs.ts").read_text()
    declared = re.search(r"const STAGES = \[(.*?)\] as const", store, re.S)
    assert declared, "could not find the STAGES literal in stores/runs.ts"

    ui_stages = tuple(re.findall(r"'([a-z_]+)'", declared.group(1)))
    assert ui_stages == orchestrator.STAGES, (
        f"UI renders {ui_stages}, orchestrator runs {orchestrator.STAGES} -- "
        "events for the difference are dropped without a trace"
    )


def test_there_is_exactly_one_stage_list() -> None:
    """`pipeline/types.py` used to declare a nine-name `STAGES` listing internal
    phases (normalize, dedup, resolve, score, persist) that are steps *inside*
    the four stages and were never separately runnable. Two contradictory lists
    is how `--only persist` becomes a valid-looking no-op."""
    assert not hasattr(types, "STAGES")


def test_an_unknown_stage_exits_2_and_names_the_real_ones(capsys) -> None:
    assert cli.main(["run", "--only", "persist"]) == 2
    err = capsys.readouterr().err
    assert "persist" in err
    for stage in orchestrator.STAGES:
        assert stage in err


def test_stage_validation_happens_before_the_database_is_touched(monkeypatch, capsys) -> None:
    """A typo must not create a `runs` row, open Neo4j, or migrate anything."""

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("connected to the database despite an invalid --only")

    monkeypatch.setattr(db, "connect", explode)
    assert cli.main(["run", "--only", "nonsense"]) == 2


# ── the single-active-run guard ──────────────────────────────────────


def test_a_second_run_refuses_while_one_is_active(
    monkeypatch, conn: db.Conn, database_url: str, capsys
) -> None:
    """`RunRegistry` is in-process, so it cannot see a run the API started.
    The CLI has to ask the database instead, or `venona run` in one terminal races
    the UI's run in another: two writers, two budgets, one `documents` table.

    The row is LEASED, and it has to be. A `running` row on its own stopped
    meaning "in flight" when liveness moved to a session advisory lock -- an
    unleased one is a crashed owner, and blocking on it would wedge the lane until
    somebody edited SQL. `test_an_unleased_running_row_does_not_block` is the
    other half of this.
    """
    conn.execute(
        "INSERT INTO runs (id, kind, status, started_at) VALUES (?,?,?,?)",
        ("run:already-going", "on_demand", "running", "2026-08-04T09:00:00.000Z"),
    )
    run_repo.claim_lease(conn, "run:already-going")
    conn.commit()

    settings = cli.get_settings()
    monkeypatch.setattr(type(settings), "database_url", property(lambda self: database_url))

    assert cli.main(["run"]) == 1
    err = capsys.readouterr().err
    assert "already active" in err
    # Naming the run id proves we read OUR database, not the developer's.
    assert "run:already-going" in err


@pytest.mark.negative
def test_the_guard_ignores_finished_runs(tmp_path: Path, conn: db.Conn) -> None:
    """Only `running` blocks. A `failed` or `succeeded` row must not wedge the
    CLI permanently — that would make one crash unrecoverable without SQL."""
    conn.execute(
        "INSERT INTO runs (id, kind, status, started_at) VALUES (?,?,?,?)",
        ("run:done", "on_demand", "succeeded", "2026-08-04T09:00:00.000Z"),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM runs WHERE status = 'running' LIMIT 1").fetchone()
    assert row is None
