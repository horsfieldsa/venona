"""The call cap, and the carve-out that quietly disabled it.

`max_calls_per_run` is the one honest bound on a subscription run -- there is no
spend to manage, so what a run may attempt is the whole of the budget story. It
did not hold. `call_and_record` read:

    for attempt in range(MAX_ATTEMPTS):
        if attempt and budget is not None:
            budget.check()

`if attempt` skips the check on the FIRST attempt of every call. The carve-out
was written for the brief -- one call, and a near-cap run skipping it entirely
is the worst degradation available -- but `call_and_record` is also the path for
`define_write`, `adjudicate`, `acronym_read` and `discovery`, none of which
check the budget in their own loops before dispatch.

Measured on `run:06a8160f`: **152 charged calls against a cap of 120**, a 27%
overshoot, while the same run deferred 107 clusters, 8 acronym lookups and 9
definitions *for being out of budget*. Retries were never the leak.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

import pytest
from venona.db import core as db
from venona.llm import budget as budget_mod
from venona.llm.executor import LlmResult, Usage
from venona.llm.runner import call_and_record
from venona.settings import get_settings


@dataclass
class StubExecutor:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def call(self, task: str, **kwargs) -> LlmResult:
        self.calls.append({"task": task, **kwargs})
        return LlmResult(data={"ok": True}, usage=Usage(), cost_usd=0.01)


class StubCtx:
    def __init__(self, conn: db.Conn, settings) -> None:
        self.db = conn
        self.llm = StubExecutor()
        self.settings = settings
        self.rng = random.Random(0)
        self.run_id = "run:test"
        self.ids = type("I", (), {"uuid7": staticmethod(lambda: "call:test")})()
        self.clock = type("C", (), {"now": staticmethod(lambda: "2026-08-16T00:00:00.000Z")})()

    async def counter(self, *args, **kwargs) -> None:
        return None

    async def emit(self, *args, **kwargs) -> None:
        return None


def _spent_budget(settings):
    """A budget with nothing left."""
    budget = budget_mod.Budget(settings=settings.llm)
    budget.calls = settings.llm.max_calls_per_run
    return budget


# ── Budget.remaining ─────────────────────────────────────────────────


def test_remaining_is_what_a_concurrent_stage_needs() -> None:
    """`check()` answers "may I make one more", which a batch of N overshoots by
    up to N-1. A stage that dispatches concurrently has to size its batch."""
    settings = get_settings()
    budget = budget_mod.Budget(settings=settings.llm)
    assert budget.remaining == settings.llm.max_calls_per_run
    budget.calls = settings.llm.max_calls_per_run - 3
    assert budget.remaining == 3


def test_remaining_never_goes_negative() -> None:
    """A call that retried charges more than once, so `calls` can pass the cap.
    A negative slice would read as "take from the end" and dispatch the wrong
    rows rather than none."""
    settings = get_settings()
    budget = budget_mod.Budget(settings=settings.llm)
    budget.calls = settings.llm.max_calls_per_run + 5
    assert budget.remaining == 0


# ── the cap holds on first attempts ──────────────────────────────────


@pytest.mark.negative
async def test_a_call_at_the_cap_is_refused(conn: db.Conn) -> None:
    """The regression. `define_write` made 31 of these past a spent budget."""
    settings = get_settings()
    ctx = StubCtx(conn, settings)
    with pytest.raises(budget_mod.BudgetExceeded):
        await call_and_record(
            ctx,
            "define_write",
            user_content="x",
            schema={"type": "object"},
            budget=_spent_budget(settings),
        )
    assert ctx.llm.calls == [], "the executor must not be reached at all"


async def test_the_brief_is_exempt(conn: db.Conn) -> None:
    """And it is the only thing that is.

    A run that gathered and scored all day and then skipped the brief because it
    was one call over is the worst possible degradation -- the brief IS the
    product. The exemption is now something a call site asks for by name rather
    than something every call site got by accident.
    """
    settings = get_settings()
    ctx = StubCtx(conn, settings)
    result = await call_and_record(
        ctx,
        "brief",
        user_content="x",
        schema={"type": "object"},
        budget=_spent_budget(settings),
        exempt=True,
    )
    assert result.ok
    assert len(ctx.llm.calls) == 1


async def test_a_call_below_the_cap_still_goes_through(conn: db.Conn) -> None:
    """The check must bound, not block. Every non-exempt task depends on this."""
    settings = get_settings()
    ctx = StubCtx(conn, settings)
    budget = budget_mod.Budget(settings=settings.llm)
    result = await call_and_record(
        ctx, "define_write", user_content="x", schema={"type": "object"}, budget=budget
    )
    assert result.ok
    assert budget.calls == 1


async def test_no_budget_at_all_is_still_allowed(conn: db.Conn) -> None:
    """`budget=None` is how the CLI's one-off paths call this. Requiring one
    would turn an optional bound into a mandatory dependency."""
    ctx = StubCtx(conn, get_settings())
    result = await call_and_record(
        ctx, "define_write", user_content="x", schema={"type": "object"}, budget=None
    )
    assert result.ok
