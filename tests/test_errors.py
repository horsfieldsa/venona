"""The error envelope, and the failures that used to be 500s.

A 500 says "this app is broken". For a graph read with Neo4j down that is
actively wrong -- Neo4j is a rebuildable projection (invariant 1), the brief and
curation are unaffected, and it will be back. The frontend rendered the
difference as the literal string `500: Internal Server Error` across the graph
canvas.
"""

from __future__ import annotations

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from neo4j.exceptions import ServiceUnavailable
from venona.api import errors


@pytest.fixture
def app() -> FastAPI:
    """A bare app, so these test the handlers rather than the whole stack."""
    application = FastAPI()
    application.add_middleware(errors.RequestIdMiddleware)
    errors.install(application)

    @application.get("/boom/graph")
    async def _graph():
        raise ServiceUnavailable("Unable to retrieve routing information")

    @application.get("/boom/locked")
    async def _locked():
        raise psycopg.OperationalError("database is locked")

    @application.get("/boom/other")
    async def _other():
        raise ValueError("a detail nobody outside should read")

    @application.get("/fine")
    async def _fine():
        return {"ok": True}

    return application


@pytest.fixture
def edge_app() -> FastAPI:
    """The hosted posture: this process IS the public edge, so the unhandled-500
    backstop must carry HSTS and the CSP the app owns there."""
    application = FastAPI()
    application.add_middleware(errors.RequestIdMiddleware)
    errors.install(application, edge=True)

    @application.get("/boom/other")
    async def _other():
        raise ValueError("a detail nobody outside should read")

    return application


async def _get(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://t",
    ) as client:
        return await client.get(path)


async def test_a_graph_outage_is_503_not_500(app) -> None:
    response = await _get(app, "/boom/graph")

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "graph_unavailable"


async def test_the_graph_message_says_what_is_still_working(app) -> None:
    """Invariant 1 made useful to a reader: the projection being down does not
    mean the brief is down, and the message should stop them assuming it."""
    message = (await _get(app, "/boom/graph")).json()["detail"]["message"]

    assert "brief" in message.lower()
    assert "rebuild" in message.lower()


async def test_a_locked_database_is_503_and_retryable(app) -> None:
    response = await _get(app, "/boom/locked")

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "database_busy"


@pytest.mark.negative
async def test_an_unexpected_error_says_nothing_about_itself(app) -> None:
    """The backstop must not leak. An app on the internet does not tell a
    stranger which line of which file failed -- the traceback goes to the log
    with the request id, and the caller gets the id."""
    response = await _get(app, "/boom/other")
    body = response.text

    assert response.status_code == 500
    assert "a detail nobody outside should read" not in body
    assert "ValueError" not in body
    assert "Traceback" not in body


async def test_the_reference_in_the_message_is_the_request_id(app) -> None:
    """So a person can quote the thing they were shown and it finds the log
    line. A reference that appears nowhere else is decoration."""
    response = await _get(app, "/boom/other")

    rid = response.headers["x-request-id"]
    assert rid
    assert rid in response.json()["detail"]["message"]


async def test_every_response_carries_a_request_id(app) -> None:
    for path in ("/fine", "/boom/graph", "/boom/other"):
        response = await _get(app, path)
        assert response.headers.get("x-request-id"), f"{path} had no request id"


async def test_request_ids_are_not_reused(app) -> None:
    first = (await _get(app, "/fine")).headers["x-request-id"]
    second = (await _get(app, "/fine")).headers["x-request-id"]

    assert first != second


def test_the_envelope_matches_what_the_frontend_already_parses() -> None:
    """`web/src/api/client.ts::extractDetail` reads `detail.message` for an
    object detail, and `curation.py` has returned that shape for a recoverable
    409 since before this module existed. Generalising the shape that worked
    beats inventing a second one -- and means no frontend change was needed.
    """
    body = errors.envelope("some_code", "a sentence", "abc123", run_id="run:x")

    assert body["detail"]["error"] == "some_code"
    assert body["detail"]["message"] == "a sentence"
    assert body["detail"]["run_id"] == "run:x"
    assert body["request_id"] == "abc123"


async def test_the_backstop_500_is_not_the_one_response_that_ships_bare(app) -> None:
    """`ServerErrorMiddleware` sits outside the user stack, so this is the only
    response `SecurityHeadersMiddleware` never sees -- and the failure is
    invisible, because every other short-circuited response carries the set."""
    from venona.api.middleware import SecurityHeadersMiddleware

    response = await _get(app, "/boom/other")

    assert response.status_code == 500
    for key, value in SecurityHeadersMiddleware.HEADERS.items():
        assert response.headers[key.decode()] == value.decode()
    assert response.headers["cache-control"] == "no-store"


async def test_the_backstop_500_carries_the_edge_headers_on_the_edge(edge_app) -> None:
    """In production the app owns HSTS and the CSP, and the one response nobody
    planned for is exactly the one an anonymous caller can trigger. It must not be
    the hole in the header set."""
    from venona.api.middleware import SecurityHeadersMiddleware

    response = await _get(edge_app, "/boom/other")

    assert response.status_code == 500
    for key, value in SecurityHeadersMiddleware.EDGE_HEADERS.items():
        assert response.headers[key.decode()] == value.decode()


async def test_the_backstop_500_omits_the_edge_headers_off_edge(app) -> None:
    """Off-edge the app sits behind the reverse proxy and does not know its own scheme, so it
    must NOT emit HSTS or the CSP -- that is the reverse proxy's to own, and emitting HSTS over
    http on localhost pins the dev browser to https."""
    response = await _get(app, "/boom/other")

    assert "strict-transport-security" not in response.headers
    assert "content-security-policy" not in response.headers
