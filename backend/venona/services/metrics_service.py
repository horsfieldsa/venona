"""Read side of run observability. Aggregation only -- nothing here writes.

Everything is derived from four tables the pipeline already fills:
`run_stages`, `run_metrics`, `run_source_stats` and `llm_calls`. The last is the
interesting one: it has carried `duration_ms`, the four token columns and cost
per task since it shipped, indexed by `run_id`, and no endpoint has ever read
it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from venona.db import core as db
from venona.db.repositories import metrics as repo

# Enough samples that a p95 means something. Below this the ledger reports the
# max instead, because "p95 of three calls" is the third-largest of three and
# reads as precision that is not there.
P95_MIN_SAMPLES = 5


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def elapsed_ms(start: str | None, end: str | None) -> int:
    a, b = _parse(start), _parse(end)
    if a is None or b is None:
        return 0
    return max(0, int((b - a).total_seconds() * 1000))


def _percentile(sorted_values: list[int], q: float) -> int:
    if not sorted_values:
        return 0
    index = min(len(sorted_values) - 1, int(len(sorted_values) * q))
    return sorted_values[index]


def stage_metrics(conn: db.Conn, run_id: str) -> list[dict[str, Any]]:
    """One row per stage, in execution order, with its funnel attached."""
    by_stage: dict[str, dict[str, float]] = {}
    for row in repo.select_run_metrics_by_run_id(conn, run_id):
        by_stage.setdefault(row["stage"], {})[row["key"]] = row["value"]

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in repo.select_run_stages_by_run_id(conn, run_id):
        stage = row["stage"]
        seen.add(stage)
        metrics = by_stage.get(stage, {})
        out.append(
            {
                "stage": stage,
                "status": row["status"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                # Prefer the stage's own measurement; fall back to subtracting
                # the timestamps for stages recorded before this shipped.
                "duration_ms": int(metrics.get("duration_ms") or 0)
                or elapsed_ms(row["started_at"], row["finished_at"]),
                "metrics": metrics,
                "reconciled": metrics.get("reconciled", 1.0) >= 1.0,
            }
        )

    # A stage that recorded metrics but has no `run_stages` row -- the graph
    # projection does exactly this. Better to show it than to drop the numbers.
    for stage, metrics in by_stage.items():
        if stage not in seen:
            out.append(
                {
                    "stage": stage,
                    "status": None,
                    "started_at": None,
                    "finished_at": None,
                    "duration_ms": int(metrics.get("duration_ms") or 0),
                    "metrics": metrics,
                    "reconciled": metrics.get("reconciled", 1.0) >= 1.0,
                }
            )
    return out


def source_stats(conn: db.Conn, run_id: str) -> list[dict[str, Any]]:
    """Per-feed funnel, worst first: most stale, then most duplicated. The order
    is the point -- this table exists to be read top-down when deciding which
    source to drop."""
    rows = [dict(r) for r in repo.select_run_source_stats_by_run_id(conn, run_id)]
    rows.sort(key=lambda r: (-(r.get("stale") or 0), -(r.get("duplicate") or 0)))
    return rows


def task_latency(conn: db.Conn, run_id: str) -> list[dict[str, Any]]:
    """Latency and cost per model task, from the ledger."""
    by_task: dict[str, dict[str, Any]] = {}
    for row in repo.select_llm_calls_by_run_id(conn, run_id):
        bucket = by_task.setdefault(
            row["task"],
            {
                "task": row["task"],
                "stage": row["stage"],
                "durations": [],
                "cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read": 0,
                "failed": 0,
            },
        )
        # A failed call has `duration_ms = 0` by construction, so including it
        # would drag every percentile toward zero and make a broken run look
        # fast. Counted separately instead.
        if row["status"] == "ok":
            bucket["durations"].append(int(row["duration_ms"] or 0))
        else:
            bucket["failed"] += 1
        bucket["cost_usd"] += row["cost_usd"] or 0.0
        bucket["input_tokens"] += row["input_tokens"] or 0
        bucket["output_tokens"] += row["output_tokens"] or 0
        bucket["cache_read"] += row["cache_read_input_tokens"] or 0
        bucket["stage"] = bucket["stage"] or row["stage"]

    out: list[dict[str, Any]] = []
    for bucket in by_task.values():
        durations = sorted(bucket["durations"])
        cached = bucket["cache_read"]
        fresh_in = bucket["input_tokens"]
        out.append(
            {
                "task": bucket["task"],
                "stage": bucket["stage"],
                "calls": len(durations) + bucket["failed"],
                "p50_ms": _percentile(durations, 0.5),
                "p95_ms": _percentile(durations, 0.95)
                if len(durations) >= P95_MIN_SAMPLES
                else (durations[-1] if durations else 0),
                "max_ms": durations[-1] if durations else 0,
                "total_ms": sum(durations),
                "input_tokens": fresh_in,
                "output_tokens": bucket["output_tokens"],
                "cache_hit_ratio": (cached / (cached + fresh_in)) if (cached + fresh_in) else 0.0,
                "cost_usd": round(bucket["cost_usd"], 4),
                "failed": bucket["failed"],
            }
        )
    out.sort(key=lambda t: -t["total_ms"])
    return out


def slowest_subjects(conn: db.Conn, run_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """The individual clusters/entities that cost the most wall-clock.

    Only possible because `llm_calls.subject` records what a call was about;
    rows written before that migration have `subject IS NULL` and are excluded
    rather than shown as an empty name.
    """
    return [dict(r) for r in repo.select_llm_calls_by_run_id_2(conn, run_id, limit)]


def freshness(sources: list[dict[str, Any]], stages: list[dict[str, Any]]) -> dict[str, Any]:
    fresh = sum(s.get("fresh") or 0 for s in sources)
    stale = sum(s.get("stale") or 0 for s in sources)
    undated = sum(s.get("undated") or 0 for s in sources)
    lookback = 0.0
    for stage in stages:
        if stage["stage"] == "fetch":
            lookback = float(stage["metrics"].get("lookback_hours") or 0.0)
    dated = fresh + stale
    return {
        "lookback_hours": lookback,
        "fresh": fresh,
        "stale": stale,
        "undated": undated,
        "stale_ratio": (stale / dated) if dated else 0.0,
    }


def rejected_shapes(conn: db.Conn, run_id: str) -> list[dict[str, Any]]:
    """The shapes the ontology refused in this run, most-asserted first."""
    return [dict(r) for r in repo.select_rejected_shapes_by_run_id(conn, run_id)]


def ontology_rejections(conn: db.Conn) -> list[dict[str, Any]]:
    """Every stored rejected shape the CURRENT ontology still refuses.

    "Recent" is defined by the ontology, not a time window: each shape is
    re-checked against `get_ontology()` at read time, so widening a contract
    dissolves its shapes from the panel on the next fetch -- which is the
    feedback the loop exists to close. The refusal prose is recomputed for the
    same reason; a stored reason goes stale the moment the ontology is edited.
    """
    from venona.ontology import get_ontology

    # Read at the point of use, never bound at import: the ontology is editable
    # over HTTP and a run executes in this process, so a snapshot would judge
    # today's rejections against yesterday's contract.
    ontology = get_ontology()

    out = []
    for row in repo.select_rejected_shapes(conn):
        problem = ontology.check_relation(row["predicate"], row["subject_type"], row["object_type"])
        if not problem:
            continue  # the contract was widened; the evidence has done its job
        out.append(
            {
                "predicate": row["predicate"],
                "subject_type": row["subject_type"],
                "object_type": row["object_type"],
                "n": int(row["n"]),
                "runs": int(row["runs"]),
                "example_subject": row["example_subject"],
                "example_object": row["example_object"],
                "last_run_id": row["run_id"],
                "last_seen_at": row["at"],
                "problem": problem,
            }
        )
    out.sort(key=lambda s: -s["n"])
    return out[:50]


def build(conn: db.Conn, run: dict[str, Any]) -> dict[str, Any]:
    """Assemble the whole payload for one run."""
    run_id = run["id"]
    stages = stage_metrics(conn, run_id)
    sources = source_stats(conn, run_id)
    tasks = task_latency(conn, run_id)

    # Queue wait: asked for, versus actually started. `mark_running` deliberately
    # does not rewrite `started_at` so that this stays measurable.
    first_start = min((s["started_at"] for s in stages if s["started_at"]), default=None)
    counters = run.get("counters") or {}

    by_stage = {s["stage"]: s["metrics"] for s in stages}
    fetch = by_stage.get("fetch", {})
    analyze = by_stage.get("analyze", {})

    funnel: list[dict[str, float | str]] = []
    if fetch:
        funnel.append({"step": "offered", "value": fetch.get("offered", 0)})
        funnel.append({"step": "documents", "value": fetch.get("kept", 0)})
        funnel.append({"step": "clusters", "value": fetch.get("clusters", 0)})
    if analyze:
        funnel.append({"step": "analyzed", "value": analyze.get("kept", 0)})

    analyzed = analyze.get("kept", 0)
    ledgered = repo.select_llm_calls_by_run_id_3(conn, run_id).fetchone()["n"]

    return {
        "run_id": run_id,
        "status": run["status"],
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "duration_ms": elapsed_ms(run.get("started_at"), run.get("finished_at")),
        "queue_ms": elapsed_ms(run.get("started_at"), first_start),
        "stages": stages,
        "sources": sources,
        "tasks": tasks,
        "slowest": slowest_subjects(conn, run_id),
        "freshness": freshness(sources, stages),
        "funnel": funnel,
        "rejected_shapes": rejected_shapes(conn, run_id),
        # Calls per analysed cluster. The divisor is `analyzed` and always was:
        # this read `counters["brief_items"]` first, which is not one of the
        # thirteen frozen counters (C6) and is therefore never present, so the
        # branch could not be reached. Named for the item a run keeps, which on
        # every kind that reaches here is the cluster it analysed.
        "cost_per_kept_item": round(ledgered / analyzed, 2) if analyzed else 0.0,
        "calls_ledgered": ledgered,
        "calls_counted": int(counters.get("llm_calls") or 0),
    }
