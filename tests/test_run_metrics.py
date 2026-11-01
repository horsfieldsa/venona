"""Run observability: the funnel invariant, the aggregates, and the endpoint.

The thing worth testing here is NOT that a number appears. It is that the
numbers cannot quietly lie: a funnel that loses items must say so, a failed
model call must not make a run look fast, and a stage recorded before this
feature existed must still render rather than vanish.
"""

from __future__ import annotations

import pytest
from venona.pipeline.metrics import Funnel, record_rejected_shapes
from venona.services import metrics_service, run_service


class TestFunnel:
    def test_it_reconciles_when_every_item_is_accounted_for(self) -> None:
        funnel = Funnel("fetch", offered=10)
        funnel.keep(6)
        funnel.drop("duplicate", 3)
        funnel.drop("too_short", 1)
        assert funnel.reconciles()
        assert funnel.dropped == 4

    def test_it_does_not_reconcile_when_items_vanish(self) -> None:
        """⭐ The whole point. Several stages lose items to a bare `continue`;
        this is what makes that visible the moment they are instrumented."""
        funnel = Funnel("analyze", offered=10)
        funnel.keep(6)  # four went nowhere
        assert not funnel.reconciles()

    def test_a_zero_drop_is_not_recorded(self) -> None:
        """Call sites pass a computed difference (`len(before) - len(after)`)
        without guarding it, so zero must be a no-op rather than a `drop_x: 0`
        row on every run."""
        funnel = Funnel("fetch", offered=3)
        funnel.drop("capped", 0)
        funnel.keep(3)
        assert "drop_capped" not in funnel.as_metrics()

    def test_metrics_are_flat_and_prefixed(self) -> None:
        funnel = Funnel("fetch", offered=5)
        funnel.keep(4)
        funnel.drop("capped", 1)
        assert funnel.as_metrics() == {"offered": 5, "kept": 4, "drop_capped": 1}


class TestTaskLatency:
    def test_a_failed_call_does_not_drag_the_percentiles_down(self, conn) -> None:
        """A failed call is written with `duration_ms = 0` by construction.
        Counting it as a sample would make a broken run look like a fast one."""
        rows = [
            ("c1", "analyze", 40000, "ok"),
            ("c2", "analyze", 42000, "ok"),
            ("c3", "analyze", 0, "error"),
        ]
        for call_id, task, ms, status in rows:
            conn.execute(
                "INSERT INTO llm_calls (id, run_id, task, duration_ms, status, cost_usd, "
                "created_at) VALUES (?,?,?,?,?,0,'2026-01-01T00:00:00Z')",
                (call_id, "run:m", task, ms, status),
            )
        [task] = metrics_service.task_latency(conn, "run:m")
        assert task["calls"] == 3
        assert task["failed"] == 1
        assert task["p50_ms"] >= 40000, "the failed 0ms call must not be a sample"

    def test_p95_is_the_max_below_the_sample_floor(self, conn) -> None:
        """ "p95 of three calls" is the third-largest of three, which reads as
        precision that is not there."""
        for i in range(3):
            conn.execute(
                "INSERT INTO llm_calls (id, run_id, task, duration_ms, status, cost_usd, "
                "created_at) VALUES (?,?,?,?,'ok',0,'2026-01-01T00:00:00Z')",
                (f"c{i}", "run:m", "analyze", 1000 * (i + 1)),
            )
        [task] = metrics_service.task_latency(conn, "run:m")
        assert task["p95_ms"] == task["max_ms"] == 3000


class TestStages:
    def test_a_stage_with_no_metrics_still_reports_a_duration(self, conn) -> None:
        """Runs that predate this feature have `run_stages` rows and no
        `run_metrics`. They must render, not vanish."""
        conn.execute(
            "INSERT INTO run_stages (run_id, stage, status, counts, started_at, finished_at) "
            "VALUES ('run:m','fetch','ok','{}','2026-01-01T00:00:00Z','2026-01-01T00:00:30Z')"
        )
        [stage] = metrics_service.stage_metrics(conn, "run:m")
        assert stage["duration_ms"] == 30_000
        assert stage["metrics"] == {}

    def test_a_stage_with_metrics_but_no_row_is_still_reported(self, conn) -> None:
        """The graph projection emits an event and writes no `run_stages` row.
        Dropping its numbers because of that would hide the projection cost."""
        conn.execute(
            "INSERT INTO run_metrics (run_id, stage, key, value, unit, at) "
            "VALUES ('run:m','persist','edges',42,'count','2026-01-01T00:00:00Z')"
        )
        [stage] = metrics_service.stage_metrics(conn, "run:m")
        assert stage["stage"] == "persist"
        assert stage["metrics"]["edges"] == 42

    def test_a_failed_invariant_surfaces_as_not_reconciled(self, conn) -> None:
        conn.execute(
            "INSERT INTO run_metrics (run_id, stage, key, value, unit, at) "
            "VALUES ('run:m','analyze','reconciled',0,'ratio','2026-01-01T00:00:00Z')"
        )
        [stage] = metrics_service.stage_metrics(conn, "run:m")
        assert stage["reconciled"] is False


class TestFreshness:
    def test_undated_is_counted_apart_from_stale(self) -> None:
        """The retrieval filter is `published_at IS NULL OR >= cutoff`, so an
        undated document is KEPT. Folding it into `stale` would report a source
        as waste when it is the opposite."""
        sources = [{"fresh": 2, "stale": 6, "undated": 5}]
        stages = [{"stage": "fetch", "metrics": {"lookback_hours": 36.0}}]
        out = metrics_service.freshness(sources, stages)
        assert out["undated"] == 5
        assert out["stale_ratio"] == pytest.approx(6 / 8), "ratio is over DATED documents only"

    def test_no_dated_documents_is_not_a_division_by_zero(self) -> None:
        out = metrics_service.freshness([{"fresh": 0, "stale": 0, "undated": 3}], [])
        assert out["stale_ratio"] == 0.0


class TestRejectedShapes:
    """The feedback loop's storage: per-run shape rows, replaced per pass, and
    an editor-side aggregate filtered by what the CURRENT ontology refuses."""

    #: Ill-typed under the live ontology (USES_TECH domain is [Program,
    #: Platform]); the legal control below is the same predicate used rightly.
    SHAPE = ("USES_TECH", "Company", "Technology")
    LEGAL = ("USES_TECH", "Program", "Technology")

    @staticmethod
    def _record(conn, run_id: str, key: tuple[str, str, str], n: int, at: str, subject: str):
        record_rejected_shapes(
            conn,
            run_id=run_id,
            shapes={
                key: {
                    "n": n,
                    "example_subject": subject,
                    "example_object": "transformers",
                    "example_article_id": "doc:t",
                    "example_evidence": "quote",
                }
            },
            at=at,
        )

    def test_the_aggregate_groups_across_runs_and_keeps_the_newest_example(self, conn) -> None:
        self._record(conn, "run:1", self.SHAPE, 3, "2026-01-01T00:00:00Z", "OldCo")
        self._record(conn, "run:2", self.SHAPE, 4, "2026-01-02T00:00:00Z", "NewCo")
        [shape] = metrics_service.ontology_rejections(conn)
        assert shape["n"] == 7
        assert shape["runs"] == 2
        assert shape["example_subject"] == "NewCo"
        assert shape["last_run_id"] == "run:2"
        assert "USES_TECH" in shape["problem"], "the refusal prose is recomputed live"

    def test_a_shape_the_current_ontology_accepts_is_filtered_out(self, conn) -> None:
        """⭐ The loop closing: widening a contract dissolves its evidence."""
        self._record(conn, "run:1", self.LEGAL, 5, "2026-01-01T00:00:00Z", "NGC2")
        assert metrics_service.ontology_rejections(conn) == []

    def test_a_reentered_pass_replaces_rather_than_accumulates(self, conn) -> None:
        """`venona run --only analyze` re-enters; a shape recorded before the
        ontology was widened must not linger on the run under upsert alone."""
        self._record(conn, "run:1", self.SHAPE, 3, "2026-01-01T00:00:00Z", "OpenAI")
        record_rejected_shapes(conn, run_id="run:1", shapes={}, at="2026-01-02T00:00:00Z")
        assert metrics_service.rejected_shapes(conn, "run:1") == []


async def test_the_endpoint_serves_a_run(api_client, api_db) -> None:
    api_db.execute(
        "INSERT INTO runs (id, kind, status, counters, started_at) "
        "VALUES ('run:m','on_demand','succeeded','{\"llm_calls\": 4}','2026-01-01T00:00:00Z')"
    )
    api_db.commit()
    response = await api_client.get("/api/runs/run:m/metrics")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == "run:m"
    # Reported alongside the ledger count rather than reconciled with it:
    # `_record_failed_call` does not bump the counter and a PermissionDenied
    # call writes no ledger row, so the two legitimately disagree.
    assert body["calls_counted"] == 4
    assert body["calls_ledgered"] == 0


async def test_the_endpoint_404s_for_an_unknown_run(api_client) -> None:
    assert (await api_client.get("/api/runs/run:nope/metrics")).status_code == 404


async def test_the_ontology_rejections_endpoint(api_client, api_db) -> None:
    """The editor-side read: grouped shapes, still-refused only, live prose."""
    api_db.execute(
        "INSERT INTO run_rejected_shapes (run_id, predicate, subject_type, object_type, "
        "n, example_subject, example_object, example_article_id, example_evidence, at) "
        "VALUES ('run:m','USES_TECH','Company','Technology',4,'OpenAI','transformers',"
        "'doc:t','q','2026-01-01T00:00:00Z')"
    )
    api_db.commit()
    response = await api_client.get("/api/config/ontology/rejections")
    assert response.status_code == 200, response.text
    [shape] = response.json()["shapes"]
    assert shape["n"] == 4
    assert shape["runs"] == 1
    assert shape["last_run_id"] == "run:m"
    assert "USES_TECH" in shape["problem"]


class TestOpenStages:
    """Writing the `run_stages` row at START is what makes a live view possible.
    Its cost is that a run which never reaches the end of a stage leaves that
    row saying `running` with nothing to update it."""

    def test_a_cancelled_run_does_not_leave_a_stage_running(self, conn) -> None:
        """⭐ Seen on screen: a `cancelled` run whose page showed `fetch` still
        running, indefinitely."""
        conn.execute(
            "INSERT INTO runs (id, kind, status, counters, started_at) "
            "VALUES ('run:c','on_demand','running','{}','2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO run_stages (run_id, stage, status, counts, started_at) "
            "VALUES ('run:c','fetch','running','{}','2026-01-01T00:00:00Z')"
        )
        run_service.finish_run(
            conn,
            run_id="run:c",
            status="cancelled",
            counters={},
            warnings=[],
            brief_id=None,
            cost_usd=0.0,
            error=None,
            now="2026-01-01T00:05:00Z",
        )
        row = conn.execute("SELECT * FROM run_stages WHERE run_id='run:c'").fetchone()
        assert row["status"] == "interrupted"
        assert row["finished_at"] == "2026-01-01T00:05:00Z"

    def test_a_completed_stage_is_left_alone(self, conn) -> None:
        """It must only touch `running`. Rewriting a finished stage would lose
        the counts the run actually produced."""
        conn.execute(
            "INSERT INTO runs (id, kind, status, counters, started_at) "
            "VALUES ('run:c','on_demand','running','{}','2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO run_stages (run_id, stage, status, counts, started_at, finished_at) "
            "VALUES ('run:c','fetch','ok','{\"documents_new\": 4}','2026-01-01T00:00:00Z',"
            "'2026-01-01T00:01:00Z')"
        )
        run_service.finish_run(
            conn,
            run_id="run:c",
            status="succeeded",
            counters={},
            warnings=[],
            brief_id=None,
            cost_usd=0.0,
            error=None,
            now="2026-01-01T00:05:00Z",
        )
        row = conn.execute("SELECT * FROM run_stages WHERE run_id='run:c'").fetchone()
        assert row["status"] == "ok"
        assert row["finished_at"] == "2026-01-01T00:01:00Z"
