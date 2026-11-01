"""The CLI health probe while the loop is busy.

Measured live on 2026-08-22 with a research run and two lookups in flight:
`/api/health` reported `ok: false, error: "version probe failed: unable to
perform operation on <WriteUnixTransport closed=True ...>; the handler is
closed"` while every real call succeeded and the circuit stayed closed. The
Runs page read that as "claude down · runs paused" and disabled every control
that spends, for the length of every concurrent run.

The cause is `communicate(b"")` writing to the stdin of a `claude --version`
that had already exited: uvloop raises `RuntimeError` from a closed transport
and `communicate()` only swallows `BrokenPipeError`/`ConnectionResetError`.
Two guards: a probe opens no stdin pipe at all, and a probe that does fail is
tried once more before the instance is declared down.
"""

from __future__ import annotations

import asyncio

import pytest
from venona.llm import executor as executor_mod
from venona.llm.executor import CliExecutor, LlmError
from venona.llm.harness import resolve
from venona.settings import get_settings

HANDLER_CLOSED = RuntimeError(
    "unable to perform operation on <WriteUnixTransport closed=True reading=False "
    "0x76afe45f25a0>; the handler is closed"
)


class FakeStdin:
    def __init__(self, raise_on_write: BaseException | None = None) -> None:
        self.raise_on_write = raise_on_write
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.raise_on_write is not None:
            raise self.raise_on_write

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeStdout:
    def __init__(self, payload: bytes) -> None:
        self._chunks = [payload]

    async def read(self, n: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class FakeProc:
    def __init__(self, *, stdout: bytes, stdin: FakeStdin | None) -> None:
        self.stdin = stdin
        self.stdout = FakeStdout(stdout)
        self.stderr = FakeStdout(b"")
        self.returncode = 0
        self._out = stdout

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        return self._out, b""

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_probe_opens_no_stdin_pipe(monkeypatch) -> None:
    """No pipe, no write, nothing for the race to close."""
    seen: list[dict] = []

    async def fake_exec(*argv, **kwargs):
        seen.append(kwargs)
        return FakeProc(stdout=b"2.1.220 (Claude Code)\n", stdin=None)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    executor = CliExecutor(get_settings(), harness=resolve("claude_code"))

    out = await executor._run(["claude", "--version"], timeout=5)

    assert out.strip() == "2.1.220 (Claude Code)"
    assert seen[0]["stdin"] is asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_a_probe_is_retried_once_before_the_instance_reads_as_down(monkeypatch) -> None:
    attempts: list[list[str]] = []

    async def flaky_run(self, argv, *, timeout, user_content=""):
        attempts.append(argv)
        if len(attempts) == 1:
            raise HANDLER_CLOSED
        return "2.1.220 (Claude Code)\n"

    monkeypatch.setattr(CliExecutor, "_run", flaky_run)
    executor = CliExecutor(get_settings(), harness=resolve("claude_code"))

    assert (await executor._probe(["claude", "--version"], timeout=5)).strip().startswith("2.1")
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_a_probe_that_fails_twice_is_reported(monkeypatch) -> None:
    """The retry separates a hiccup from a missing binary; it must not hide
    the latter."""

    async def dead_run(self, argv, *, timeout, user_content=""):
        raise LlmError("could not exec claude: No such file or directory")

    monkeypatch.setattr(CliExecutor, "_run", dead_run)
    executor = CliExecutor(get_settings(), harness=resolve("claude_code"))

    with pytest.raises(LlmError):
        await executor._probe(["claude", "--version"], timeout=5)


@pytest.mark.asyncio
async def test_a_streaming_call_survives_the_closed_transport(monkeypatch) -> None:
    """The same race on a real call: the CLI exits before reading its prompt
    (an auth failure, a bad flag) and uvloop's `RuntimeError` must end up as
    the result line's verdict, not as a bare exception out of `call()`."""
    result_line = b'{"type":"result","is_error":true,"result":"Failed to authenticate"}\n'

    async def fake_exec(*argv, **kwargs):
        return FakeProc(stdout=result_line, stdin=FakeStdin(raise_on_write=HANDLER_CLOSED))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    executor = CliExecutor(get_settings(), harness=resolve("claude_code"))

    stdout = await executor._run_streaming(
        ["claude", "-p"], timeout=5, user_content="x" * 100_000, on_event=None
    )

    assert stdout.encode() == result_line
    parsed = executor_mod.parse_envelope(stdout)
    assert parsed.ok is False
    assert "authenticate" in (parsed.error or "")


def test_the_uvloop_error_is_not_swallowed_by_communicate() -> None:
    """Pins the premise: CPython's `communicate()` ignores only these two, so a
    closed uvloop transport's `RuntimeError` escapes it. If a future asyncio
    widens the catch, the DEVNULL guard above is belt without braces."""
    import inspect
    from asyncio import subprocess as aio_subprocess

    source = inspect.getsource(aio_subprocess.Process._feed_stdin)
    assert "BrokenPipeError" in source and "ConnectionResetError" in source
    assert "RuntimeError" not in source
    assert not isinstance(HANDLER_CLOSED, (BrokenPipeError, ConnectionResetError))
