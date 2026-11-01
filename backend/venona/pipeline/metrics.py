"""Stage instrumentation: a funnel, a metric sink, and a stage timer.

WHY THIS IS ONE SMALL MODULE AND NOT A FRAMEWORK.

Every pipeline stage already ends with a report dataclass, and two of them --
`AcronymReport.reconciles()` and `DefineReport.reconciles()` -- had independently
grown the same idea: assert that a population partitions, `seen == a + b + c`.
That is the right idea and it was written twice by hand, which is how it drifts:
`DefineReport.already_defined` and `defined_web` are declared and never
incremented, so that reconciliation is currently unfalsifiable.

`Funnel` is that idea once. A stage declares how many items it was offered,
names each drop, and the invariant checks itself. The payoff is that stage seven
costs nothing to instrument: build a `Funnel`, and storage, the API shape, the
UI rendering and the invariant all come for free.

WHAT DOES NOT BELONG HERE. Counters. `RunCounters` is frozen by contract C6 and
`ctx.counter` raises `KeyError` on an unknown name specifically so that new
measurements land here instead. Nothing in this module may write to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from venona.db import core as db

if TYPE_CHECKING:  # pragma: no cover - typing only
    from venona.pipeline.types import RunContext


@dataclass
class Funnel:
    """A population, and every reason a member of it did not survive.

    The invariant is the point: `offered == kept + sum(drops)`. A stage that
    silently loses items -- and several do today, with a bare `continue` -- fails
    `reconciles()` the moment it is instrumented, which is the cheapest possible
    way to find out.

    `offered` is set at construction rather than accumulated, because every real
    drop site in this pipeline knows its population up front (a source's item
    list, a `LIMIT`ed query, a list of entity ids). A funnel that counted its own
    input would not catch the two cases that matter most -- `fetch.py`'s
    `items[: cap]` and `analyze.py`'s SQL `LIMIT` -- where the truncation happens
    before anything is iterated.
    """

    stage: str
    offered: int = 0
    kept: int = 0
    drops: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, n: int = 1) -> None:
        """Record `n` items lost for `reason`. Zero is ignored, so a call site
        may pass a computed difference without guarding it."""
        if n:
            self.drops[reason] = self.drops.get(reason, 0) + n

    def keep(self, n: int = 1) -> None:
        self.kept += n

    @property
    def dropped(self) -> int:
        return sum(self.drops.values())

    def reconciles(self) -> bool:
        return self.offered == self.kept + self.dropped

    def as_metrics(self) -> dict[str, float]:
        """Flat key/value rows. `drop_*` is prefixed so the UI can separate the
        chain (offered/kept) from its reasons without a schema."""
        out: dict[str, float] = {"offered": self.offered, "kept": self.kept}
        for reason, n in self.drops.items():
            out[f"drop_{reason}"] = n
        return out

    def emit(self, ctx: RunContext) -> None:
        """Write every row, and record the invariant as data rather than as an
        assertion.

        A failing invariant must NOT raise: this is measurement, and a run that
        died because its instrumentation disagreed with itself would be the
        worst possible trade. `reconciled=0` is visible in the UI and in tests,
        which is enough -- `tests/test_run_metrics.py` asserts it holds for the
        stages that claim it.
        """
        for key, value in self.as_metrics().items():
            ctx.metric(self.stage, key, value)
        ctx.metric(self.stage, "reconciled", 1.0 if self.reconciles() else 0.0, unit="ratio")


def record_metric(
    conn: db.Conn,
    *,
    run_id: str,
    stage: str,
    key: str,
    value: float,
    unit: str | None,
    at: str,
) -> None:
    """UPSERT one metric row.

    Upsert rather than insert because a stage may record incrementally while it
    runs -- which is what makes the live view possible -- and because
    `venona run --only analyze` re-enters a stage whose rows already exist.
    """
    db.upsert(
        conn,
        "run_metrics",
        {
            "run_id": run_id,
            "stage": stage,
            "key": key,
            "value": float(value),
            "unit": unit,
            "at": at,
        },
        key=["run_id", "stage", "key"],
    )


def record_source_stats(conn: db.Conn, *, run_id: str, source_id: str, **fields: Any) -> None:
    """UPSERT one per-source row. Called once per source per run."""
    db.upsert(
        conn,
        "run_source_stats",
        {"run_id": run_id, "source_id": source_id, **fields},
        key=["run_id", "source_id"],
    )


def record_rejected_shapes(
    conn: db.Conn,
    *,
    run_id: str,
    shapes: dict[tuple[str, str, str], dict[str, Any]],
    at: str,
) -> None:
    """Replace the run's ontology-rejection aggregate with this pass's."""
    from venona.db.repositories import metrics as repo

    repo.delete_rejected_shapes_by_run_id(conn, run_id)
    for (predicate, subject_type, object_type), shape in shapes.items():
        db.upsert(
            conn,
            "run_rejected_shapes",
            {
                "run_id": run_id,
                "predicate": predicate,
                "subject_type": subject_type,
                "object_type": object_type,
                "n": shape["n"],
                "example_subject": shape["example_subject"],
                "example_object": shape["example_object"],
                "example_article_id": shape.get("example_article_id"),
                "example_evidence": shape.get("example_evidence"),
                "at": at,
            },
            key=["run_id", "predicate", "subject_type", "object_type"],
        )
