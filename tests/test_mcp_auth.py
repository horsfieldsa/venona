"""An agent token reaches /api/mcp and nothing else.

This is the file the credential exists for. `tests/test_auth.py` proves that a
stranger reaches nothing; this proves that a holder of the WEAKEST credential
venona issues reaches exactly one path -- which is what makes it safe to paste
into an autonomous client that reads adversary-controllable trade press.

Style follows `test_auth.py`: the real `venona.main.app` with its real middleware
stack, because `tests/conftest.py::api_app` builds a bare FastAPI with none of it
and could not exercise a boundary at all.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.routing import Mount
from venona.api import security
from venona.api.middleware import EXEMPT_PATHS, MCP_PATHS


@pytest.fixture
def auth_env(monkeypatch, tmp_path, database_url: str):
    """Auth ON, with a root token and a database holding one agent token.

    `.env` sets VENONA_AUTH_ENABLED=0 for local development, so without this
    every assertion below would pass for the wrong reason -- the middleware
    returns before it ever looks at a credential.
    """
    from venona.db import core as db
    from venona.services import token_service

    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", "r" * 64)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", "operator@example.com")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()

    conn = db.connect(database_url)
    # The token's OWNER. An agent credential inherits this user's role, so the
    # sweep below asserts what a token belonging to an ADMIN cannot reach -- which
    # is the stronger claim: reach is a leash on the credential, not on the person.
    from venona.services import user_service

    owner = user_service.create(
        conn,
        user_id="user:operator",
        email="operator@example.com",
        role="admin",
        now="2026-08-16T00:00:00Z",
        allow_admin=True,
    )
    _row, secret = token_service.mint(
        conn,
        label="test agent",
        token_id="mcp_test",
        now="2026-08-16T00:00:00Z",
        user_id=owner.id,
    )
    conn.close()

    from venona.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("VENONA_DATABASE_URL", database_url)
    yield secret
    security.get_auth_config.cache_clear()
    get_settings.cache_clear()


def _app(database_url):
    """The real app, with the middleware told where the database is.

    `database_url` is passed at construction rather than read from `app.state`
    because `httpx.ASGITransport` never runs `lifespan`.
    """
    import importlib

    import venona.main as main

    importlib.reload(main)
    return main.app


@pytest.fixture
def client(auth_env, database_url: str, monkeypatch):
    from venona.api.middleware import AuthMiddleware

    monkeypatch.setattr(AuthMiddleware, "_database_url", database_url, raising=False)
    from venona.main import app
    from venona.settings import get_settings

    app.state.settings = get_settings()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://t",
    )


def test_the_mcp_path_set_is_exactly_what_is_documented() -> None:
    """The mirror of `test_the_exempt_set_is_exactly_what_is_documented`.

    EXEMPT_PATHS opens a path to every caller; MCP_PATHS closes every path to one
    caller. Widening either must be a deliberate diff reviewed against C12, not
    something that happens because a route needed to work.
    """
    assert MCP_PATHS == {"/api/mcp"}


def test_mcp_adds_nothing_to_the_exempt_set() -> None:
    """The headline property, asserted where an MCP author will read it.

    A remote MCP server authenticated by a bearer needs no unauthenticated route
    at all: `/api/mcp` itself is guarded, and always was.

    WHAT OAUTH ACTUALLY COST, now that it is built (2026-08-17), against the
    estimate this docstring used to carry. It predicted "five, two of them at the
    site root". The real number is FOUR:

        /api/oauth/register   RFC 7591. Grants nothing -- it hands back an
                              identifier that can be used to ASK.
        /api/oauth/token      RFC 6749 §4.1.3. A public client has no secret; the
                              exchange is authenticated by a human-approved code
                              plus its PKCE verifier.
        /.well-known/oauth-protected-resource        RFC 9728 fixes the path.
        /.well-known/oauth-authorization-server      RFC 8414 fixes the path.

    The two `.well-known` documents are outside `GUARDED_PREFIX` entirely, as
    predicted. The saving is `/api/oauth/authorize`, which the estimate assumed
    would have to be exempt and which is deliberately NOT: it is the one step that
    must have a person behind it, and a session is how we know there is one.
    """
    assert "/api/mcp" not in EXEMPT_PATHS
    assert len(EXEMPT_PATHS) == 8
    assert "/api/oauth/authorize" not in EXEMPT_PATHS


def test_the_mcp_endpoint_is_a_route_and_nothing_under_api_is_mounted() -> None:
    """Structural, and it holds for the next mount too, not just this one.

    A `Mount` contributes nothing to `app.openapi()["paths"]`, which is what
    `test_every_api_route_requires_auth` walks -- so mounting an MCP ASGI app
    would take the largest new attack surface in the app out of that sweep while
    it kept reporting "40+ operations checked". It also compiles to
    `^/api/mcp/(?P<path>.*)$` and 307s the bare path a client is given.
    """
    from venona.main import app

    assert "/api/mcp" in app.openapi()["paths"]
    mounted = [r.path for r in app.routes if isinstance(r, Mount) and r.path.startswith("/api")]
    assert not mounted, f"mounted under /api: {mounted}"


@pytest.mark.anyio
async def test_an_unauthenticated_call_is_refused(client) -> None:
    async with client as c:
        response = await c.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401


@pytest.mark.anyio
async def test_an_agent_token_reaches_the_mcp_endpoint(client, auth_env) -> None:
    async with client as c:
        response = await c.post(
            "/api/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"authorization": f"Bearer {auth_env}"},
        )
    assert response.status_code == 200, response.text


@pytest.mark.anyio
async def test_an_agent_token_reaches_nothing_but_the_mcp_path(client, auth_env) -> None:
    """⭐ The guarantee, as a sweep.

    Walks the real route table and asserts a `vmcp_` credential is refused
    everywhere else. This is what makes a route added next month safe without its
    author having heard of MCP: the credential is default-denied and opening a
    path is one line in a frozenset a test pins.
    """
    from venona.api import ratelimit
    from venona.main import app

    headers = {"authorization": f"Bearer {auth_env}"}
    checked = 0
    async with client as c:
        for path, operations in app.openapi()["paths"].items():
            if path in EXEMPT_PATHS or path in MCP_PATHS:
                continue
            for method in operations:
                # THE SWEEP MUST NOT BE CAPPED BY A BUCKET. Rate limiting runs
                # BEFORE auth, so every operation below spends a token, and
                # `WRITE` holds 60 a minute -- fewer than this table now has write
                # operations. Once the bucket empties, the rest of the sweep
                # collects 429s: refusals, but for a reason that has nothing to do
                # with the credential, which is the one thing this test is about.
                # The autouse reset in `conftest.py` is per TEST; this loop needs
                # it per REQUEST.
                ratelimit.reset()
                concrete = path.replace("{", "").replace("}", "")
                response = await c.request(method.upper(), concrete, headers=headers)
                assert response.status_code == 403, (
                    f"{method.upper()} {path} answered {response.status_code}, not 403"
                )
                assert response.json()["detail"]["error"] == "restricted_credential"
                checked += 1
    assert checked > 40, f"only swept {checked} operations -- did the table load?"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/runs"),  # the $10-and-forty-minutes case
        ("POST", "/api/data/purge"),
        ("POST", "/api/curation/rebuild"),
        ("GET", "/api/config/relevance_profile"),  # the exfiltration case
        ("POST", "/api/auth/logout"),
        ("GET", "/api/meta/mcp"),  # a token must not enumerate its siblings
    ],
)
async def test_named_endpoints_an_agent_must_not_reach(client, auth_env, method, path) -> None:
    """Named as well as swept.

    A sweep failure reports a count; these report the endpoint. `/api/config/...`
    is the one that matters most and reads as the least dramatic: the corpus is
    scraped public trade press, but `profile/relevance_profile.md` and the
    prompts are the operator's thesis.
    """
    async with client as c:
        response = await c.request(method, path, headers={"authorization": f"Bearer {auth_env}"})
    assert response.status_code == 403


@pytest.mark.anyio
async def test_an_agent_token_cannot_exchange_itself_for_a_session(client, auth_env) -> None:
    """⭐ The escalation path, and it is easy to miss.

    `/api/auth/token-login` is EXEMPT, so the reach check never runs on it. If
    `verify_bearer` accepted a `vmcp_` value the agent would walk out with an
    unrestricted session cookie and the leash would be gone. It is closed today
    only because `routers/auth.py` calls `verify_bearer` directly rather than
    `principal_for` -- a natural-looking refactor to the latter would open it, so
    this test lives here where an MCP author will read it.
    """
    async with client as c:
        response = await c.post("/api/auth/token-login", json={"token": auth_env})
    assert response.status_code == 401
    assert "venona_session" not in response.headers.get("set-cookie", "")


@pytest.mark.anyio
async def test_a_bad_agent_token_does_not_fall_through_to_the_session(client, auth_env) -> None:
    """⭐ A presented bearer IS the credential.

    `principal_for` used to be `verify_bearer(...) or verify_session(...)`, and
    with a second bearer class that `or` was a hole: a revoked or mistyped agent
    token presented FROM A BROWSER fell through to `venona_session` and
    authenticated as the operator, unrestricted. The connection test on
    /settings/agents does exactly that, and would have reported "connected" for
    every credential it was given.
    """
    from venona.api.security import get_auth_config, mint_session

    cfg = get_auth_config()
    cookie, _expires = mint_session(cfg, email="operator@example.com", now=1_800_000_000.0)
    async with client as c:
        c.cookies.set("venona_session", cookie)
        response = await c.get(
            "/api/data/stats",
            headers={"authorization": "Bearer vmcp_" + "0" * 64},
        )
    assert response.status_code == 401, (
        "a bad agent token fell through to the session cookie and was upgraded to "
        "unrestricted access"
    )


@pytest.mark.anyio
async def test_a_revoked_token_stops_working(client, auth_env, database_url: str) -> None:
    from venona.db import core as db
    from venona.services import token_service

    conn = db.connect(database_url)
    token_service.revoke(conn, "mcp_test", now="2026-08-16T01:00:00Z")
    conn.close()

    async with client as c:
        response = await c.post(
            "/api/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"authorization": f"Bearer {auth_env}"},
        )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_a_403_carries_the_security_headers(client, auth_env) -> None:
    """The middleware-ordering guarantee, for the NEW short-circuit.

    `SecurityHeadersMiddleware` must wrap `AuthMiddleware` or every rejection
    ships bare -- CLAUDE.md names this failure by name, and the existing test
    covers the 401. The reach check is a second way to short-circuit and so a
    second way to regress it.
    """
    async with client as c:
        response = await c.get("/api/data/stats", headers={"authorization": f"Bearer {auth_env}"})
    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.anyio
async def test_a_trailing_slash_is_not_a_security_error(client, auth_env) -> None:
    """A URL typo must not read as a blocked credential.

    Starlette's Router issues its slash redirect ITSELF, inside the app and
    therefore after this middleware -- so `/api/mcp/` arrives here unredirected,
    misses an exact-match MCP_PATHS, and would 403 with `restricted_credential`.
    That gets reported as "venona blocked my token" rather than "you typed an
    extra slash".
    """
    async with client as c:
        response = await c.post(
            "/api/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"authorization": f"Bearer {auth_env}"},
            follow_redirects=False,
        )
    assert response.status_code != 403
