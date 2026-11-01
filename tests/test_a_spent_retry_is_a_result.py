"""A transient failure that outlives its retries is an answer, not a crash.

`run:06aa58a3` spent **447 minutes and $397.28** and reported `failed`. It was a
corroboration sweep: 154 discovery searches, of which 140 returned and 14 timed
out. The 155th call raised `LlmError: Codex timed out after 300s` and took the
whole run with it, banking none of the work.

`discovery` carries a branch for exactly this. It says, in its own comment, that
a model failure is not evidence the SOURCE is dead, and returns the source as
fine with a note so the sweep goes on:

    if not result.ok:
        return SourceResult(source_id=spec.id, ok=True, error=...)

It could not be reached by the failure that actually happens, because
`call_and_record` RAISED once the attempts were spent. The same function already
gets this right for an open circuit, and its docstring says why: letting
`BreakerOpen` propagate bypassed every `except LlmError` degrade path and
aborted the run at the first refusal. Spent patience is the same fact -- nothing
more can be had right now -- and now takes the same path.

A TERMINAL error still raises. The distinction is the harness's to make, and all
three drivers grade a timeout as transient; nothing here re-decides it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

import pytest
from venona.db import core as db
from venona.llm.executor import LlmError, LlmResult, Usage
from venona.llm.runner import call_and_record
from venona.settings import get_settings


@dataclass
class FailingExecutor:
    """Raises the same string the executor raises on a timeout."""

    error: str
    attempts: int = 0
    succeed_on: int | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def call(self, task: str, **kwargs) -> LlmResult:
        self.attempts += 1
        self.calls.append({"task": task, **kwargs})
        if self.succeed_on is not None and self.attempts >= self.succeed_on:
            return LlmResult(data={"ok": True}, usage=Usage(), cost_usd=0.01)
        raise LlmError(self.error)


class StubCtx:
    def __init__(self, conn: db.Conn, settings, llm) -> None:
        self.db = conn
        self.llm = llm
        self.settings = settings
        self.rng = random.Random(0)
        self.run_id = "run:test"
        self.ids = type("I", (), {"uuid7": staticmethod(lambda: "call:test")})()
        self.clock = type("C", (), {"now": staticmethod(lambda: "2026-09-13T00:00:00.000Z")})()

    async def counter(self, *args, **kwargs) -> None:
        return None

    async def emit(self, *args, **kwargs) -> None:
        return None

    def note(self, *args, **kwargs) -> None:
        return None


@pytest.fixture
def no_waiting(monkeypatch):
    """The curve is asserted below; in these three it is only wall clock.

    NOT autouse, because the one test that measures the curve has to call the
    real thing.
    """

    async def instant(attempt: int, **kwargs) -> float:
        return 0.0

    monkeypatch.setattr("venona.llm.budget.backoff", instant)
    # `_wait` asks for the curve and sleeps it itself, so patching `backoff`
    # alone leaves the real seconds in place.
    monkeypatch.setattr("venona.llm.budget.next_delay", lambda attempt, **kw: 0.0)


async def test_a_timeout_that_outlives_its_retries_comes_back_as_a_result(
    conn: db.Conn, no_waiting
) -> None:
    """The regression, in the words of the run that paid for it."""
    settings = get_settings()
    llm = FailingExecutor("Codex timed out after 300s")
    ctx = StubCtx(conn, settings, llm)

    result = await call_and_record(
        ctx,
        "discovery",
        user_content="x",
        schema={"type": "object"},
        degrade_when_spent=True,
    )

    assert not result.ok, "a call that never landed must not read as a success"
    assert result.error is not None and "timed out" in result.error, (
        "the caller's note to the reader is built from this, so it has to say "
        "what actually happened"
    )
    assert llm.attempts == settings.llm.retry_attempts


async def test_a_terminal_failure_still_raises(conn: db.Conn, no_waiting) -> None:
    """The other half, and the reason this is not simply "never raise".

    A malformed request or a missing binary is not going to come right on its
    own, and swallowing it would turn every such run into one that quietly
    found nothing.
    """
    settings = get_settings()
    llm = FailingExecutor("no such model: gpt-nonexistent")
    ctx = StubCtx(conn, settings, llm)

    with pytest.raises(LlmError):
        await call_and_record(ctx, "discovery", user_content="x", schema={"type": "object"})

    assert llm.attempts == 1, "a terminal error is not worth a second attempt"


async def test_a_transient_failure_that_clears_is_just_a_slow_call(
    conn: db.Conn, no_waiting
) -> None:
    """What the retry is FOR. The provider had a bad minute and came back."""
    settings = get_settings()
    llm = FailingExecutor("503 upstream unavailable", succeed_on=3)
    ctx = StubCtx(conn, settings, llm)

    result = await call_and_record(ctx, "discovery", user_content="x", schema={"type": "object"})

    assert result.ok
    assert llm.attempts == 3


# ── how long the policy actually waits ───────────────────────────────


async def test_the_policy_waits_long_enough_to_be_patience(monkeypatch) -> None:
    """Three attempts doubling from one second is not a retry policy.

    The waits were about a second and about two, so a call that met the same
    bad minute on each attempt had exhausted a policy that in total had not
    waited three seconds. Whether the provider recovers is decided on the scale
    of a provider's bad minute, so the curve has to reach it.

    Asserted as a RANGE because the jitter is the point: a run fires several
    calls at once, and without it they retry in lockstep and arrive as one
    burst on a service that is already struggling.
    """
    import venona.llm.budget as budget_mod

    slept: list[float] = []

    async def capture(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(budget_mod.asyncio, "sleep", capture)
    settings = get_settings()
    rng = random.Random(0)

    total = 0.0
    for attempt in range(settings.llm.retry_attempts - 1):
        total += await budget_mod.backoff(attempt, rng=rng, first=settings.llm.retry_base_seconds)

    assert total > 15.0, (
        f"the whole policy waits {total:.1f}s, which is not long enough for a provider to come back"
    )
    assert total < 300.0, "and not so long that a dead provider holds the lane"
    assert len(slept) == settings.llm.retry_attempts - 1, (
        "one wait between attempts, and none after the last"
    )


def test_the_discovery_ceiling_is_above_its_own_tail() -> None:
    """A ceiling inside the distribution it bounds censors rather than catches.

    Successful discovery searches on the defense corpus run to 299s, with a
    dozen in seven days past four minutes. At 300s one sweep timed out on 14 of
    154 searches, every one of them at exactly the limit -- so the failures
    were the right-censored tail of our own latency, not a provider fault.

    Also asserts the resolution PATH: `discovery` passes no literal, so an
    instance that needs a different ceiling can set one. A literal at the call
    site wins over `task_timeout_seconds`, which is how this one came to be
    pinned to a number nobody could raise.
    """
    from venona.settings import timeout_for

    settings = get_settings()
    assert timeout_for(settings.llm, "discovery") >= 600

    source = __import__("pathlib").Path("backend/venona/sources/discovery.py").read_text()
    assert "timeout=300" not in source, (
        "a literal timeout at the call site overrides llm.task_timeout_seconds"
    )


# ── saying so while it happens ───────────────────────────────────────


class RecordingCtx(StubCtx):
    """A ctx that keeps what was emitted.

    NO `stage` ATTRIBUTE, deliberately. `RunContext` has none (contract C7), and
    an earlier version of this stub invented one -- which is what let a feature
    that could never render in production pass its own tests. The emitter named
    `getattr(ctx, "stage", None)`, which is always None, and the client drops a
    stage-less `stage_progress` outright. Filling the stage in is the
    orchestrator's job, because it is the only thing that knows which is
    running.
    """

    def __init__(self, conn: db.Conn, settings, llm) -> None:
        super().__init__(conn, settings, llm)
        self.emitted: list[tuple[str, str | None, dict]] = []

    async def emit(self, type_: str, stage: str | None = None, **data) -> None:
        self.emitted.append((type_, stage, data))


async def test_a_run_that_is_waiting_says_so_while_it_waits(conn: db.Conn, no_waiting) -> None:
    """A waiting run and a wedged run look identical, and now for longer.

    The strip's own comment makes the case for the count beneath a stage:
    analyze runs for over an hour and a pulsing segment "cannot tell working
    from wedged". A call sitting out a provider's bad minute is the same
    problem with nothing on screen at all, and the outage wait runs to half an
    hour.

    `stage_progress` rather than a warning: `warn` is the only thing that sets
    `ctx.degraded`, so using it for a wait that recovers would report damage the
    reader never took.
    """
    settings = get_settings()
    llm = FailingExecutor("503 upstream unavailable", succeed_on=2)
    ctx = RecordingCtx(conn, settings, llm)

    await call_and_record(ctx, "discovery", user_content="x", schema={"type": "object"})

    progress = [d for t, _, d in ctx.emitted if t == "stage_progress"]
    assert any(p.get("waiting") for p in progress), "a wait that nothing reports"
    assert all(stage is None for t, stage, _ in ctx.emitted if t == "stage_progress"), (
        "a call does not know which stage asked for it; naming one here is how "
        "this came to name None and be discarded"
    )
    assert [p for p in progress if p.get("waiting")][0]["retry_in"] >= 0, (
        "the wait carries its own duration, or the strip has nothing to count"
    )


async def test_the_wait_is_cleared_when_it_is_over(conn: db.Conn, no_waiting) -> None:
    """A strip still saying "waiting" while the model answers is the same lie in
    the other direction, and it would outlive the run."""
    settings = get_settings()
    llm = FailingExecutor("503 upstream unavailable", succeed_on=2)
    ctx = RecordingCtx(conn, settings, llm)

    await call_and_record(ctx, "discovery", user_content="x", schema={"type": "object"})

    progress = [d for t, _, d in ctx.emitted if t == "stage_progress"]
    assert progress[-1].get("waiting") is False, (
        "the last thing said about the wait has to be that it ended"
    )


async def test_a_call_that_never_waits_says_nothing(conn: db.Conn, no_waiting) -> None:
    """Most calls succeed first time. A quiet one must stay quiet: a strip that
    mentions waiting on every call is one nobody reads when it matters."""
    settings = get_settings()
    llm = FailingExecutor("unused", succeed_on=1)
    ctx = RecordingCtx(conn, settings, llm)

    await call_and_record(ctx, "discovery", user_content="x", schema={"type": "object"})

    assert [t for t, _, _ in ctx.emitted if t == "stage_progress"] == []


async def test_a_caller_that_cannot_say_which_happened_still_gets_the_exception(
    conn: db.Conn, no_waiting
) -> None:
    """The property that protects the corpus, and the reason it is the default.

    "No answer was obtained" and "the answer was nothing" are opposite claims,
    and several callers write the second one down. `glossary.lookup_all` marks
    every surface `undefined` with an `attempted_run_id`, and the acronym sweep
    then excludes anything carrying a `last_attempt_at` -- so a provider having
    a bad half hour during a hundred-surface sweep would close all hundred
    questions permanently, report nothing grounded, and call the run
    `succeeded`.

    That file says so itself, twelve lines above the branch: one such mistake
    "would quietly close three hundred questions". The exception is what those
    paths were written against, so it stays the default.
    """
    settings = get_settings()
    llm = FailingExecutor("Codex timed out after 300s")
    ctx = StubCtx(conn, settings, llm)

    with pytest.raises(LlmError):
        await call_and_record(ctx, "acronym_read", user_content="x", schema={"type": "object"})

    assert llm.attempts == settings.llm.retry_attempts, (
        "the whole policy is still spent before it gives up"
    )


def test_only_a_caller_that_reports_it_may_degrade() -> None:
    """Which call sites opt in, held as a list rather than as a habit.

    The opt-in is one keyword away from every site that marks a surface, an
    entity or a document as looked-at, and those are exactly the sites that
    must keep the exception. `discovery` qualifies because its own failure
    branch returns the source as fine with a note, saying in its comment that a
    model failure is not evidence the source is dead.
    """
    import pathlib

    allowed = {"backend/venona/sources/discovery.py"}
    found = {
        str(path).replace("\\", "/")
        for path in pathlib.Path("backend/venona").rglob("*.py")
        if "degrade_when_spent=True" in path.read_text()
    }
    assert found == allowed, (
        f"{found ^ allowed} changed the set of callers allowed a degraded result. "
        f"A caller may only opt in when its own failure path can say that no "
        f"answer was obtained, rather than recording that the answer was nothing."
    )
