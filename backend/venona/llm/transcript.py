"""CLI session messages -> `model_message` run events (contract C6).

A harness streams its own session here -- init, assistant turns, tool calls,
tool results, rate-limit notices -- and each one maps its vocabulary onto this
shape in its own driver. That is what makes a run
watchable instead of a progress bar that sits on "analyze" for twenty minutes.

Two things this module exists to prevent.

**Volume.** Every event is persisted before it is published (contract C6), so
the transcript lands in `run_events`. A full run makes ~230 analyze calls; at
raw fidelity that is six figures of rows for text nobody reads twice. So:
`--include-partial-messages` is not passed (no token-level deltas), text is
truncated here rather than in the UI, and anything with no operator value is
dropped outright.

**Leakage.** `user` messages carry tool RESULTS, which for this pipeline means
whole fetched web pages. Those already live in `documents`; copying them into
the event log would double the database for no gain. Results are reduced to a
shape and a size.

`summarize` returns None for events not worth a row. That is the common case and
it is deliberate -- silence is cheaper than noise.
"""

from __future__ import annotations

from typing import Any

# Long enough to see what the model is doing, short enough that 230 calls'
# worth of transcript stays smaller than the documents it is reasoning about.
MAX_TEXT = 600
MAX_TOOL_INPUT = 300


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, dict)]


def summarize(raw: dict[str, Any], *, task: str) -> dict[str, Any] | None:
    """One CLI event -> one `model_message` payload, or None to drop it."""
    kind = raw.get("type")

    if kind == "system":
        # `init` is the only system event with standing value: it names the
        # session and the tools actually granted, which is the first thing you
        # want when a tool-using stage comes back empty. `status` fires
        # constantly and says nothing an operator can act on.
        if raw.get("subtype") != "init":
            return None
        return {
            "task": task,
            "kind": "session_started",
            "session_id": raw.get("session_id"),
            "model": raw.get("model"),
            "tools": raw.get("tools") or [],
        }

    if kind == "rate_limit_event":
        info = raw.get("rate_limit_info") or {}
        # Only worth a row when it is not the boring answer. On a subscription
        # this is the signal that explains a slow night.
        if info.get("status") == "allowed" and not info.get("isUsingOverage"):
            return None
        return {
            "task": task,
            "kind": "rate_limit",
            "status": info.get("status"),
            "limit_type": info.get("rateLimitType"),
            "using_overage": bool(info.get("isUsingOverage")),
            "resets_at": info.get("resetsAt"),
        }

    if kind == "assistant":
        message = raw.get("message") or {}
        events: list[dict[str, Any]] = []
        for block in _blocks(message):
            if block.get("type") == "text" and (block.get("text") or "").strip():
                events.append({"kind": "text", "text": _clip(block["text"], MAX_TEXT)})
            elif block.get("type") == "thinking" and (block.get("thinking") or "").strip():
                events.append({"kind": "thinking", "text": _clip(block["thinking"], MAX_TEXT)})
            elif block.get("type") == "tool_use":
                events.append(
                    {
                        "kind": "tool_use",
                        "tool": block.get("name"),
                        "input": _clip(str(block.get("input") or ""), MAX_TOOL_INPUT),
                    }
                )
        if not events:
            return None
        # Several blocks in one turn become one row. The alternative is an
        # emit-per-block signature change through the whole call path for a
        # distinction the UI renders as one bubble anyway.
        head = events[0]
        return {
            "task": task,
            "kind": head["kind"],
            "text": head.get("text"),
            "tool": head.get("tool"),
            "input": head.get("input"),
            "blocks": events[1:] or None,
            "subagent": raw.get("parent_tool_use_id") is not None,
        }

    if kind == "user":
        # Tool results. The CONTENT is the payload we refuse to copy -- a
        # fetched page is already in `documents`. Shape and size only.
        message = raw.get("message") or {}
        for block in _blocks(message):
            if block.get("type") != "tool_result":
                continue
            body = block.get("content")
            text = body if isinstance(body, str) else str(body or "")
            return {
                "task": task,
                "kind": "tool_result",
                "is_error": bool(block.get("is_error")),
                "chars": len(text),
                "preview": _clip(text, 200),
                "subagent": raw.get("parent_tool_use_id") is not None,
            }
        return None

    return None
