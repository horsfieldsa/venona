"""One stage, opened and closed the same way by every run.

THE SEQUENCE IS THE POINT: mark the run, write a `running` row, announce it,
check for a cancel, then a terminal row and announce that. Four lifecycles had
grown their own copy and they did not agree -- one wrote the terminal row and
not the running one, so a live run's Steps table read "No step records" about
work happening in front of the reader; another named whichever stage the emitter
knew rather than the one open, so a sweep reported two stages running at once.
Each was right about itself, which is what made the divergence hard to see.

WHY `services/`: `run_service` imports `pipeline.types` and not the reverse, so
a scope under `pipeline/` would close the cycle the moment it needed
`record_stage`. It declares NO stage list -- `RunType.stages` is what a kind
emits, `orchestrator.STAGES` is what `--only` accepts, and a third would be the
one that drifts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from venona.db import core as db
from venona.services import run_service


@dataclass
class StageReport:
    """What a stage says about itself, filled in by its own body.

    Only the DIFFERENCES live here; the sequence lives in `RunScope.stage`.
    Defaults describe the common stage: it worked, it committed, and what it
    counted is what the client is told.
    """

    #: The stage's own numbers, stored on the `run_stages` row.
    counts: dict[str, Any] = field(default_factory=dict)
    #: What `stage_finished` carries, when that differs from `counts`.
    payload: dict[str, Any] | None = None
    status: str = "ok"
    error: str | None = None
    #: Whether this stage's work is committed to Postgres. False keeps the stage
    #: out of `completed`, so a later failure does not claim it.
    committed: bool = True


@dataclass
class RunScope:
    """The lifecycle of one run, however many stages it has.

    A single-stage run is the degenerate case, not a different shape.
    """

    run_id: str
    conn: db.Conn
    ctx: Any
    #: `(type, stage, data) -> None`, already durable-then-broadcast.
    emit: Callable[[str, str | None, dict[str, Any]], Awaitable[None]]
    #: Stages whose work reached Postgres. A failure after any of these makes
    #: the run `succeeded_partial`: a partially useful run is never a failure.
    completed: list[str] = field(default_factory=list)
    #: The stage currently open and when, so a caller that catches an exception
    #: can settle the row `stage()` left behind -- it catches nothing itself.
    _open: tuple[str, str] | None = None

    async def settle_failed(self, error: str | None) -> None:
        """Close the open stage as `error`, for a caller that caught the raise.

        Left alone the row is settled `interrupted` by `_settle_open_stages` --
        right for a process that died, wrong for work that raised and was
        caught. A caller that knows which happened says so.
        """
        if self._open is None:
            return
        name, started = self._open
        run_service.record_stage(
            self.conn,
            run_id=self.run_id,
            stage=name,
            status="error",
            error=error,
            started_at=started,
            finished_at=self.ctx.clock.now(),
        )
        self._open = None

    def check_cancel(self) -> None:
        self.ctx.check_cancel()

    async def open_stage(self, name: str) -> str:
        """Open a stage and return when it opened.

        The explicit half of `stage()`, for a body that cannot be a `with`
        block. `close_stage` is its partner, and one without the other is the
        half-a-pair this module exists to stop.
        """
        ctx = self.ctx
        started = ctx.clock.now()
        # THE ONE SOURCE for which stage a piece of work belonged to. The event
        # log, `run_metrics` and the `llm_calls` ledger all read it, so none of
        # them can be handed a name that disagrees with the others.
        ctx.stage = name
        self._open = (name, started)
        run_service.set_stage(self.conn, self.run_id, name)
        run_service.record_stage(
            self.conn, run_id=self.run_id, stage=name, status="running", started_at=started
        )
        await self.emit("stage_started", name, {})
        self.check_cancel()
        return started

    async def close_stage(self, name: str, started: str, report: StageReport) -> None:
        """Settle a stage opened by `open_stage`."""
        ctx = self.ctx
        ctx.stage = None
        self._open = None
        run_service.record_stage(
            self.conn,
            run_id=self.run_id,
            stage=name,
            status=report.status,
            counts=report.counts,
            error=report.error,
            started_at=started,
            finished_at=ctx.clock.now(),
        )
        await self.emit(
            "stage_finished", name, report.counts if report.payload is None else report.payload
        )
        # A COUNTER SNAPSHOT PER STAGE. The whole of `RunCounters`, so it is
        # idempotent; skipping it left a launcher run's numbers arriving in its
        # final second and the wizard's never arriving at all.
        await self.emit("counter", None, ctx.counters.as_dict())
        if report.committed:
            self.completed.append(name)

    @asynccontextmanager
    async def stage(self, name: str) -> AsyncIterator[StageReport]:
        """Run one stage's body inside the bookkeeping every stage shares.

        NOTHING IS CAUGHT HERE. An exception leaves the `running` row and skips
        `completed`, which is what the terminal handler reads: a stage that
        threw did not commit. Catching would make every crash look partial.
        """
        started = await self.open_stage(name)
        report = StageReport()
        try:
            yield report
        finally:
            # Cleared however the stage leaves, including by raising. An event
            # emitted between stages belongs to no stage, and attaching it to
            # the one that just finished would light a finished segment.
            self.ctx.stage = None

        await self.close_stage(name, started, report)
