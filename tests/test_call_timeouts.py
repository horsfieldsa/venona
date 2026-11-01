"""Per-task call ceilings.

`llm.timeout_seconds` is one number for every call the app makes, and the
wizard's ontology document does not fit it: recording the wizard live (epic C)
killed three attempts at the 300s ceiling for nothing, and the identical prompt
returned in ~6 minutes at 900. Raising the global instead would have lengthened
every lane's calls, which is how a wedged subprocess stops looking like one --
so the ceiling is per task, and the global stays what it is.

No subprocess here: `_run_streaming` is stubbed and asked what it was handed.
"""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError
from venona.llm.executor import CliExecutor
from venona.llm.harness import resolve
from venona.settings import (
    TASK_TIMEOUT_CEILING,
    TASK_TIMEOUT_DEFAULTS,
    LlmSettings,
    get_settings,
    timeout_for,
)

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


def _llm(**overrides) -> LlmSettings:
    return LlmSettings(timeout_seconds=300, **overrides)


def test_an_unlisted_task_gets_the_global_default() -> None:
    assert timeout_for(_llm(), "analyze") == 300


def test_the_shipped_default_covers_the_task_the_global_does_not_fit() -> None:
    """The wizard's ontology document, which is why this setting exists."""
    assert timeout_for(_llm(), "setup_ontology") == 900
    assert TASK_TIMEOUT_DEFAULTS["setup_ontology"] == 900


def test_an_instance_overrides_a_shipped_default() -> None:
    llm = _llm(task_timeout_seconds={"setup_ontology": 1200})
    assert timeout_for(llm, "setup_ontology") == 1200


def test_one_override_does_not_erase_the_others() -> None:
    """The footgun this resolution order exists to defuse.

    A dict field replaces its default wholesale, so an operator who adds an
    unrelated ceiling would otherwise drop the shipped one and put the wizard
    back where it started -- silently, on the run after next.
    """
    llm = _llm(task_timeout_seconds={"brief": 600})
    assert timeout_for(llm, "brief") == 600
    assert timeout_for(llm, "setup_ontology") == 900
    assert timeout_for(llm, "analyze") == 300


@pytest.mark.parametrize("seconds", [0, -1])
def test_a_timeout_that_is_not_a_duration_is_refused(seconds: int) -> None:
    """Zero reads as 'no limit' and is the opposite: `asyncio.wait_for` takes
    it literally and kills the call before the CLI has answered."""
    with pytest.raises(ValidationError, match="at least 1 second"):
        _llm(task_timeout_seconds={"analyze": seconds})


def test_a_timeout_above_the_ceiling_is_refused() -> None:
    with pytest.raises(ValidationError, match="ceiling"):
        _llm(task_timeout_seconds={"analyze": TASK_TIMEOUT_CEILING + 1})


def test_the_refusal_names_the_task() -> None:
    """One bad entry in a map of several; the message has to say which."""
    with pytest.raises(ValidationError, match="setup_anchors_trunk"):
        _llm(task_timeout_seconds={"analyze": 600, "setup_anchors_trunk": 0})


@pytest.fixture
def handed_timeout(monkeypatch):
    """The timeout `_run_streaming` actually received, per call."""
    seen: list[int] = []

    async def fake_run_streaming(self, argv, *, timeout, user_content, on_event):
        seen.append(timeout)
        return OK_ENVELOPE

    monkeypatch.setattr(CliExecutor, "_run_streaming", fake_run_streaming)
    return seen


def _executor(**llm_overrides) -> CliExecutor:
    scratch = copy.deepcopy(get_settings())
    scratch.llm = scratch.llm.model_copy(update={"timeout_seconds": 300, **llm_overrides})
    return CliExecutor(scratch, harness=resolve("claude_code"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task", "expected"),
    [("analyze", 300), ("setup_ontology", 900)],
)
async def test_the_executor_hands_the_resolved_ceiling_to_the_subprocess(
    handed_timeout, task: str, expected: int
) -> None:
    """The wiring, not just the helper. Resolving in the executor is what makes
    this reach every caller: `timeout` was already on the protocol and nothing
    but web grounding ever passed it."""
    await _executor().call(task, user_content="x", schema={"type": "object"})
    assert handed_timeout == [expected]


@pytest.mark.asyncio
async def test_an_explicit_timeout_still_outranks_the_table(handed_timeout) -> None:
    """A caller that has already decided is not second-guessed by config."""
    executor = _executor(task_timeout_seconds={"setup_ontology": 1200})
    await executor.call("setup_ontology", user_content="x", schema={"type": "object"}, timeout=45)
    assert handed_timeout == [45]
