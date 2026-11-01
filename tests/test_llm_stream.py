"""Reading the CLI's stdout, specifically when a line is large.

The regression here cost a completed run its brief. `claude` had already
emitted the StructuredOutput tool_use, the model had finished three minutes of
Opus work, and the process exited 0 -- but the final `{"type":"result"}` line
carrying the whole brief was over `StreamReader`'s 64k limit, so `readline()`
raised `ValueError` and the envelope was discarded in the client. The failure
is entirely on our side of the pipe and gets worse as briefs get longer, so it
is pinned here with a real subprocess rather than a mocked stream.
"""

from __future__ import annotations

import json
import sys

import pytest
from venona.llm.executor import CliExecutor
from venona.llm.harness import resolve
from venona.settings import get_settings

# Comfortably past both the 64k reader limit and the 64k pipe buffer.
BIG = 400_000


SCRIPT = """
import json, sys
sys.stdin.read()
sys.stdout.write(json.dumps({"type": "system", "subtype": "init"}) + "\\n")
sys.stdout.flush()
sys.stdout.write(json.dumps({"type": "result", "payload": "x" * SIZE}) + EOL)
"""


def emitter(payload_chars: int, *, trailing_newline: bool = True) -> list[str]:
    """argv for a fake CLI that emits a small line then one very large one."""
    script = SCRIPT.replace("SIZE", str(payload_chars)).replace(
        "EOL", '"\\n"' if trailing_newline else '""'
    )
    return [sys.executable, "-c", script]


async def run(argv: list[str], **kwargs):
    executor = CliExecutor(get_settings(), harness=resolve("claude_code"))
    return await executor._run_streaming(argv, timeout=60, user_content="prompt", **kwargs)


@pytest.mark.asyncio
async def test_result_line_over_the_reader_limit_survives() -> None:
    """The bug. A 400k result line must come back whole, not raise."""
    stdout = await run(emitter(BIG), on_event=None)

    lines = [line for line in stdout.splitlines() if line.strip()]
    assert len(lines) == 2
    parsed = json.loads(lines[-1])
    assert parsed["type"] == "result"
    assert len(parsed["payload"]) == BIG


@pytest.mark.asyncio
async def test_final_line_without_a_trailing_newline_is_not_dropped() -> None:
    """Hand-rolled line splitting invents this failure mode; readline did not.

    A CLI that exits without a final newline would otherwise lose the envelope
    still sitting in the buffer -- the same lost-brief symptom, new cause.
    """
    stdout = await run(emitter(BIG, trailing_newline=False), on_event=None)

    parsed = json.loads(stdout.splitlines()[-1])
    assert len(parsed["payload"]) == BIG


@pytest.mark.asyncio
async def test_events_still_stream_and_the_result_line_is_not_one() -> None:
    """Chunked reads must not break per-message streaming (contract C6)."""
    seen: list[dict] = []

    async def on_event(event: dict) -> None:
        seen.append(event)

    await run(emitter(BIG), on_event=on_event)

    # The init line is a transcript event; the result line is the return value.
    assert [e["type"] for e in seen] == ["system"]
