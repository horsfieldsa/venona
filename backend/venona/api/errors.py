"""One error envelope, and handlers for the failures that would otherwise be 500s.

    {"detail": {"error": "<machine code>", "message": "<for a human>"},
     "request_id": "..."}

`web/src/api/client.ts::extractDetail` unpacks exactly this shape, and
`ApiError.body` keeps carrying the structured payload every recovery path
depends on.

WHY HANDLERS AND NOT try/except AT EACH SITE. Guarding Neo4j call sites one at a
time means the next route is written without one, and the symptom is a 500 that
the frontend renders literally as "500: Internal Server Error" on the graph
canvas. A handler covers every site that exists and every site that will exist.

`curation.py::_projection_state` must NOT be changed to match: it deliberately
degrades to "no pending work" rather than an error, because a graph we cannot
query is not evidence that curation is blocked.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from venona import logging as venona_logging
from venona.api.middleware import SecurityHeadersMiddleware

log = logging.getLogger("venona.api")

# The header a client (or the reverse proxy's access log) can correlate on.
REQUEST_ID_HEADER = "X-Request-ID"


def envelope(code: str, message: str, request_id: str = "", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"detail": {"error": code, "message": message, **extra}}
    if request_id:
        body["request_id"] = request_id
    return body


def request_id_of(request: Request) -> str:
    return getattr(request.state, "request_id", "") or ""


def _backstop_headers(request_id: str, *, edge: bool = False) -> dict[str, str]:
    """`ServerErrorMiddleware` renders the unhandled case OUTSIDE the user
    stack, where `SecurityHeadersMiddleware` cannot reach it -- so the one
    response nobody planned for is the one that would ship bare. On the edge the
    app owns HSTS and the CSP too, so those belong here as well or the 500 an
    anonymous caller can trigger is the one hole in the header set."""
    source = dict(SecurityHeadersMiddleware.HEADERS)
    if edge:
        source.update(SecurityHeadersMiddleware.EDGE_HEADERS)
    headers = {key.decode(): value.decode() for key, value in source.items()}
    headers["cache-control"] = "no-store"
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    return headers


class RequestIdMiddleware:
    """Raw ASGI, for the same reasons as `AuthMiddleware` — see `middleware.py`.

    Stamps every request with an id, puts it in `scope["state"]` so handlers and
    log records can reach it, and echoes it on the response. Without this, an
    unexpected failure produces an opaque `{"detail": "Internal Server Error"}`
    with nothing tying it to the traceback in the container's log.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = uuid.uuid4().hex[:12]
        scope["state"] = dict(scope.get("state") or {})
        scope["state"]["request_id"] = rid
        # Also into the context var, so every log line emitted while handling
        # this request carries the id without any call site passing it.
        token = venona_logging.request_id.set(rid)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append((REQUEST_ID_HEADER.lower().encode(), rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            venona_logging.request_id.reset(token)


def _graph_exception_types() -> tuple[type[BaseException], ...]:
    """Every neo4j driver error, without importing a private module path.

    `neo4j.exceptions.Neo4jError` covers server-side failures and
    `DriverError` covers "could not reach it at all" — which is the one that
    matters here, because that is what a stopped container looks like.
    """
    from neo4j.exceptions import DriverError, Neo4jError

    return (DriverError, Neo4jError)


def install(app: FastAPI, *, edge: bool = False) -> None:
    async def _graph_unavailable(request: Request, exc: BaseException) -> JSONResponse:
        """503, not 500.

        The distinction is the whole point: 500 says "this app is broken", and
        the frontend rendered it as `500: Internal Server Error` across the
        graph canvas. 503 with a code says "the projection is unreachable" —
        which is recoverable, expected during a Neo4j restart, and does not mean
        the brief is unavailable, because Neo4j holds nothing original
        (invariant 1).
        """
        rid = request_id_of(request)
        log.warning(
            "graph unavailable on %s %s [%s]: %s: %s",
            request.method,
            request.url.path,
            rid,
            type(exc).__name__,
            exc,
        )
        return JSONResponse(
            status_code=503,
            headers={REQUEST_ID_HEADER: rid} if rid else None,
            content=envelope(
                "graph_unavailable",
                "The graph projection is unavailable. The brief and curation are "
                "unaffected; run `venona graph rebuild` if it does not come back.",
                rid,
            ),
        )

    # Registered one class at a time: starlette asserts `issubclass` on the key,
    # so a tuple raises at construction rather than matching several types.
    for error_type in _graph_exception_types():
        app.add_exception_handler(error_type, _graph_unavailable)

    @app.exception_handler(psycopg.OperationalError)
    async def _database_busy(request: Request, exc: psycopg.OperationalError) -> JSONResponse:
        """Postgres is the system of record, so this one is genuinely serious --
        but "the database is locked" is transient and worth telling a client to
        retry, rather than presenting as a crash."""
        rid = request_id_of(request)
        log.error("database error on %s %s [%s]: %s", request.method, request.url.path, rid, exc)
        locked = "locked" in str(exc).lower() or "busy" in str(exc).lower()
        return JSONResponse(
            status_code=503 if locked else 500,
            headers={REQUEST_ID_HEADER: rid} if rid else None,
            content=envelope(
                "database_busy" if locked else "database_error",
                "The database is busy. Try again in a moment."
                if locked
                else "The database rejected that operation.",
                rid,
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """The backstop.

        Deliberately says NOTHING about `exc` on the wire. The traceback goes to
        the log with the request id attached; the client gets that id and can
        quote it. An app on the internet does not tell a stranger which line of
        which file failed.
        """
        rid = request_id_of(request)
        log.exception("unhandled error on %s %s [%s]", request.method, request.url.path, rid)
        return JSONResponse(
            status_code=500,
            headers=_backstop_headers(rid, edge=edge),
            content=envelope(
                "internal_error",
                f"Something went wrong. Reference: {rid}" if rid else "Something went wrong.",
                rid,
            ),
        )
