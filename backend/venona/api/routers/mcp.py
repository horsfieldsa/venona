"""The MCP endpoint. One route, and that is a security decision.

A MOUNT WAS REJECTED, and the reasons are measured rather than stylistic:

  * `app.mount("/api/mcp", asgi)` compiles to `^/api/mcp/(?P<path>.*)$`, so the
    bare `POST /api/mcp` -- which is the URL a client is given -- 307-redirects.
    Every JSON-RPC call would pay a redirect and a client that will not re-POST
    on a 307 fails outright.
  * A Mount contributes NOTHING to `app.openapi()["paths"]`, which is what
    `tests/test_auth.py::test_every_api_route_requires_auth` walks, and it is not
    an `APIRoute`, which is what `tests/test_route_contracts.py` walks. Both
    sweeps would pass over the largest new attack surface in the app and keep
    reporting "40+ operations checked". `AuthMiddleware` would still guard it --
    the prefix is `/api` either way -- but the test that PROVES so would have
    stopped covering it, silently.

As an ordinary route it is in the OpenAPI document, in both sweeps, and inside
the middleware stack, and `GET`/`DELETE` get a 405 with `Allow: POST` for free.
`tests/test_mcp_auth.py::test_nothing_under_api_is_mounted` keeps it that way.

AUTH IS NOT DONE HERE. `AuthMiddleware` is default-deny over `/api`, and it is
also what refuses a `vmcp_` credential on every path except this one (contract
C12, `MCP_PATHS`). A route that forgot to check something would still be safe,
which is the property the middleware exists to give.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response

from venona.api.deps import get_db
from venona.api.mcp import dispatch, protocol
from venona.api.mcp.tools import ToolContext
from venona.db import core as db

router = APIRouter(tags=["mcp"])


def _json(payload: dict, status: int = 200) -> Response:
    # `Response`, not a pydantic model. A JSON-RPC envelope has one wire shape
    # for every method and `result` is a union over all of them; a
    # `response_model` over that union would FILTER, and a field one method sets
    # that the union does not name would vanish from the wire silently. That is
    # the hazard CLAUDE.md warns about, so the per-method payloads are modelled
    # in `api/mcp/` and asserted end to end in `tests/test_mcp_protocol.py`
    # instead. Recorded in `test_route_contracts.py`'s EXEMPT table.
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        status_code=status,
    )


@router.post("/mcp")
async def mcp_endpoint(request: Request, conn: db.Conn = Depends(get_db)) -> Response:
    """Every MCP message. One request in, one response out.

    `async def` with the blocking tools pushed to a threadpool by the dispatcher
    -- the reasoning `routers/annotate.py` gives for being a sync `def` applies
    one layer up: `build_index` is ~84ms of CPU and stalling the event loop
    stalls the `/api/health` the frontend polls every three seconds during a run.
    """
    raw = await request.body()
    if len(raw) > protocol.MAX_REQUEST_BYTES:
        return _json(
            protocol.error(
                None,
                protocol.INVALID_REQUEST,
                f"request body exceeds {protocol.MAX_REQUEST_BYTES} bytes",
            ),
            status=413,
        )

    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json(protocol.error(None, protocol.PARSE_ERROR, "invalid JSON"), status=400)

    if isinstance(body, list):
        # Batching was removed in the 2025-06-18 revision. Refusing it explicitly
        # also keeps the rate-limit bucket exact: without this, one HTTP request
        # could be five hundred tool calls charged as a single token.
        return _json(
            protocol.error(
                None,
                protocol.INVALID_REQUEST,
                "JSON-RPC batching is not supported; send one request per POST",
            ),
            status=400,
        )
    if not isinstance(body, dict):
        return _json(
            protocol.error(None, protocol.INVALID_REQUEST, "expected a JSON object"), status=400
        )

    ctx = ToolContext(conn=conn, graph=getattr(request.app.state, "graph", None))
    response = await dispatch.handle(
        body, ctx=ctx, version_header=request.headers.get("mcp-protocol-version")
    )
    if response is None:
        # A notification. No body, and 202 rather than 200 so a client can tell
        # "accepted, nothing to say" from "here is your result".
        return Response(status_code=202)
    return _json(response)
