"""Who gets a page and who gets the envelope.

Named `test_hub_errors` rather than `test_errors`: `tests/` has no
`__init__.py`, so pytest imports test modules by basename and a second
`test_errors` collides with `tests/test_errors.py` at COLLECTION time -- which
takes the whole suite down, not just this file. `test_hub_route_contracts.py`
next door carries the same prefix for the same reason.

The hub answers browsers and machines on the same paths. Before these handlers
existed every refusal was a `JSONResponse`, so a person who mistyped a URL read
`{"detail":"Not Found"}` in a tab -- and so did a 405, a 422 from the access
form's own length limit, the 500 backstop, and the 503 raised when Postgres is
unreachable, which is the one a person is most likely to be looking at.

The two halves are tested together on purpose. Negotiation that only ever gets
exercised from one side is negotiation that has silently stopped negotiating.
"""

from __future__ import annotations

import psycopg
import pytest

from hub import errors

HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
JSON = {"Accept": "application/json"}


@pytest.mark.anyio
async def test_a_browser_gets_a_page_where_a_script_gets_the_envelope(hub_client) -> None:
    page = await hub_client.get("/nonsense", headers=HTML)
    assert page.status_code == 404
    assert page.headers["content-type"].startswith("text/html")
    assert "There is nothing at this address." in page.text

    wire = await hub_client.get("/nonsense", headers=JSON)
    assert wire.status_code == 404
    assert wire.headers["content-type"].startswith("application/json")
    assert wire.json()["detail"]["error"] == "not_found"


@pytest.mark.anyio
async def test_a_caller_that_states_no_preference_keeps_the_envelope(hub_client) -> None:
    """`Accept` absent is a script that did not bother, never a browser: every
    browser sends `text/html` on a top-level navigation. The envelope is the
    safer default, because a client parsing JSON that receives a page fails at
    the parse rather than acting on a body it does not understand."""
    reply = await hub_client.get("/nonsense")
    assert reply.status_code == 404
    assert reply.headers["content-type"].startswith("application/json")


@pytest.mark.anyio
async def test_the_machine_api_is_never_negotiated(hub_client) -> None:
    """`/admin/instances/*` is bearer-gated and read by a script. An HTML body
    there is a parse error at the far end, phrased as a page -- and a browser
    `Accept` costs nothing to send, so asking politely must not change it."""
    for headers in (HTML, JSON, {}):
        reply = await hub_client.get("/admin/instances/nope", headers=headers)
        assert reply.headers["content-type"].startswith("application/json"), headers


@pytest.mark.anyio
async def test_the_error_page_follows_the_theme_cookie(hub_client) -> None:
    """The hub carries no script, so the palette is decided on the server. An
    error page that ignored the cookie would be the one page in the product
    that flips to the other theme."""

    async def html_tag(choice: str) -> str:
        hub_client.cookies.set("venona.theme", choice)
        body = (await hub_client.get("/nonsense", headers=HTML)).text
        # The OPENING TAG, not the document. The shell's own stylesheet carries
        # `:root[data-theme='light']` selectors, so a search over the whole page
        # matches whatever the cookie says and the test proves nothing.
        return body.split(">", 2)[1]

    assert 'data-theme="light"' in await html_tag("light")
    assert 'data-theme="dark"' in await html_tag("dark")
    # `system` is the ABSENCE of the attribute, not a third value.
    assert "data-theme" not in await html_tag("system")


@pytest.mark.anyio
async def test_the_error_page_carries_the_field(hub_client) -> None:
    """The drawing is generated into `hub/hub/field.py`. If the splice breaks,
    the page still renders and still reads correctly, which is exactly why
    nothing else would notice."""
    body = (await hub_client.get("/nonsense", headers=HTML)).text
    assert 'class="graph-field"' in body
    assert body.count('class="graph-field-node"') == 7
    assert "@keyframes graph-field-breathe" in body


@pytest.mark.anyio
async def test_every_public_page_carries_the_field(hub_client) -> None:
    """⭐ The hub is a Venona property and has to look like one. A visitor
    arrives here from example.com, where the field IS the page, and landed on a
    bare panel on a flat ground.

    It was off everywhere but the refusal pages, on the grounds that 5KB of
    markup is free on a page you hit by accident and not on the sign-in page
    you hit daily. Measured, it is 7.6KB raw and 1.65KB gzipped, taking the
    sign-in page from 5.2KB to 6.8KB over the wire -- small enough that
    looking like the same company is worth it. It stays OFF the operator
    pages, which are working surfaces with dense tables rather than a front
    door (docs/adr/0191).
    """
    assert "graph-field" in (await hub_client.get("/", headers=HTML)).text


def test_the_operator_surface_does_not_carry_the_field() -> None:
    """The other half of the rule above: a drawing behind a table of addresses
    competes with the thing being read.

    The operator surface is a bundle, so the check is on the document it is
    served from rather than on a render. An unbuilt checkout has nothing to
    assert about, and the shell is the only file that could carry the field.
    """
    from hub.routers.ops import SHELL

    if not SHELL.is_file():
        pytest.skip("the operator bundle is not built in this checkout")
    assert "graph-field" not in SHELL.read_text()


@pytest.mark.anyio
async def test_a_405_says_not_found(hub_client) -> None:
    """Whether a path exists for a method it refuses is not something an
    unauthenticated caller gets to learn from an identity provider."""
    reply = await hub_client.post("/healthz", headers=HTML)
    assert reply.status_code == 405
    assert "There is nothing at this address." in reply.text


@pytest.mark.anyio
async def test_an_over_long_address_gets_a_page_not_an_error_array(hub_client) -> None:
    """The concrete 422, and it is a person every time: `/request-access`
    declares `Form(min_length=3, max_length=254)` and the form is typed into."""
    reply = await hub_client.post(
        "/request-access", data={"email": "a" * 300, "note": ""}, headers=HTML
    )
    assert reply.status_code == 422
    assert reply.headers["content-type"].startswith("text/html")
    assert "Check that again" in reply.text


@pytest.mark.anyio
async def test_the_database_being_down_is_a_page_for_a_person(hub_client, hub_app) -> None:
    """The likeliest outage anyone sees, and it answered with a JSON blob."""

    @hub_app.get("/_boom_db")
    async def _boom() -> None:
        raise psycopg.OperationalError("connection refused")

    reply = await hub_client.get("/_boom_db", headers=HTML)
    assert reply.status_code == 503
    assert "Not available right now" in reply.text
    wire = await hub_client.get("/_boom_db", headers=JSON)
    assert wire.json()["detail"]["error"] == "database_unavailable"


@pytest.mark.anyio
async def test_the_500_backstop_keeps_its_headers_on_the_html_branch(hub_client, hub_app) -> None:
    """`ServerErrorMiddleware` renders this one OUTSIDE the user stack, where
    `SecurityHeadersMiddleware` cannot reach it, so the handler stamps the
    headers itself. Adding an HTML branch is exactly how that gets dropped for
    the one response nobody planned for."""

    @hub_app.get("/_boom")
    async def _boom() -> None:
        raise RuntimeError("nope")

    reply = await hub_client.get("/_boom", headers=HTML)
    assert reply.status_code == 500
    assert reply.headers["x-content-type-options"] == "nosniff"
    assert reply.headers["cache-control"] == "no-store"
    # The reference is on the page, or it cannot be quoted back.
    assert reply.headers["x-request-id"] in reply.text


def test_a_405_is_not_found_to_a_script_too() -> None:
    """The machine code follows the copy. Telling a script the method was wrong
    while telling a person the page does not exist would be the same disclosure
    the page is worded to avoid, one content type over."""
    assert errors.CODES[405] == errors.CODES[404] == "not_found"


def test_every_status_the_hub_refuses_with_has_words_for_it() -> None:
    """A status missing from the table falls back to the 500 copy, which would
    tell a reader something went wrong on our side when they had simply asked
    for a page that is not there."""
    assert set(errors.PAGES) >= {403, 404, 405, 422, 429, 500, 503}
    for status, (title, message, heading) in errors.PAGES.items():
        assert title and message, status
        assert not title.endswith("."), f"{status}: a title is not a sentence"
        assert message.endswith("."), f"{status}: a message is"
        # The heading IS a sentence -- it is the line on the page, not the
        # label in the tab -- or it is absent and the title stands in.
        assert heading is None or heading.endswith("."), f"{status}: a heading is a sentence"
