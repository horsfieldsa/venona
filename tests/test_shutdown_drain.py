"""A light-lane run finishes when the process is going down (epic AA).

Removing the api's disk bought a shutdown grace where there was none: the host
sends SIGTERM and waits `maxShutdownDelaySeconds` before SIGKILL. The app was
not using it -- measured 2026-09-14, an `on_demand` run died 87 seconds into
shutdown with 213 of the platform's 300 still unspent, because uvicorn waits
for connections and the lifespan tore the loop down without asking whether
anything was executing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from venona.services import run_service, run_types

ROOT = Path(__file__).resolve().parent.parent


async def _register(reg: run_service.RunRegistry, run_id: str, lane: str, coro):
    handle = run_service.RunHandle(run_id=run_id, lane=lane, kind="lookup")
    handle.task = asyncio.create_task(coro)
    reg._handles[run_id] = handle
    reg._running.setdefault(lane, []).append(run_id)
    return handle


async def test_a_light_run_is_waited_for(conn) -> None:
    """The whole point: a run that fits inside the grace finishes instead of
    being killed halfway."""
    reg = run_service.RunRegistry()
    finished: list[str] = []

    async def work():
        await asyncio.sleep(0.05)
        finished.append("did the work")

    await _register(reg, "run:light", run_types.LIGHT, work())
    still_running = await run_service.drain_lane(reg, lane=run_types.LIGHT, seconds=5)

    assert still_running == [], "a run that had time to finish was abandoned"
    assert finished == ["did the work"]


async def test_the_budget_is_a_ceiling_not_a_promise() -> None:
    """A run too long for the grace is left to be killed and reaped, which is
    what happens to every research run and to `definitions` at its median."""
    reg = run_service.RunRegistry()

    async def forever():
        await asyncio.sleep(30)

    await _register(reg, "run:slow", run_types.LIGHT, forever())
    still_running = await run_service.drain_lane(reg, lane=run_types.LIGHT, seconds=0.05)

    assert still_running == ["run:slow"], "shutdown waited past its own budget"
    reg._handles["run:slow"].task.cancel()


async def test_nothing_in_flight_returns_at_once() -> None:
    """Every ordinary deploy takes this path, so it must not cost the grace."""
    reg = run_service.RunRegistry()
    assert await run_service.drain_lane(reg, lane=run_types.LIGHT, seconds=99) == []


async def test_the_research_lane_is_not_waited_for() -> None:
    """A research run is 25 to 48 minutes and cannot be saved by any value under
    300, so waiting on one holds the old instance up for nothing. Draining the
    light lane must not touch it."""
    reg = run_service.RunRegistry()

    async def forever():
        await asyncio.sleep(30)

    await _register(reg, "run:research", run_types.RESEARCH, forever())
    still_running = await run_service.drain_lane(reg, lane=run_types.LIGHT, seconds=0.05)

    assert still_running == [], "the light-lane drain waited on a research run"
    reg._handles["run:research"].task.cancel()
