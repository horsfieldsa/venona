"""The maintenance stage must not hold the event loop.

A run executes as a task inside the API process, so a stage that does a long
stretch of synchronous work is a stage during which the API answers nothing.

Observed in production 2026-09-13. A Name review's last model verdict landed at
20:49:54Z and the stage reported at 20:51:11Z; in between, apscheduler logged
its one-minute job `missed by 0:00:37`, the host's health check went unanswered,
and the instance was restarted. The site was down for ninety seconds as the run
finished, and memory never went above 225MB of 2GB -- nothing was overloaded,
the loop was simply not running.

Written against the LOOP rather than against a duration: what went wrong is not
that curate is slow (it is, and it will get slower as the corpus grows) but that
it was slow without yielding.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from venona.db import core as db
from venona.pipeline import curate
from venona.pipeline.types import build_context
from venona.settings import get_settings

# Long enough that a blocking call would starve the ticker unmistakably, short
# enough to keep the suite fast. The real sections ran for tens of seconds.
BLOCK_SECONDS = 0.4
TICK_SECONDS = 0.01


class _NoModel:
    """Curate's only model call is `adjudicate`, which this test does not
    exercise -- `adjudicate_conflicts` is off in the settings it runs under."""

    async def call(self, task, **kwargs):  # pragma: no cover - never reached
        raise AssertionError(f"curate made a model call: {task}")


class _NoHttp:
    async def get(self, url, **kwargs):  # pragma: no cover - never reached
        raise AssertionError("curate made a request")


async def test_curate_keeps_answering_while_it_works(conn: db.Conn, monkeypatch) -> None:
    """⭐ The stage yields between its sections, so uvicorn can still answer.

    `_classify_open` stands in for all of them: it is the one that walks the
    whole open queue with a query per row, and it is the first thing the stage
    does.
    """
    settings = get_settings()
    monkeypatch.setattr(settings.pipeline, "adjudicate_conflicts", False, raising=False)

    def blocking(*args, **kwargs):
        time.sleep(BLOCK_SECONDS)
        return {}

    monkeypatch.setattr(curate, "_classify_open", blocking)

    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(TICK_SECONDS)

    ctx = build_context(
        run_id="run:curate-yields",
        db=conn,
        llm=_NoModel(),
        settings=settings,
        http=_NoHttp(),
    )
    beat = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0)
        before = ticks
        await curate.run_curate(ctx)
        during = ticks - before
    finally:
        beat.cancel()

    # `_classify_open` alone blocks for two full sections' worth of ticks. Half
    # of what the wall clock allows is a wide margin against a loaded machine
    # and still an order of magnitude away from the zero a blocking stage gives.
    expected = (2 * BLOCK_SECONDS) / TICK_SECONDS
    assert during > expected / 2, (
        f"the loop ran {during} times while curate worked; "
        "a stage that does not yield starves everything else in the process"
    )


def test_every_blocking_section_is_offloaded() -> None:
    """Each section names `_unblocked`, which is the whole fix.

    Asserted against the source because the alternative is a timing test per
    section, and the failure this prevents is a NEW section written in the old
    shape -- which no timing test over today's sections would ever see.
    """
    import inspect

    source = inspect.getsource(curate.run_curate)
    blocking = (
        "_file_entity_duplicates(",
        "_classify_open(",
        "identical_pairs(",
        "close_moot_conflicts(",
        "promote_provisionals(",
        "model_merges(",
        "model_relations(",
        "model_distinctions(",
        "_reconcile_entity_claims(",
    )
    for call in blocking:
        assert call in source, f"{call} left run_curate; this list needs updating"
    # One `_unblocked` per section, and the sections outnumber nothing.
    assert source.count("await _unblocked(") >= 8

    # And nothing calls the heaviest of them straight off the loop.
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("await ", "def ", "#", '"')):
            continue
        assert not stripped.startswith("_classify_open("), (
            "a bare `_classify_open(...)` call blocks the event loop; "
            "wrap it in `await _unblocked(...)`"
        )


@pytest.mark.parametrize("name", ["_unblocked"])
def test_the_offload_helper_says_why(name: str) -> None:
    """The reason is the whole value of the helper: without it, the next person
    reading `asyncio.to_thread` around a database call sees ceremony."""
    doc = getattr(curate, name).__doc__ or ""
    assert "health" in doc and "restarted" in doc
