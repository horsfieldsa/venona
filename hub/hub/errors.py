"""One error envelope, request ids, the backstop, and who gets a page.

The instance's `venona.api.errors` shape, so anyone operating both reads one
format:

    {"detail": {"error": "<machine code>", "message": "<for a human>"},
     "request_id": "..."}

WHO GETS HTML. The hub answers browsers and machines on the same paths, and
until these handlers existed a person who mistyped a URL got that envelope
raw in a tab -- and so did a 405, a 422 from the access form's own length
limit, the 500 backstop and the 503 the database-down handler raises, which
is the one most likely to be seen when something is actually wrong. So the
reply is negotiated: `Accept: text/html` from outside the machine API gets a
page, everything else keeps the envelope byte for byte.

The machine API is `/admin/instances/*` and it is NOT negotiated, whatever it
asks for. It is bearer-gated and read by a script; an HTML body there would
be a parse error at the far end, phrased as a page. `middleware.py` draws the
same line from the other side and `tests/hub/test_ops.py` proves the two
agree.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from hub import pages
from hub.middleware import SecurityHeadersMiddleware

log = logging.getLogger("hub.api")

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
    hub owns HSTS and the CSP too, so those belong here or the 500 is the one hole
    in the header set."""
    source = dict(SecurityHeadersMiddleware.HEADERS)
    if edge:
        source.update(SecurityHeadersMiddleware.EDGE_HEADERS)
    headers = {key.decode(): value.decode() for key, value in source.items()}
    headers["cache-control"] = "no-store"
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    return headers


class RequestIdMiddleware:
    """Raw ASGI. Stamps every request with an id, puts it in `scope["state"]`
    for handlers, and echoes it on the response header."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = uuid.uuid4().hex[:12]
        scope["state"] = dict(scope.get("state") or {})
        scope["state"]["request_id"] = rid

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append((REQUEST_ID_HEADER.lower().encode(), rid.encode()))
            await send(message)

        await self.app(scope, receive, send_with_id)


#: What each status says to a person. The machine half of the envelope keeps
#: its own vocabulary; this is the reader's.
#:
#: 405 deliberately says "not found". Whether a path exists for a method it
#: refuses is not something an unauthenticated caller gets to learn, and the
#: hub is an identity provider.
#: `(tab title, message, heading)`. The heading is the sentence on the page and
#: the title is the label in the tab; where they are the same, the heading is
#: None.
PAGES: dict[int, tuple[str, str, str | None]] = {
    403: ("Not permitted", "This account cannot open that page.", None),
    404: (
        "Not found",
        "The link may be wrong, or the page may have moved. Nothing is broken, "
        "and the front page is one click away.",
        "There is nothing at this address.",
    ),
    405: (
        "Not found",
        "The link may be wrong, or the page may have moved. Nothing is broken, "
        "and the front page is one click away.",
        "There is nothing at this address.",
    ),
    409: (
        "Not right now",
        "That cannot be done in this account's current state.",
        None,
    ),
    422: ("Check that again", "One of the answers was not something this form accepts.", None),
    429: ("Slow down", "Too many requests. Try again in a minute.", None),
    500: ("Something went wrong", "Something went wrong on our side.", None),
    502: (
        "Not available right now",
        "Our payment provider could not complete that. Nothing has been charged.",
        None,
    ),
    503: ("Not available right now", "Venona cannot reach its records. Try again shortly.", None),
}

#: The machine half, per status. A caller can read the status line, so this
#: earns its place only by being stable and greppable in a log; `refused` is the
#: fallback for a status no route is expected to raise.
CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "not_found",
    409: "conflict",
    422: "invalid_request",
    429: "rate_limited",
}

#: Answers to a script, never to a browser. Kept as a prefix rather than a
#: route list because the whole point of `AdminBearerMiddleware` is that a new
#: path under here is born guarded; a new path must be born un-negotiated too.
MACHINE_PREFIX = "/admin/instances"


def wants_html(request: Request) -> bool:
    """Whether this caller is a person looking at a tab.

    `text/html` in `Accept` and nothing else: a browser sends it on a top-level
    navigation and `httpx`, `curl` and the poller do not. Deliberately not a
    user-agent check, which is a guess, and not a path check, because the same
    paths serve both.
    """
    if request.url.path.startswith(MACHINE_PREFIX):
        return False
    return "text/html" in request.headers.get("accept", "")


#: Statuses where the ROUTE's own sentence is the point, and the fixed copy
#: above is only a fallback.
#:
#: Narrow on purpose. 403 and 404 stay deliberately vague because this is an
#: identity provider and what exists is not something an unauthenticated caller
#: gets to learn. But "there is no subscription to change yet" is the whole
#: answer, and a reader who clicked Change plan and got "Something went wrong"
#: has been told nothing and has no idea it was not a fault.
#:
#: Nothing new is disclosed: `reply` already returns these details verbatim to
#: a JSON caller. This is the same sentence for the same person.
DETAILED: frozenset[int] = frozenset({409})


def _page(request: Request, status: int, rid: str, detail: str = "") -> HTMLResponse:
    title, message, heading = PAGES.get(status, PAGES[500])
    if status in DETAILED and detail:
        message = detail if detail.endswith(".") else f"{detail}."
        heading = None
    # The reference goes on the page for the two a reader is expected to quote
    # back. On a 404 it would be noise: nothing is wrong on our side and there
    # is nothing to look up.
    if status in (500, 503) and rid:
        message = f"{message} Reference: {rid}"
    return HTMLResponse(
        pages.error(title, message, status=status, heading=heading, theme=pages.theme_of(request)),
        status_code=status,
    )


def reply(request: Request, status: int, code: str, message: str, rid: str) -> Response:
    """One answer, in whichever of the two shapes the caller asked for."""
    if wants_html(request):
        response = _page(request, status, rid, detail=message)
        if rid:
            response.headers[REQUEST_ID_HEADER] = rid
        return response
    return JSONResponse(
        status_code=status,
        headers={REQUEST_ID_HEADER: rid} if rid else None,
        content=envelope(code, message, rid),
    )


def install(app: FastAPI, *, edge: bool = False) -> None:
    @app.exception_handler(psycopg.OperationalError)
    async def _database_down(request: Request, exc: psycopg.OperationalError) -> Response:
        rid = request_id_of(request)
        log.error("database error on %s %s [%s]: %s", request.method, request.url.path, rid, exc)
        return reply(request, 503, "database_unavailable", "The database is unavailable.", rid)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Response:
        """Deliberately says NOTHING about `exc` on the wire. The traceback
        goes to the log with the request id attached; the client gets the id
        and can quote it."""
        rid = request_id_of(request)
        log.exception("unhandled error on %s %s [%s]", request.method, request.url.path, rid)
        message = f"Something went wrong. Reference: {rid}" if rid else "Something went wrong."
        # `ServerErrorMiddleware` renders this one outside the user stack, so
        # `SecurityHeadersMiddleware` never sees it and the headers are applied
        # here -- to the page as much as to the envelope. A 500 that ships
        # without `X-Content-Type-Options` is the one response nobody planned
        # for shipping bare, and there is a test for exactly that.
        response = (
            _page(request, 500, rid)
            if wants_html(request)
            else JSONResponse(status_code=500, content=envelope("internal_error", message, rid))
        )
        response.headers.update(_backstop_headers(rid, edge=edge))
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _refused(request: Request, exc: StarletteHTTPException) -> Response:
        """Every deliberate refusal: 404, 405, 403, 429, and the two
        `HTTPException(404)`s `routers/ops.py` raises on a form POST from the
        operator page, which answered a browser with JSON until now.

        `ops.py` already renders an HTML 404 for a signed-in non-operator and
        its comment calls that "the same page a wrong URL gets". This is what
        makes the sentence true.
        """
        rid = request_id_of(request)
        detail = exc.detail if isinstance(exc.detail, str) else "Refused."
        code = CODES.get(exc.status_code, "refused")
        response = reply(request, exc.status_code, code, detail, rid)
        # A 401 carries the challenge or the browser cannot retry; the bearer
        # gate writes its own 401 at the ASGI level, but a router may raise one.
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def _malformed(request: Request, exc: RequestValidationError) -> Response:
        """422. The concrete case is a person, not a script: `/request-access`
        declares `Form(min_length=3, max_length=254)`, so an over-long address
        typed into the real form came back as FastAPI's error array."""
        rid = request_id_of(request)
        if wants_html(request):
            return _page(request, 422, rid)
        return JSONResponse(
            status_code=422,
            headers={REQUEST_ID_HEADER: rid} if rid else None,
            content=envelope(
                "invalid_request", "That request was not valid.", rid, errors=exc.errors()
            ),
        )
