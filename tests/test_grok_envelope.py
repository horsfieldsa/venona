"""Grok's stream, parsed by Grok's driver, from envelopes NOBODY HAS RECORDED.

This file is deliberately unlike `test_codex_envelope.py` beside it, and the
difference is the point. Those fixtures were emitted by a real binary on a real
account, and the file says so: the shapes it pins are ones nobody would think to
invent. These are hand-written from `xai-org/grok-build`'s headless reference,
so they pin what this DRIVER does with a documented shape and cannot pin what
the vendor actually emits.

So what is tested here is the half that is ours: the last-text rule, the
stop-reason vocabulary, the error-word classifier, the search counter, and the
argv decisions that exist because this vendor does not report permission
denials. When somebody records real envelopes, these stay and a fixture-backed
file joins them -- the hand-written cases are still the edge cases, and a real
recording will not contain a refusal or a usage ceiling on demand.
"""

from __future__ import annotations

import json

import pytest
from venona.llm.harness import CallRequest, Credential, resolve

GROK = resolve("grok")


def stream(*events: dict) -> str:
    """Newline-delimited JSON, the way `--output-format streaming-json` emits."""
    return "\n".join(json.dumps(event) for event in events) + "\n"


def end(**extra) -> dict:
    return {"type": "end", "stopReason": "end_turn", **extra}


# ── the answer ───────────────────────────────────────────────────────


def test_the_last_text_is_the_answer_and_not_the_first() -> None:
    """A turn may narrate before it answers.

    Taking the first text returns a well-formed lie, and joining the chunks
    returns something that parses as nothing. Both failures look like a model
    that cannot follow a schema.
    """
    out = GROK.parse(
        stream(
            {"type": "text", "text": "Looking at the article now."},
            {"type": "text", "text": '{"verdict": "kept", "score": 7}'},
            end(),
        )
    )
    assert out.data == {"verdict": "kept", "score": 7}
    assert out.error is None
    assert not out.refused


def test_text_that_is_not_a_document_is_refused_rather_than_returned() -> None:
    out = GROK.parse(stream({"type": "text", "text": "I could not do that."}, end()))
    assert out.data is None
    assert out.refused
    assert "no structured output" in (out.error or "")


def test_an_answer_that_is_a_list_is_not_an_object() -> None:
    """A schema can produce valid JSON that is the wrong SHAPE, and every caller
    here indexes the result by key."""
    out = GROK.parse(stream({"type": "text", "text": "[1, 2, 3]"}, end()))
    assert out.data is None
    assert out.refused
    assert "not an object" in (out.error or "")


def test_a_stream_with_no_text_at_all_is_refused_not_empty() -> None:
    out = GROK.parse(stream({"type": "thought", "text": "hmm"}, end()))
    assert out.data is None
    assert out.refused
    assert out.error == "no answer in the stream"


# ── how a turn can end ───────────────────────────────────────────────


@pytest.mark.parametrize("reason", ["refusal", "max_tokens", "max_turn_requests"])
def test_stopping_early_is_a_result_and_never_damage(reason: str) -> None:
    """These must not count against the circuit breaker.

    Retrying a refusal fails identically, and opening the breaker on one stops
    work that is fine -- which is how a service that was working got given up
    on once already.
    """
    out = GROK.parse(stream({"type": "text", "text": "{}"}, end(stopReason=reason)))
    assert out.refused
    assert out.data is None
    assert GROK.classify(out.error) == "terminal"


def test_a_clean_stop_returns_the_document() -> None:
    out = GROK.parse(stream({"type": "text", "text": '{"ok": true}'}, end()))
    assert out.data == {"ok": True}
    assert not out.refused


def test_an_error_event_beats_a_text_that_arrived_before_it() -> None:
    out = GROK.parse(
        stream(
            {"type": "text", "text": '{"ok": true}'},
            {"type": "error", "errorType": "overloaded_error", "message": "capacity"},
        )
    )
    assert out.data is None
    assert "capacity" in (out.error or "")


# ── the ledger ───────────────────────────────────────────────────────


def test_the_token_counts_reach_the_ledger() -> None:
    out = GROK.parse(
        stream(
            {"type": "text", "text": "{}"},
            end(
                usage={
                    "input_tokens": 7210,
                    "output_tokens": 1893,
                    "cache_read_input_tokens": 41000,
                }
            ),
        )
    )
    assert out.usage.input_tokens == 7210
    assert out.usage.output_tokens == 1893
    assert out.usage.cache_read_input_tokens == 41000


def test_a_reported_price_is_measured_and_an_absent_one_is_not_zero() -> None:
    """`cost_basis` is what stops four kinds of number being summed.

    This vendor reports `total_cost_usd`, so a call that carries one is
    `measured`. A call that does not is `none` -- NOT a computed zero, which
    would read as a call that was free.
    """
    priced = GROK.parse(stream({"type": "text", "text": "{}"}, end(total_cost_usd=0.0127)))
    assert priced.cost_usd == pytest.approx(0.0127)
    assert priced.cost_basis == "measured"

    unpriced = GROK.parse(stream({"type": "text", "text": "{}"}, end()))
    assert unpriced.cost_usd == 0.0
    assert unpriced.cost_basis == "none"


def test_searches_are_counted_from_the_stream() -> None:
    """The usage block carries tokens and nothing else.

    Without this every row reads `web_search_requests: 0`, and a zero does not
    say "nobody measured" -- it says "no searches happened", about the sweeps
    that are most of a run's spend and consist of searching.
    """
    out = GROK.parse(
        stream(
            {"type": "tool_call", "name": "web_search", "query": "NGC2"},
            {"type": "tool_call", "name": "web_search", "query": "Maven"},
            {"type": "tool_call", "name": "read_file", "input": "x"},
            {"type": "text", "text": "{}"},
            end(),
        )
    )
    assert out.usage.web_search_requests == 2


def test_the_price_table_is_absent_on_purpose() -> None:
    """Unlike Codex, this service reports its own cost, so a table here would be
    a second and worse estimate competing with a measured figure."""
    assert GROK.cost_for("grok-4.6", out_usage := GROK.parse(stream(end())).usage) is None
    assert out_usage is not None


# ── what is transient ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "rate limit reached, try again at 2:25 PM",
        "You've hit your usage limit.",
        "upstream service unavailable",
        "gateway timeout",
        "the model is overloaded",
    ],
)
def test_a_ceiling_is_transient_and_not_the_end_of_the_run(message: str) -> None:
    """The research lane is built to wait an outage out (ADR 0155).

    Grading one of these terminal is what opened the breaker after six and cost
    a run its brief on the other service. A usage limit whose own message says
    when it ends is the definition of transient.
    """
    assert GROK.classify(message) == "transient"


def test_a_request_id_full_of_digits_is_still_terminal() -> None:
    """WORDS, NEVER NUMBERS, and this is the case that forces it.

    These errors carry a long hex request id, so roughly one in sixteen contains
    `429` or `500` by accident. A numeric matcher would grade a schema rejection
    as a provider outage and wait half an hour for it to clear.
    """
    assert GROK.classify("invalid_request: bad schema (requestId 500e429fa1b2c3d4)") == "terminal"


def test_no_error_is_terminal_rather_than_a_retry_forever() -> None:
    assert GROK.classify(None) == "terminal"
    assert GROK.classify("") == "terminal"


# ── the transcript ───────────────────────────────────────────────────


def test_the_outcome_events_never_reach_the_transcript() -> None:
    """`end` and `error` carry the call's own result. Forwarding them puts the
    answer in the operator log twice and the error in it as a message."""
    assert not GROK.is_transcript_event({"type": "end"})
    assert not GROK.is_transcript_event({"type": "error"})
    assert GROK.is_transcript_event({"type": "text"})
    assert GROK.is_transcript_event({"type": "thought"})


def test_a_tool_line_carries_the_name_of_the_tool() -> None:
    """`tool` is the key contract C6 names and the render site reads. Dropping
    it renders a tool line with no chip saying what ran."""
    summary = GROK.summarize(
        {"type": "tool_call", "name": "web_search", "query": "NGC2"}, task="analyze"
    )
    assert summary is not None
    assert summary["kind"] == "tool_use"
    assert summary["tool"] == "web_search"
    assert "NGC2" in summary["input"]


def test_a_coding_agents_plan_is_not_shown_to_an_operator() -> None:
    """`plan` is a coding tool's plan for work Venona never asked for."""
    assert GROK.summarize({"type": "plan", "steps": []}, task="analyze") is None


def test_the_preamble_opens_the_transcript_without_inventing_its_contents() -> None:
    """A run that did work above an empty transcript is the silent failure here.

    But the shape must not be padded either: a driver that supplied `tools: []`
    would draw "0 tools" on a call that had several.
    """
    bare = GROK.summarize({"type": "available_commands"}, task="analyze")
    assert bare == {"kind": "session_started", "task": "analyze"}

    full = GROK.summarize(
        {"type": "available_commands", "sessionId": "s-1", "tools": ["web_search"]},
        task="analyze",
    )
    assert full is not None
    assert full["session_id"] == "s-1"
    assert full["tools"] == ["web_search"]


# ── the argv, which is where the permission-denial gap is answered ───


class _Files:
    """`FileCache` for a test: records what was written, returns no path.

    Returning None is the interesting branch -- it makes `argv` fall back to
    passing the prompt inline, which is the path that has to stay correct.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self.written: list[tuple[str, str]] = []

    async def materialize(self, content: str, *, kind: str, suffix: str) -> str | None:
        self.written.append((kind, content))
        return self.path


def request(**extra) -> CallRequest:
    base = {
        "task": "analyze",
        "system_prompt": "you are a reader",
        "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        "model": "grok-4.6",
        "effort": "medium",
        "tools": [],
        "user_content": "an article",
    }
    return CallRequest(**{**base, **extra})


async def test_the_argv_never_leaves_a_call_able_to_be_denied_quietly() -> None:
    """The reason `--yolo` is here, stated as a test rather than only a comment.

    This vendor does not collect permission denials, so a gated tool call is
    either a hang (no terminal to approve at) or a silent "found nothing". A
    `--deny` rule would reintroduce exactly the failure CLAUDE.md bans treating
    as a result, with nothing left to detect it.
    """
    argv = await GROK.argv(request(), _Files("/tmp/p.md"))
    assert "--yolo" in argv
    assert "--deny" not in argv
    assert "--permission-mode" not in argv


async def test_asking_for_search_grants_it_and_not_asking_turns_it_off() -> None:
    """EITHER VOCABULARY. Callers name capabilities; older sites named one
    vendor's tools. Matching one and not the other disables search on every call
    while the prompt and the schema still ask the model to search."""
    with_search = await GROK.argv(request(tools=["web_search"]), _Files("/tmp/p.md"))
    assert "--disable-web-search" not in with_search

    also = await GROK.argv(request(tools=["WebSearch"]), _Files("/tmp/p.md"))
    assert "--disable-web-search" not in also

    without = await GROK.argv(request(tools=[]), _Files("/tmp/p.md"))
    assert "--disable-web-search" in without


async def test_the_prompt_goes_to_a_file_because_argv_has_a_ceiling() -> None:
    """A single argv element is capped at MAX_ARG_STRLEN and this content
    reaches 192k. The inline form is the fallback, not the path."""
    files = _Files("/tmp/prompt.md")
    argv = await GROK.argv(request(), files)
    assert "--prompt-file" in argv
    assert argv[argv.index("--prompt-file") + 1] == "/tmp/prompt.md"
    assert "-p" not in argv

    inline = await GROK.argv(request(), _Files(None))
    assert "-p" in inline
    assert inline[inline.index("-p") + 1] == "an article"


async def test_a_coding_agents_defaults_are_turned_off() -> None:
    """Venona asks one question and wants one document. A plan-first loop and
    eight parallel subagents would spend a subscription on deliberation nothing
    reads."""
    argv = await GROK.argv(request(), _Files("/tmp/p.md"))
    assert "--no-plan" in argv
    assert "--no-subagents" in argv


async def test_the_schema_is_passed_and_the_output_is_the_event_stream() -> None:
    argv = await GROK.argv(request(), _Files("/tmp/p.md"))
    assert argv[argv.index("--output-format") + 1] == "streaming-json"
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["properties"]["ok"]["type"] == "boolean"


# ── the environment ──────────────────────────────────────────────────


def test_the_child_environment_is_an_allowlist_and_not_an_inheritance() -> None:
    """This service runs a shell, so the question is not whether it is
    trustworthy but what is within reach. A prompt that talks the model into
    running `printenv` must learn nothing."""
    env = GROK.child_env(
        {
            "PATH": "/usr/bin",
            "DATABASE_URL": "postgres://secret",
            "NEO4J_PASSWORD": "hunter2",
            "HTTPS_PROXY": "http://proxy:3128",
            "HOME": "/root",
        },
        Credential(),
    )
    assert env["PATH"] == "/usr/bin"
    assert "DATABASE_URL" not in env
    assert "NEO4J_PASSWORD" not in env
    # The one inherited group it keeps: without it a deployment behind an egress
    # proxy cannot reach the provider at all.
    assert env["HTTPS_PROXY"] == "http://proxy:3128"


def test_the_updater_is_off_so_the_pin_is_not_a_fiction() -> None:
    env = GROK.child_env({"PATH": "/usr/bin"}, Credential())
    assert env["GROK_DISABLE_AUTOUPDATER"] == "1"


def test_a_pasted_key_is_metered_and_a_subscription_holds_no_variable() -> None:
    """Matching on an `_API_KEY` suffix once put a subscription token into the
    variable that means metered billing. Declared, never guessed."""
    assert GROK.env_for("api") == "XAI_API_KEY"
    assert GROK.env_for("subscription") is None
    assert "XAI_API_KEY" in GROK.metered_env


def test_no_usage_windows_are_promised() -> None:
    """A service that will report a window has simply not been asked yet. One
    that never will must not be described that way on the strip a reader
    consults to decide whether to start another run."""
    assert GROK.reports_usage_windows is False
    assert GROK.quota() is None
