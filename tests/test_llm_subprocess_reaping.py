"""Every `claude` subprocess is reaped, on every exit path.

A `claude` process that outlives its caller is not idle. It runs its model call
to completion, holds a slot the executor's semaphore believes is free, and
spends against the subscription with nothing left to record the spend -- the
ledger write lives in the code that was cancelled.

The path that was missing is CANCELLATION, which is also the one that happens
in production: when one coroutine in an `asyncio.gather` raises, the rest are
cancelled, and a `wait_for` that only handled `TimeoutError` and `OSError` let
their subprocesses run on unattended.
"""

from __future__ import annotations

import inspect

import pytest
from venona.llm.executor import CliExecutor, _reap


class _FakeProc:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        self.killed = False
        self.waited = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.waited = True
        return self.returncode or 0


async def test_a_running_child_is_killed_and_reaped() -> None:
    """`returncode is None` means it is still going. Nobody else is going to
    stop it: the caller is unwinding."""
    proc = _FakeProc(returncode=None)
    await _reap(proc)
    assert proc.killed and proc.waited


async def test_a_finished_child_is_left_alone() -> None:
    """The ordinary path runs through this too, so it has to be a no-op when the
    process has already exited -- and must not reap a status something else is
    about to read."""
    proc = _FakeProc(returncode=0)
    await _reap(proc)
    assert not proc.killed


async def test_a_child_that_exits_between_the_check_and_the_signal_is_not_an_error() -> None:
    """The benign race: `ProcessLookupError` means the thing we wanted gone is
    gone. Raising here would replace a model error with a confusing one."""

    class _Vanishing(_FakeProc):
        def kill(self) -> None:
            raise ProcessLookupError

    proc = _Vanishing(returncode=None)
    await _reap(proc)  # must not raise


@pytest.mark.parametrize("method", ["_run_streaming", "_run"])
def test_every_subprocess_wait_reaps_in_a_finally(method: str) -> None:
    """Asserted against the source because the alternative is a test that
    genuinely cancels a real `claude`, and the property is structural: the reap
    must sit in a `finally`, not in the two named `except` clauses. Catching
    only `TimeoutError` and `OSError` is exactly the bug -- cancellation is
    neither."""
    source = inspect.getsource(getattr(CliExecutor, method))
    assert "finally:" in source, f"{method} has no finally around its subprocess wait"
    assert "_reap(proc)" in source, f"{method} does not reap its subprocess"
    assert "except asyncio.CancelledError" not in source, (
        "cancellation must be handled by the finally, not swallowed by an except"
    )
