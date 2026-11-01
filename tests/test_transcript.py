"""The Claude Code session, reduced to something worth persisting.

`--output-format stream-json` is the raw session. Every event here is written to
`run_events` before it is published (contract C6), so the two failure modes this
module exists to prevent are VOLUME and LEAKAGE — a full run makes ~230 analyze
calls, and tool results for this pipeline are whole fetched web pages.
"""

from __future__ import annotations

import pytest
from venona.llm.transcript import MAX_TEXT, summarize


def test_an_assistant_turn_becomes_one_entry() -> None:
    event = summarize(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Scoring the cluster."}]},
        },
        task="analyze",
    )
    assert event == {
        "task": "analyze",
        "kind": "text",
        "text": "Scoring the cluster.",
        "tool": None,
        "input": None,
        "blocks": None,
        "subagent": False,
    }


def test_a_tool_call_names_the_tool() -> None:
    """The first thing you want when a tool-using stage returns nothing."""
    event = summarize(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "WebSearch", "input": {"query": "NGC2"}}]
            },
        },
        task="acronym_search",
    )
    assert event["kind"] == "tool_use"
    assert event["tool"] == "WebSearch"
    assert "NGC2" in event["input"]


def test_subagent_work_is_marked() -> None:
    """`--forward-subagent-text` surfaces delegated work with a
    `parent_tool_use_id`. Unmarked, it reads as the main session talking to
    itself."""
    event = summarize(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "sub"}]},
            "parent_tool_use_id": "toolu_1",
        },
        task="analyze",
    )
    assert event["subagent"] is True


def test_a_tool_result_carries_its_size_not_its_body() -> None:
    """LEAKAGE. A fetched page is already in `documents`; copying it into the
    event log would double the database to say nothing new."""
    page = "x" * 40_000
    event = summarize(
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": page}]},
        },
        task="acronym_read",
    )
    assert event["chars"] == 40_000
    assert len(event["preview"]) < 250
    assert page not in str(event)


def test_long_text_is_clipped_at_the_source() -> None:
    """VOLUME. Truncating in the UI would still have persisted the whole thing."""
    event = summarize(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "y" * 5000}]}},
        task="brief",
    )
    assert len(event["text"]) <= MAX_TEXT


def test_session_start_records_the_tools_actually_granted() -> None:
    """`dontAsk` denies tools while still exiting 0, so "which tools did this
    session really have" is the first question worth answering."""
    event = summarize(
        {
            "type": "system",
            "subtype": "init",
            "session_id": "s1",
            "model": "claude-sonnet-5",
            "tools": ["WebSearch", "WebFetch"],
        },
        task="acronym_search",
    )
    assert event["kind"] == "session_started"
    assert event["tools"] == ["WebSearch", "WebFetch"]


# ── what must NOT reach the log ──────────────────────────────────────


@pytest.mark.negative
@pytest.mark.parametrize(
    "raw",
    [
        {"type": "system", "subtype": "status", "status": "requesting"},
        {"type": "stream_event", "event": {"type": "content_block_delta"}},
        {"type": "assistant", "message": {"content": []}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "   "}]}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "hi"}]}},
        {"type": "unknown_future_event"},
    ],
)
def test_noise_is_dropped(raw: dict) -> None:
    """Silence is cheaper than noise, and every kept row costs a write."""
    assert summarize(raw, task="analyze") is None


@pytest.mark.negative
def test_an_allowed_rate_limit_is_not_worth_a_row() -> None:
    """It fires constantly and says nothing actionable. The interesting case —
    throttled, or burning overage — still gets through."""
    allowed = {
        "type": "rate_limit_event",
        "rate_limit_info": {"status": "allowed", "isUsingOverage": False},
    }
    assert summarize(allowed, task="analyze") is None

    throttled = {
        "type": "rate_limit_event",
        "rate_limit_info": {"status": "throttled", "rateLimitType": "five_hour"},
    }
    assert summarize(throttled, task="analyze")["kind"] == "rate_limit"
