"""Method dispatch. Stateless, dual-era, and the only place a tool is called.

Every branch answers one JSON-RPC request and returns one response object. There
is no session, no server-initiated message and no stream, so a request is a
function call and the transport is a POST -- which is what lets the endpoint be
an ordinary `APIRoute` rather than a mounted ASGI app. See `protocol.py` for why
that mattered enough to hand-roll this.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from venona.api.mcp import protocol, registry, render
from venona.api.mcp.tools import ToolContext, ToolError


def _text(payload: str) -> dict[str, Any]:
    """A `CallToolResult`. Text only.

    No `structuredContent` and no `outputSchema`: the spec says a server
    returning structured content SHOULD also return equivalent unstructured
    content, which doubles the tokens -- and the only consumer here is a language
    model. Revisit the day a non-LLM client parses this.
    """
    return {"content": [{"type": "text", "text": payload}], "isError": False}


def _tool_error(message: str) -> dict[str, Any]:
    """A result the model can act on, not a transport failure.

    `isError: true` rather than a JSON-RPC error code, deliberately: a transport
    error usually never reaches the model's context, while this does -- so the
    agent can read "check the predicate against venona_ontology" and retry
    correctly instead of giving up or looping.
    """
    return {"content": [{"type": "text", "text": message}], "isError": True}


async def _call_tool(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    tool = registry.TOOLS.get(name)
    if tool is None:
        known = ", ".join(sorted(registry.TOOLS))
        return _tool_error(f"no tool named `{name}`. This server has: {known}.")

    try:
        args = tool.args_model.model_validate(arguments or {})
    except ValidationError as exc:
        # `isError`, not -32602. Bad ARGUMENTS are something the model can fix on
        # its own if it can read why; an unknown METHOD is a protocol fault and
        # stays a JSON-RPC error.
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()
        )
        return _tool_error(f"those arguments do not work for `{name}` — {problems}")

    try:
        if tool.is_async:
            body = await tool.handler(ctx, args)
        else:
            # A synchronous driver (and ~84ms of `build_index` for annotate)
            # must not run on the event loop: it would stall the /api/health the
            # reader polls every three seconds during a run.
            body = await run_in_threadpool(tool.handler, ctx, args)
    except ToolError as exc:
        return _tool_error(str(exc))

    return _text(render.clip(body, tool.max_chars))


def _read_resource(ctx: ToolContext, uri: str) -> dict[str, Any] | None:
    from venona.api.mcp.schemas import BriefsArgs, OntologyArgs
    from venona.api.mcp.tools import tool_briefs, tool_ontology

    if uri == "venona://ontology":
        body = tool_ontology(ctx, OntologyArgs())
    elif uri == "venona://brief/latest":
        try:
            body = tool_briefs(ctx, BriefsArgs(brief_id="latest"))
        except ToolError as exc:
            body = str(exc)
    else:
        return None
    return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": body}]}


async def handle(
    body: dict[str, Any], *, ctx: ToolContext, version_header: str | None
) -> dict[str, Any] | None:
    """One request in, one response out. `None` means "notification, send 202"."""
    request_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    if body.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return protocol.error(request_id, protocol.INVALID_REQUEST, "not a JSON-RPC 2.0 request")

    # A notification has no id and gets no response body at all.
    if request_id is None:
        return None

    requested = protocol.requested_version(body, version_header)
    if requested is not None and requested not in protocol.SUPPORTED:
        return protocol.unsupported_version(request_id, requested)
    negotiated = requested if requested in protocol.SUPPORTED else protocol.LATEST

    match method:
        # ── modern (2026-07-28): no handshake, a mandatory discovery RPC ──
        case "server/discover":
            return protocol.result(
                request_id,
                protocol.cacheable(
                    {
                        "supportedVersions": list(protocol.SUPPORTED),
                        "capabilities": protocol.capabilities(),
                        "serverInfo": protocol.server_info(),
                        "instructions": protocol.instructions(),
                    }
                ),
            )

        # ── legacy (2025-11-25 and earlier): the handshake ──
        case "initialize":
            return protocol.result(
                request_id,
                {
                    "protocolVersion": negotiated,
                    "capabilities": protocol.capabilities(),
                    "serverInfo": protocol.server_info(),
                    "instructions": protocol.instructions(),
                },
            )
        case "ping":
            # Removed in 2026-07-28, answered anyway: a legacy client that pings
            # a server which 404s the method decides the server is broken.
            return protocol.result(request_id, {})

        case "tools/list":
            # Deterministic order, which the spec asks for: it lets a client
            # cache the list and improves prompt-cache hit rates upstream.
            return protocol.result(
                request_id,
                protocol.cacheable(
                    {"tools": [registry.TOOLS[name].as_wire() for name in sorted(registry.TOOLS)]}
                ),
            )
        case "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                return protocol.error(
                    request_id, protocol.INVALID_PARAMS, "`name` is required and must be a string"
                )
            payload = await _call_tool(ctx, name, params.get("arguments") or {})
            payload["resultType"] = "complete"
            return protocol.result(request_id, payload)

        case "resources/list":
            return protocol.result(
                request_id, protocol.cacheable({"resources": registry.RESOURCES})
            )
        case "resources/read":
            uri = params.get("uri")
            contents = (
                await run_in_threadpool(_read_resource, ctx, uri) if isinstance(uri, str) else None
            )
            if contents is None:
                return protocol.error(request_id, protocol.INVALID_PARAMS, f"no resource `{uri}`")
            return protocol.result(request_id, protocol.cacheable(contents))

        case "prompts/list":
            return protocol.result(request_id, protocol.cacheable({"prompts": registry.PROMPTS}))
        case "prompts/get":
            if params.get("name") != "check_draft":
                return protocol.error(
                    request_id, protocol.INVALID_PARAMS, f"no prompt `{params.get('name')}`"
                )
            draft = (params.get("arguments") or {}).get("draft") or ""
            return protocol.result(
                request_id,
                {
                    "description": "Verify every name, acronym and relationship claim.",
                    "messages": [
                        {
                            "role": "user",
                            "content": {
                                "type": "text",
                                "text": registry.check_draft_instructions() + draft,
                            },
                        }
                    ],
                },
            )

    return protocol.error(request_id, protocol.METHOD_NOT_FOUND, f"no method `{method}`")
