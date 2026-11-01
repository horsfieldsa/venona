"""The executor waiting out a provider incident.

Anthropic's status page records a 20-to-40-minute "elevated errors" incident
every few weeks (2026-08-20, 19:16-19:42Z, major, Claude Code affected). Before
this, a run that met one opened the circuit within two clusters and then raced
through every remaining call as a refusal in seconds -- a whole day's brief lost
to half an hour of 529s. Now the research lane's executor sleeps until the next
half-open probe and becomes it, for up to `llm.outage_wait_seconds`; the light
lane passes 0 and fails fast, because a person is watching its spinner.

No subprocess here: `_run_streaming` is stubbed, the breaker runs on a
millisecond cooldown, and the clock is the loop's.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from venona.llm import executor as executor_mod
from venona.llm.breaker import FAILURE_THRESHOLD, BreakerOpen, CircuitBreaker
from venona.llm.executor import CliExecutor, LlmError
from venona.settings import get_settings

OK_ENVELOPE = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": {"answer": 1},
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "modelUsage": {},
        "total_cost_usd": 0.0,
    }
)


def _executor(*, outage_wait: float, cooldown: float = 0.05) -> CliExecutor:
    executor = CliExecutor(get_settings(), outage_wait_seconds=outage_wait)
    executor._breaker = CircuitBreaker(cooldown=cooldown)
    return executor


def _trip(executor: CliExecutor, error: str) -> None:
    for _ in range(FAILURE_THRESHOLD):
        executor.breaker.record_failure(error, now=asyncio.get_running_loop().time())


async def _call(executor: CliExecutor):
    return await executor.call("probe", user_content="x", schema={"type": "object"})


@pytest.fixture
def cli_answers(monkeypatch):
    """Stub the subprocess: a list of outcomes, consumed one per call. A string
    is stdout; an exception is raised as the CLI failing."""
    answers: list[object] = []

    async def fake_run_streaming(self, argv, *, timeout, user_content, on_event):
        outcome = answers.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(CliExecutor, "_run_streaming", fake_run_streaming)
    # Keep the wait loop's slices inside the test's patience.
    monkeypatch.setattr(executor_mod, "WAIT_SLICE_SECONDS", 0.01)
    return answers


@pytest.mark.asyncio
async def test_a_transient_outage_is_waited_out_and_the_circuit_closes(cli_answers) -> None:
    executor = _executor(outage_wait=5.0)
    _trip(executor, "529: overloaded_error")
    assert executor.breaker.is_open

    cli_answers.append(OK_ENVELOPE)
    started = time.monotonic()
    result = await _call(executor)

    assert result.ok
    assert not executor.breaker.is_open
    # It waited for the cooldown rather than refusing outright.
    assert time.monotonic() - started >= 0.04
    assert executor.breaker.opens == 1


@pytest.mark.asyncio
async def test_a_dead_token_is_refused_immediately_however_large_the_budget(
    cli_answers,
) -> None:
    """A 401 does not get better by waiting. Thirty minutes of sleep in front of
    an expired credential would be thirty minutes nobody was told."""
    executor = _executor(outage_wait=3600.0, cooldown=60.0)
    _trip(executor, "401 OAuth access token is invalid")

    started = time.monotonic()
    with pytest.raises(BreakerOpen):
        await _call(executor)
    assert time.monotonic() - started < 1.0
    assert cli_answers == []  # nothing was asked


@pytest.mark.asyncio
async def test_the_light_lane_fails_fast(cli_answers) -> None:
    """`outage_wait_seconds=0` is the light lane: a lookup someone is watching
    a spinner for must not sit out an outage on their behalf."""
    executor = _executor(outage_wait=0.0, cooldown=60.0)
    _trip(executor, "529: overloaded_error")

    with pytest.raises(BreakerOpen):
        await _call(executor)
    assert cli_answers == []


@pytest.mark.asyncio
async def test_a_spent_budget_refuses_for_the_rest_of_the_episode(cli_answers) -> None:
    """Measured from the START of the outage, not per call -- otherwise every
    call, and every retry of every call, buys itself a fresh half hour."""
    executor = _executor(outage_wait=5.0, cooldown=60.0)
    _trip(executor, "503 service unavailable")
    executor.breaker.episode_started_at = asyncio.get_running_loop().time() - 100.0

    started = time.monotonic()
    with pytest.raises(BreakerOpen):
        await _call(executor)
    assert time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_a_failed_probe_keeps_waiting_inside_the_budget(cli_answers) -> None:
    """The provider is still down at the first half-open probe: that probe's
    failure restarts the cooldown and the NEXT call keeps waiting, rather than
    refusing because `opened_at` moved."""
    executor = _executor(outage_wait=5.0)
    _trip(executor, "529: overloaded_error")

    cli_answers.append(LlmError("claude exited 1: 529 overloaded"))
    with pytest.raises(LlmError):
        await _call(executor)  # the probe itself fails
    assert executor.breaker.is_open
    assert executor.breaker.opens == 1

    cli_answers.append(OK_ENVELOPE)
    result = await _call(executor)  # waits another cooldown, then succeeds
    assert result.ok
    assert not executor.breaker.is_open


@pytest.mark.asyncio
async def test_a_cancelled_run_leaves_the_wait_promptly(cli_answers) -> None:
    """A cancel is a flag the stages poll between items. Without this hook a
    cancelled run would sit out the whole outage wait before noticing."""
    executor = _executor(outage_wait=3600.0, cooldown=60.0)
    _trip(executor, "529: overloaded_error")
    executor.wait_abort = lambda: True

    started = time.monotonic()
    with pytest.raises(BreakerOpen) as caught:
        await _call(executor)
    assert time.monotonic() - started < 1.0
    assert "cancelled" in str(caught.value)
