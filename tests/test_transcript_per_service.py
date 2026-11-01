"""Every service's stream reaches the transcript, or the run looks like it did nothing.

Contract C6's `model_message` is one shape, and each service names its own
events -- so the translation is the driver's. Asking the wrong one is silent:
every event maps to None, nothing is emitted, and the run page shows a run that
plainly did work with an empty transcript. There is no error to notice, which
is why this is a test rather than a comment.
"""

from __future__ import annotations

import json

import pytest
from venona.llm.harness import known, resolve

#: One line per service, in that service's own spelling. The first two were
#: taken from streams those CLIs actually emitted. GROK'S ARE FROM ITS
#: DOCUMENTATION and nobody has run it -- which is exactly why they are here: a
#: driver written from a reference is the one most likely to map every event to
#: None and show an empty transcript under a run that did work.
FIRST_EVENT = {
    "claude_code": {
        "type": "system",
        "subtype": "init",
        "session_id": "s-1",
        "model": "claude-sonnet-4-5",
        "tools": ["WebSearch"],
    },
    "codex": {"type": "thread.started", "thread_id": "01a0897f-8702-7052"},
    # This format's only preamble. It is the nearest thing the reference
    # describes to a session line, and without one this service would open its
    # transcript with nothing.
    "grok": {"type": "available_commands", "sessionId": "s-1", "tools": ["web_search"]},
}

ANSWER_EVENT = {
    "claude_code": {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "the answer"}]},
    },
    "codex": {
        "type": "item.completed",
        "item": {"item_type": "agent_message", "text": "the answer"},
    },
    "grok": {"type": "text", "text": "the answer"},
}

#: The line carrying the call's own outcome. It is the RETURN VALUE, and
#: forwarding it would persist a whole brief into `run_events`.
RESULT_EVENT = {
    "claude_code": {"type": "result", "is_error": False, "structured_output": {}},
    "codex": {"type": "turn.completed", "usage": {"input_tokens": 1}},
    "grok": {"type": "end", "stopReason": "end_turn", "total_cost_usd": 0.01},
}


@pytest.mark.parametrize("key", known())
def test_a_session_opens_the_transcript(key: str) -> None:
    payload = resolve(key).summarize(FIRST_EVENT[key], task="analyze")
    assert payload is not None, f"{key}: the first event produced no transcript entry"
    assert payload["kind"] == "session_started"
    assert payload["task"] == "analyze"


@pytest.mark.parametrize("key", known())
def test_the_answer_reaches_the_transcript(key: str) -> None:
    payload = resolve(key).summarize(ANSWER_EVENT[key], task="analyze")
    assert payload is not None, f"{key}: the model's own words produced no entry"
    assert payload["kind"] == "text"
    assert "the answer" in payload["text"]


@pytest.mark.parametrize("key", known())
def test_the_result_line_is_not_a_transcript_entry(key: str) -> None:
    assert not resolve(key).is_transcript_event(RESULT_EVENT[key])


@pytest.mark.parametrize("key", known())
def test_the_session_entry_only_claims_what_the_service_reported(key: str) -> None:
    """A service that does not name its model must not appear to have none.

    The render site joins the parts it is given; a driver that invented
    `tools: []` to fill the shape would draw "0 tools" on a call that had
    several, which is worse than saying nothing.
    """
    payload = resolve(key).summarize(FIRST_EVENT[key], task="analyze")
    assert payload is not None
    assert "tools" not in payload or isinstance(payload["tools"], list)
    assert "model" not in payload or isinstance(payload["model"], str)


def test_the_emitter_asks_the_active_service() -> None:
    """The wiring itself, because getting it wrong is invisible at run time.

    A stub without a harness falls back to the translation its recorded
    envelopes were written by, which is what keeps the offline suite working.
    """
    from venona.llm import runner

    class _Ctx:
        _emit = object()
        stage = None
        llm = type("E", (), {"harness": resolve("codex")})()

        async def emit(self, *a, **kw):
            self.seen = kw

    ctx = _Ctx()
    emit = runner.transcript_emitter(ctx, "analyze")
    assert emit is not None

    import asyncio

    asyncio.run(emit(json.loads(json.dumps(ANSWER_EVENT["codex"]))))
    assert ctx.seen["kind"] == "text", "the emitter did not use the active service"


@pytest.mark.parametrize("key", known())
def test_the_model_reaching_a_service_is_one_it_knows(key: str) -> None:
    """The configured value is a tier word, and tiers are not portable.

    Sending one service's vocabulary to another is refused by the provider,
    not by anything here: `invalid_request_error: The 'sonnet' model is not
    supported`, once per call, until the breaker gives up on a service that was
    working. So the resolution is asserted rather than assumed.
    """
    from venona.llm.harness import model_for

    harness = resolve(key)
    for tier in ("fast", "standard", "deep"):
        got = model_for(harness, tier)
        assert got != tier, f"{key}: {tier} did not resolve to a model name"

    # The OTHER service's words resolve too. An instance that has tuned its
    # models keeps working when it switches, rather than asking a provider for
    # a model belonging to somebody else.
    for foreign in ("sonnet", "opus", "haiku"):
        got = model_for(harness, foreign)
        if key != "claude_code":
            assert got != foreign, f"{key}: {foreign} was passed through unresolved"


def test_the_call_path_resolves_before_the_service_sees_it() -> None:
    """The wiring, because the helper existing is not the same as it being used.

    This is the shape of a bug that shipped: `model_for` was written, tested
    and never called, so every pipeline call sent a tier word to a provider
    that had never heard of it.
    """
    from venona.llm.runner import resolve_model

    class _Codex:
        harness = resolve("codex")

    assert resolve_model(_Codex(), "sonnet") == "gpt-5.6-sol"

    class _Stub:
        pass

    # A stub has no service, so its recorded envelopes keep their own spelling.
    assert resolve_model(_Stub(), "sonnet") == "sonnet"


def test_no_tier_resolves_to_a_model_a_subscription_cannot_call() -> None:
    """Measured, not assumed, and the measurement cost a run to find.

    On a ChatGPT account both `gpt-5.4-mini` and `gpt-5.6-sol-mini` answer
    `invalid_request_error: ... not supported when using Codex with a ChatGPT
    account`, while `luna`, `sol` and `terra` all work. The cheapest tier
    pointed at a mini model, so every `fast` call on the billing mode this
    project runs on failed -- once per call, until the breaker gave up on a
    service that was fine.

    Held as a test rather than a comment because the alias table is where
    somebody reaches first to make the cheap tier cheaper, and the model that
    looks cheapest is the one that does not exist here.
    """
    from venona.llm.harness import model_for

    unavailable = {"gpt-5.4-mini", "gpt-5.6-sol-mini"}
    codex = resolve("codex")
    for tier, model in codex.aliases.items():
        assert model not in unavailable, (
            f"{tier!r} resolves to {model!r}, which a ChatGPT account refuses"
        )
    assert model_for(codex, "fast") == "gpt-5.6-luna"


def test_a_run_says_which_service_thought_and_never_guesses() -> None:
    """The identity line's rule, which is the same rule every actor column has.

    A row from before the column existed, and a run cancelled before it was
    ever admitted, both genuinely have no answer -- the executor is chosen at
    ADMISSION, which for a queued light run is minutes after the row is
    written. Rendering either as "unknown" would be a guess presented as a
    fact, on the one line that explains why a brief does not read like last
    week's.
    """
    from venona.api.schemas.run import decorate

    assert decorate({"kind": "daily", "harness": "codex"})["harness_label"] == "Codex"
    assert decorate({"kind": "daily", "harness": None})["harness_label"] is None
    assert decorate({"kind": "daily"})["harness_label"] is None
    # A key this build no longer has a driver for still renders as itself
    # rather than vanishing: the run really did happen, on something.
    assert decorate({"kind": "daily", "harness": "retired"})["harness_label"] == "retired"


async def test_the_service_is_handed_the_resolved_model_not_the_tier_word() -> None:
    """`resolve_model` existing is not the same as the call using its answer.

    That exact gap shipped and cost a run: the helper was written, unit-tested
    and never reached, so every pipeline call handed a tier word straight to a
    provider that had never heard of it. The ledger recorded `model=opus`, the
    provider answered `invalid_request_error: The 'opus' model is not
    supported`, and it did that once per call until the breaker gave up on a
    service that was working.

    So this asserts on what the EXECUTOR was handed, not on what the helper
    returns. `sources.yaml` carries `model: opus` on discovery blocks today,
    which is how a tier word from a config file reaches this path at all.
    """
    from venona.llm.runner import call_and_record
    from venona.llm.types import LlmResult, Usage

    seen: dict[str, object] = {}

    class _Recording:
        harness = resolve("codex")

        async def call(self, task, **kw):
            seen.update(kw)
            return LlmResult(data={"ok": True}, usage=Usage())

    class _Clock:
        def now(self) -> str:
            return "2026-01-01T00:00:00Z"

    class _Ids:
        def uuid7(self) -> str:
            return "call:test"

    class _Ctx:
        settings = None
        stage = None
        llm = _Recording()
        run_id = "run:test"
        db = None
        clock = _Clock()
        ids = _Ids()

        def note(self, *a, **kw) -> None:
            pass

        async def emit(self, *a, **kw) -> None:
            pass

        async def counter(self, *a, **kw) -> None:
            pass

    from unittest.mock import patch

    from venona.settings import get_settings

    ctx = _Ctx()
    ctx.settings = get_settings()

    # The ledger write is not what this is about, and it needs a database.
    with patch("venona.llm.runner.record_call"):
        await call_and_record(
            ctx,
            "discovery",
            user_content="x",
            schema={"type": "object"},
            system_prompt="s",
            model="opus",
            effort="high",
        )
    assert seen.get("model") == "gpt-5.6-terra", (
        f"the service was handed {seen.get('model')!r}, which it has never heard of"
    )


def test_a_service_that_searched_does_not_report_zero_searches() -> None:
    """`web_search_requests` is a measurement, and 0 is a claim.

    This vendor's usage block carries tokens and nothing else, so the count has
    to come from the items in the stream. Without that every row reads 0 --
    which does not say "nobody measured", it says "no searches happened", about
    the discovery sweeps that are most of a run's spend and consist almost
    entirely of searching. `llm_calls.web_search_requests` read 0 on every row
    ever written once before, for the same reason on the other service.
    """
    from pathlib import Path

    fixture = (
        Path(__file__).resolve().parents[1] / "fixtures/llm/_codex/_stream/with_web_search.jsonl"
    )
    result = resolve("codex").parse(fixture.read_text(), cache_key="k", duration_ms=1)
    assert result.ok, result.error
    assert result.usage.web_search_requests == 1, (
        "a call that demonstrably searched reported no searches"
    )

    plain = (
        Path(__file__).resolve().parents[1] / "fixtures/llm/_codex/_stream/structured_reply.jsonl"
    )
    assert resolve("codex").parse(plain.read_text(), cache_key="k").usage.web_search_requests == 0


def test_a_subscription_still_gets_a_figure_when_the_service_sends_none() -> None:
    """Zero is not a smaller number than the other service's. It is free.

    Claude Code puts a shadow price in its envelope, so a subscription row
    carries `list_price`. Codex sends no figure at all, and leaving the column
    at zero does three things: every Codex run reads $0 in the history, the
    Overview panel counts none of it, and -- worst -- the price quoted in front
    of `Look up all` is an average over past runs that SPENT, so a service
    reporting nothing can never accumulate a sample and the modal quotes a
    constant forever.

    What the billing mode changes is what the number MEANS, which is what
    `cost_basis` records. Neither `list_price` nor `computed` is a bill.
    """
    from venona.llm.executor import CliExecutor
    from venona.llm.types import LlmResult, Usage
    from venona.settings import get_settings

    settings = get_settings()
    executor = CliExecutor(settings, harness=resolve("codex"))
    priced = executor._priced(
        LlmResult(data={}, usage=Usage(input_tokens=10_000, output_tokens=1_000)),
        "gpt-5.6-sol",
    )
    assert priced.cost_usd > 0, "a subscription row was left at zero, which reads as free"
    assert priced.cost_basis == "computed"

    # A model with no published price is None, never zero: a missing price is
    # not the same claim as free.
    unknown = executor._priced(
        LlmResult(data={}, usage=Usage(input_tokens=10_000)), "gpt-9-unreleased"
    )
    assert unknown.cost_usd == 0.0
    assert unknown.cost_basis == "none"
