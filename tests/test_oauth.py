"""OAuth 2.1 for agents, end to end and at its edges.

The happy path is one test. The rest are refusals, because that is what OAuth 2.1
IS relative to 2.0 — a set of things a conforming server must not do — and every
one of them is a real, named attack rather than a hypothetical.

Style follows `tests/test_authz.py`: the real `venona.main.app` with its real
middleware stack, because `tests/conftest.py::api_app` builds a bare FastAPI with
no middleware and could not exercise a boundary.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
from venona.api import authz, security
from venona.services import oauth_service

REDIRECT = "http://127.0.0.1:33418/callback"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


class _NoGraph:
    class _Health:
        ok = False
        version = None
        edition = None
        error = "no driver in tests"

    async def health(self):
        return self._Health()

    @property
    def driver(self):
        raise AssertionError("this test serves no Neo4j")


class _NoLlm:
    pass


@pytest.fixture
def env(monkeypatch, tmp_path, database_url: str):
    """Auth ON, one admin, and the app pointed at this test's database."""
    from venona.db import core as db
    from venona.services import user_service

    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", "r" * 64)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", "admin@example.com")
    monkeypatch.setenv("VENONA_PUBLIC_URL", "https://venona.test")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VENONA_DATABASE_URL", database_url)
    security.get_auth_config.cache_clear()

    conn = db.connect(database_url)
    try:
        user_service.create(
            conn,
            user_id="user:admin",
            email="admin@example.com",
            role=authz.ADMIN,
            now="2026-08-17T00:00:00Z",
            allow_admin=True,
        )
    finally:
        conn.close()

    from venona.settings import get_settings

    get_settings.cache_clear()
    yield
    security.get_auth_config.cache_clear()
    get_settings.cache_clear()


def _client(database_url: str, monkeypatch, *, signed_in: bool = True) -> httpx.AsyncClient:
    import importlib

    from venona.api.middleware import AuthMiddleware
    from venona.settings import get_settings

    monkeypatch.setattr(AuthMiddleware, "_database_url", database_url, raising=False)
    import venona.main as main

    importlib.reload(main)
    main.app.state.settings = get_settings()
    main.app.state.graph = _NoGraph()
    main.app.state.llm = _NoLlm()

    cookies = {}
    if signed_in:
        cfg = security.get_auth_config()
        token, _ = security.mint_session(cfg, email="admin@example.com", now=1_800_000_000.0)
        cookies[security.COOKIE_NAME] = token
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
        base_url="https://venona.test",
        cookies=cookies,
    )


async def _register(client, name="Claude Code", uris=(REDIRECT,)) -> str:
    response = await client.post(
        "/api/oauth/register", json={"client_name": name, "redirect_uris": list(uris)}
    )
    assert response.status_code == 201, response.text
    return response.json()["client_id"]


async def _authorize(client, client_id: str, challenge: str, **over) -> httpx.Response:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": "mcp",
        "state": "st-1",
        **over,
    }
    return await client.get("/api/oauth/authorize", params=params)


async def _approve(
    client, client_id: str, challenge: str, decision="allow", **over
) -> httpx.Response:
    return await client.post(
        "/api/oauth/authorize",
        data={
            "decision": decision,
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "scope": "mcp",
            "state": "st-1",
            **over,
        },
    )


def _code_from(response: httpx.Response) -> str:
    assert response.status_code == 302, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["code"][0]


# ── discovery ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_discovery_is_reachable_without_a_credential(env, database_url, monkeypatch) -> None:
    """⭐ The whole point of RFC 9728: a client configured with nothing but the
    venona URL can find its way in.

    These two are the ONLY routes outside `/api` and therefore outside
    `AuthMiddleware`. Asserting they are public is that fact written down as an
    expectation rather than left as a gap in `test_every_api_route_requires_auth`,
    which sweeps `/api` and so never sees them.
    """
    async with _client(database_url, monkeypatch, signed_in=False) as client:
        resource = await client.get("/.well-known/oauth-protected-resource")
        assert resource.status_code == 200
        assert resource.json()["resource"] == "https://venona.test/api/mcp"
        assert resource.json()["authorization_servers"] == ["https://venona.test"]

        server = await client.get("/.well-known/oauth-authorization-server")
        assert server.status_code == 200
        meta = server.json()
        assert meta["token_endpoint"] == "https://venona.test/api/oauth/token"
        # 2.1, not 2.0: `plain` is absent, so a client library reading this will
        # not offer it.
        assert meta["code_challenge_methods_supported"] == ["S256"]
        assert "implicit" not in meta["grant_types_supported"]


@pytest.mark.anyio
async def test_an_unauthenticated_mcp_call_points_at_the_metadata(
    env, database_url, monkeypatch
) -> None:
    """The header that starts the flow. Without it an MCP client sees an ordinary
    401 and stops, and nothing else in this file is reachable."""
    async with _client(database_url, monkeypatch, signed_in=False) as client:
        response = await client.post("/api/mcp", json={})
        assert response.status_code == 401
        assert (
            'resource_metadata="https://venona.test/.well-known/oauth-protected-resource"'
            in response.headers["www-authenticate"]
        )


@pytest.mark.anyio
async def test_a_browser_401_does_not_start_an_oauth_flow(env, database_url, monkeypatch) -> None:
    """The header is on the MCP path ONLY. On a browser path it would invite a
    human-facing client to run OAuth instead of showing the login page."""
    async with _client(database_url, monkeypatch, signed_in=False) as client:
        response = await client.get("/api/briefs")
        assert response.status_code == 401
        assert "resource_metadata" not in response.headers.get("www-authenticate", "")


# ── the flow ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_whole_flow_mints_a_working_token(env, database_url, monkeypatch) -> None:
    """⭐ Register, approve, exchange, and use the result on /api/mcp."""
    verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)

        page = await _authorize(client, client_id, challenge)
        assert page.status_code == 200
        assert "Claude Code" in page.text
        # The destination is shown beside the name: the name is the easy thing to
        # lie about, the redirect is the thing with consequences.
        assert REDIRECT in page.text

        code = _code_from(await _approve(client, client_id, challenge))

        token = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert token.status_code == 200, token.text
        body = token.json()
        assert body["token_type"] == "Bearer"
        assert body["access_token"].startswith("vmcp_")
        # RFC 6749 §5.1: a token response must never be cached. `Cache-Control`
        # comes from `SecurityHeadersMiddleware`, which stamps it app-wide; the
        # route adds only the HTTP/1.0 half, because setting both produced a
        # literal `no-store, no-store`.
        assert "no-store" in token.headers["cache-control"]
        assert token.headers["pragma"] == "no-cache"
        # No refresh token, deliberately -- see `schemas/oauth.py::TokenResponse`.
        assert "refresh_token" not in body

        # It is an ORDINARY agent token: same verification path, same reach.
        live = await client.post(
            "/api/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"authorization": f"Bearer {body['access_token']}"},
        )
        assert live.status_code == 200, live.text

        # And admissible nowhere else.
        elsewhere = await client.post(
            "/api/runs", headers={"authorization": f"Bearer {body['access_token']}"}
        )
        assert elsewhere.status_code == 403


@pytest.mark.anyio
async def test_the_consent_screen_may_submit_onward_to_the_callback(
    env, database_url, monkeypatch
) -> None:
    """⭐ Chrome checks `form-action` against every hop of a form submission.

    This form posts same-origin and answers with a 302 to the client's callback,
    so the site-wide `form-action 'self'` blocks it -- and the console names the
    SAME-ORIGIN action URL as the violation, which sends anybody debugging it to
    the wrong place. It reads as "the Authorize button does nothing".

    The page therefore carries its own policy naming the destination it is about
    to send the browser to, and nothing wider: the origin is rebuilt from
    scheme, host and port so an attacker-supplied `redirect_uri` cannot smuggle
    a directive, and it is the same destination shown to the human below it.
    """
    _verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)
        page = await _authorize(client, client_id, challenge)
        assert page.status_code == 200, page.text

        policy = page.headers["content-security-policy"]
        assert "form-action 'self' http://127.0.0.1:33418" in policy
        # ONE policy. Two would be enforced as their intersection, which is the
        # original block wearing a different hat.
        assert len(page.headers.get_list("content-security-policy")) == 1
        # Widened in that one directive and nowhere else.
        assert "script-src 'self';" in policy
        assert "default-src 'self';" in policy

        # A page whose form goes nowhere useful gets no widening at all.
        elsewhere = await _register(client, name="x", uris=("https://ok.test/cb",))
        other = await _authorize(client, elsewhere, challenge, redirect_uri="https://ok.test/cb")
        assert "form-action 'self' https://ok.test" in other.headers["content-security-policy"]


@pytest.mark.anyio
async def test_a_client_may_ask_for_every_scope_the_discovery_document_lists(
    env, database_url, monkeypatch
) -> None:
    """⭐ `scope` is a SPACE-DELIMITED LIST (RFC 6749 §3.3).

    Read as one value, `"annotate mcp"` is an unknown scope and the whole
    connection fails at the consent screen. This is exactly how a client that
    reads `scopes_supported` off the discovery document asks -- the Claude Code
    SDK does -- so the refusal was unreachable by any other route and total for
    the clients it hit.
    """
    verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)

        page = await _authorize(client, client_id, challenge, scope="annotate mcp")
        assert page.status_code == 200, page.text
        # The screen has to describe everything it grants, not the first half.
        assert "look a name up" in page.text
        assert "pages you visit" in page.text

        code = _code_from(await _approve(client, client_id, challenge, scope="annotate mcp"))
        token = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert token.status_code == 200, token.text
        body = token.json()
        # RFC 6749 §5.1: what was GRANTED, normalised, so a client is never left
        # believing it holds something it does not.
        assert body["scope"] == "annotate mcp"

        # The reach is the UNION: both scopes' paths, and nothing else.
        auth = {"authorization": f"Bearer {body['access_token']}"}
        mcp = await client.post(
            "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=auth
        )
        assert mcp.status_code == 200, mcp.text
        assert (await client.get("/api/sources/sites", headers=auth)).status_code == 200
        assert (await client.post("/api/runs", headers=auth)).status_code == 403


@pytest.mark.anyio
async def test_a_scope_this_server_does_not_know_is_dropped_not_refused(
    env, database_url, monkeypatch
) -> None:
    """Unknown names are dropped and the rest is granted -- the RFC's other
    permitted answer, and the one that survives a client adding `offline_access`
    next year. Refusing happens only when NOTHING is recognised."""
    _verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)

        page = await _authorize(client, client_id, challenge, scope="mcp offline_access")
        assert page.status_code == 200, page.text

        # Refused by redirecting to the VALIDATED uri, which is what the client
        # is waiting for (RFC 6749 §4.1.2.1), not by an error page.
        nothing = await _authorize(client, client_id, challenge, scope="offline_access")
        assert nothing.status_code == 302
        assert "error=invalid_scope" in nothing.headers["location"]


@pytest.mark.anyio
async def test_an_annotate_pairing_mints_a_token_that_reaches_exactly_its_scope(
    env, database_url, monkeypatch
) -> None:
    """The browser extension's flow: a chromiumapp.org redirect and scope=annotate.

    The redirect URI is what `chrome.identity.getRedirectURL()` produces, and it
    must pass registration as an ordinary https URI — no allowlist entry, which
    is the property the extension's pairing depends on.
    """
    ext_redirect = "https://hjkknjcomgnlnnkhgcdenhdkejnjiphg.chromiumapp.org/"
    verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client, name="Venona browser extension", uris=(ext_redirect,))

        page = await _authorize(
            client, client_id, challenge, scope="annotate", redirect_uri=ext_redirect
        )
        assert page.status_code == 200, page.text
        # The reader's consent page speaks reader words: no path list, no CLI.
        assert "/api/annotate" not in page.text
        assert "venona mcp token revoke" not in page.text
        assert "Venona" in page.text

        approved = await _approve(
            client, client_id, challenge, scope="annotate", redirect_uri=ext_redirect
        )
        code = _code_from(approved)
        token = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": ext_redirect,
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert token.status_code == 200, token.text
        body = token.json()
        assert body["scope"] == "annotate"
        headers = {"authorization": f"Bearer {body['access_token']}"}

        # Inside the scope: the two calls the extension lives on.
        sites = await client.get("/api/sources/sites", headers=headers)
        assert sites.status_code == 200, sites.text
        annotated = await client.post(
            "/api/annotate",
            json={"blocks": [{"id": "b0", "text": "Nothing notable here."}]},
            headers=headers,
        )
        assert annotated.status_code == 200, annotated.text

        # Outside it: the gated features 403 as a scope refusal, not a 401 —
        # the signal the extension's capability detection branches on.
        for method, path in (
            ("GET", "/api/runs/run:nope"),
            # The extension's capability probe: 403 restricted_credential here
            # is what classifies a token as limited.
            ("GET", "/api/runs/types"),
            ("POST", "/api/entities/org:nope/define"),
        ):
            refused = await client.request(method, path, headers=headers)
            assert refused.status_code == 403, (path, refused.text)
            assert refused.json()["detail"]["error"] == "restricted_credential"

        # And a made-up bearer is nobody at all.
        garbage = await client.get(
            "/api/sources/sites", headers={"authorization": "Bearer vmcp_nope"}
        )
        assert garbage.status_code == 401


@pytest.mark.anyio
async def test_login_next_carries_an_authorize_url_intact() -> None:
    """The extension enters the flow at /api/auth/login?next=<authorize URL>.

    `_safe_next` is the gate: it must pass a same-site path WITH its query
    string through unchanged, or pairing would land on the app shell instead of
    the consent screen."""
    from venona.api.routers.auth import _safe_next

    target = (
        "/api/oauth/authorize?client_id=client%3Ax&redirect_uri="
        "https%3A%2F%2Fx.chromiumapp.org%2F&response_type=code"
        "&code_challenge=c&code_challenge_method=S256&scope=annotate&state=s"
    )
    assert _safe_next(target) == target
    # And the property that makes that safe to allow: nothing absolute or
    # protocol-relative survives.
    assert _safe_next("https://evil.example/api/oauth/authorize") == "/"
    assert _safe_next("//evil.example") == "/"


@pytest.mark.negative
async def test_a_backslash_cannot_smuggle_an_origin_past_the_next_check() -> None:
    """⭐ The one that gets through, because Python and browsers disagree about
    what a path separator is.

    `/\\evil.com` starts with a single `/` and does not start with `//`, so a
    check written in those terms admits it — and every major browser normalises
    the backslash to a slash before resolving, reading it as `//evil.com`, which
    is protocol-relative and lands on another origin. A parser-based check does
    not save you: `urlsplit` treats the backslash as an ordinary path character
    and reports an empty netloc, agreeing with Python and disagreeing with the
    browser that will actually follow the redirect.
    """
    from venona.api.routers.auth import _safe_next

    for hostile in (
        "/\\evil.example",
        "/\\\\evil.example",
        "/\\/evil.example",
        "/api\\@evil.example",
    ):
        assert _safe_next(hostile) == "/", hostile


@pytest.mark.negative
async def test_control_characters_cannot_smuggle_an_origin_either() -> None:
    """The same attack, differently spelled: a browser strips tab, newline and
    carriage return from a URL before parsing it, so a value that looks like a
    path to us is a different URL to the thing that follows it."""
    from venona.api.routers.auth import _safe_next

    for hostile in ("/\t/evil.example", "/\n//evil.example", "/\r\n/evil.example", "/\x00"):
        assert _safe_next(hostile) == "/", repr(hostile)


@pytest.mark.anyio
async def test_declining_returns_an_error_not_a_code(env, database_url, monkeypatch) -> None:
    _verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)
        response = await _approve(client, client_id, challenge, decision="deny")
        assert response.status_code == 302
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["error"] == ["access_denied"]
        assert "code" not in query
        # `state` survives, so the client can match the answer to its request.
        assert query["state"] == ["st-1"]


# ── the refusals ─────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.negative
async def test_a_code_cannot_be_used_twice(env, database_url, monkeypatch) -> None:
    """⭐ Single use. A code that survives its first redemption is a credential
    anyone who read a proxy log can replay."""
    verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)
        code = _code_from(await _approve(client, client_id, challenge))
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
        }
        assert (await client.post("/api/oauth/token", data=form)).status_code == 200
        second = await client.post("/api/oauth/token", data=form)
        assert second.status_code == 400
        assert second.json()["error"] == "invalid_grant"


@pytest.mark.anyio
@pytest.mark.negative
async def test_a_code_approved_before_a_suspension_cannot_be_exchanged_after_it(
    env, database_url, monkeypatch
) -> None:
    """⭐ The second credential that outlives the check that authorized it.

    `redeem` validates the code, the client, the redirect URI, the verifier and
    the expiry -- five facts about the REQUEST, none about the person. Approval
    and exchange are separated by a browser round trip, so "suspend this account"
    can land in between.

    Refusing at exchange is what keeps that from leaving a residue. The token
    would be refused on every use by `AuthMiddleware._verify_mcp`, which reads
    the owner's status -- so what minting produces is not access, it is a live
    `mcp_tokens` row that silently starts working again the day the account is
    reinstated. Nobody reinstating an account expects to be handing back a
    credential they never saw issued.

    The stream's half of this rule is `tests/test_revocation_reach.py`.
    """
    from venona.db import core as db
    from venona.services import token_service, user_service

    verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)
        code = _code_from(await _approve(client, client_id, challenge))

        conn = db.connect(database_url)
        try:
            with db.transaction(conn, what="test_suspend"):
                user_service.change_authority(
                    conn,
                    user_id="user:admin",
                    status=user_service.SUSPENDED,
                    actor=None,  # the CLI: `venona users suspend`, under pressure
                    event_id="uevent:suspend",
                    now="2026-08-17T00:01:00Z",
                    epoch=1_800_000_100.0,
                    force=True,  # a sole admin, which is the shape of an instance
                )

            response = await client.post(
                "/api/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT,
                    "client_id": client_id,
                    "code_verifier": verifier,
                },
            )
            assert response.status_code == 400
            assert response.json()["error"] == "invalid_grant"
            # Same sentence as every other refusal -- see
            # `test_every_refusal_says_the_same_thing`.
            assert "not usable" in response.json()["error_description"]

            assert token_service.listing(conn, include_revoked=True) == [], (
                "no row may be minted: it would be refused on every use and then "
                "start working again on reinstatement"
            )
        finally:
            conn.close()


@pytest.mark.anyio
@pytest.mark.negative
async def test_the_wrong_pkce_verifier_is_refused(env, database_url, monkeypatch) -> None:
    """⭐ THE attack PKCE exists to stop: a code stolen out of a redirect, on a
    machine where another app registered the same custom scheme, is useless
    without the verifier that never left the client that asked."""
    _verifier, challenge = _pkce()
    other_verifier, _ = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)
        code = _code_from(await _approve(client, client_id, challenge))
        response = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "client_id": client_id,
                "code_verifier": other_verifier,
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"


@pytest.mark.anyio
@pytest.mark.negative
async def test_plain_pkce_is_refused(env, database_url, monkeypatch) -> None:
    """RFC 7636 permits `plain`; OAuth 2.1 forbids it. With `plain` the verifier
    IS the challenge, so it rides the same redirect and protects nothing."""
    _verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)
        response = await _authorize(client, client_id, challenge, code_challenge_method="plain")
        assert response.status_code == 302
        assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == [
            "invalid_request"
        ]


@pytest.mark.anyio
@pytest.mark.negative
async def test_an_unregistered_redirect_is_answered_here_never_by_redirecting(
    env, database_url, monkeypatch
) -> None:
    """⭐ The open-redirector guard.

    Redirecting to an unvalidated URI is how an authorization server becomes an
    open redirector -- and it would hand `state` to whoever asked. So this failure,
    unlike every other one, must be answered on OUR page.
    """
    _verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)
        response = await _authorize(
            client, client_id, challenge, redirect_uri="https://evil.test/steal"
        )
        assert response.status_code == 400
        assert "location" not in response.headers


@pytest.mark.anyio
@pytest.mark.negative
async def test_a_code_is_bound_to_the_client_that_asked(env, database_url, monkeypatch) -> None:
    """A second registered client cannot redeem the first one's code."""
    verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        first = await _register(client, name="First")
        second = await _register(client, name="Second")
        code = _code_from(await _approve(client, first, challenge))
        response = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "client_id": second,
                "code_verifier": verifier,
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"


@pytest.mark.anyio
@pytest.mark.negative
async def test_authorize_sends_a_session_less_human_to_sign_in(
    env, database_url, monkeypatch
) -> None:
    """`/api/oauth/authorize` is deliberately NOT exempt: it is the one step that
    must have a person behind it, and a session is how we know there is one.

    What a session-less GET gets is a redirect to sign in, not a 401 body. An MCP
    client opens `authorization_endpoint` verbatim out of the RFC 8414 document
    and has no way to know venona wants it entered through
    `/api/auth/login?next=...` the way the extension does (ADR 0161) -- so
    without this, every operator whose browser has no live cookie met raw JSON
    where a sign-in should have been.

    THE QUERY MUST SURVIVE INTACT. `next` is what carries the client's own
    parameters across the sign-in, and losing one of them strands the flow at
    the far end with an error the client cannot explain.
    """
    _verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as signed_in:
        client_id = await _register(signed_in)
    async with _client(database_url, monkeypatch, signed_in=False) as stranger:
        response = await _authorize(stranger, client_id, challenge)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("/api/auth/login?next=")
    resumed = unquote(parse_qs(urlsplit(location).query)["next"][0])
    assert resumed.startswith("/api/oauth/authorize?")
    resumed_params = parse_qs(urlsplit(resumed).query)
    assert resumed_params["client_id"] == [client_id]
    assert resumed_params["code_challenge"] == [challenge]
    assert resumed_params["code_challenge_method"] == ["S256"]


@pytest.mark.anyio
@pytest.mark.negative
async def test_the_consent_decision_still_refuses_without_a_session(
    env, database_url, monkeypatch
) -> None:
    """The redirect above is GET-only, and this is the half that matters.

    `POST /api/oauth/authorize` is what mints a code. Redirecting it would drop
    the form, and exempting the path -- rather than special-casing the method --
    would have taken the middleware off the one request in the flow that turns a
    human's click into a credential.
    """
    async with _client(database_url, monkeypatch, signed_in=False) as stranger:
        response = await stranger.post(
            "/api/oauth/authorize",
            data={"decision": "allow", "client_id": "client:whatever", "redirect_uri": REDIRECT},
        )
    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.negative
@pytest.mark.parametrize(
    "uri",
    [
        "http://evil.test/cb",  # plain http off-loopback
        "http://127.0.0.1.evil.test/cb",  # loopback as a prefix of someone else's name
        "ftp://127.0.0.1/cb",
        "https://ok.test/cb#frag",  # a fragment cannot be in a redirect target
    ],
)
async def test_registration_refuses_an_unsafe_redirect(env, database_url, monkeypatch, uri) -> None:
    async with _client(database_url, monkeypatch) as client:
        response = await client.post(
            "/api/oauth/register", json={"client_name": "x", "redirect_uris": [uri]}
        )
        assert response.status_code == 400, uri
        assert response.json()["error"] == "invalid_redirect_uri"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "uri",
    ["http://127.0.0.1:9999/cb", "http://localhost:9999/cb", "http://[::1]:9999/cb"],
)
async def test_registration_accepts_every_spelling_of_loopback(
    env, database_url, monkeypatch, uri
) -> None:
    """A native client picks its own callback host, and refusing one spelling of
    the same machine fails the connection before a human ever sees consent."""
    async with _client(database_url, monkeypatch) as client:
        response = await client.post(
            "/api/oauth/register", json={"client_name": "x", "redirect_uris": [uri]}
        )
        assert response.status_code == 201, uri
        assert response.json()["redirect_uris"] == [uri]


@pytest.mark.anyio
@pytest.mark.negative
async def test_the_consent_screen_escapes_the_client_name(env, database_url, monkeypatch) -> None:
    """⭐ `client_name` arrives at an UNAUTHENTICATED endpoint and is rendered on
    the one page where a human grants access. If it can carry markup, the consent
    screen is whatever the client says it is."""
    _verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client, name="<script>alert(1)</script>")
        page = await _authorize(client, client_id, challenge)
        assert page.status_code == 200
        assert "<script>alert(1)</script>" not in page.text
        assert "&lt;script&gt;" in page.text


async def test_the_consent_screen_follows_the_shared_theme(env, database_url, monkeypatch) -> None:
    """The consent screen is server-rendered and carries no script, so its half
    of the shared choice is read from the cookie on the way out (docs/adr/0177).

    Without it this page is the one surface in the app that ignores the reader:
    every other one resolves `venona.theme` in a render-blocking boot script,
    and a consent screen that flips to the operating system's palette mid-flow
    reads as the app breaking at exactly the moment it asks for trust.
    """
    _verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client, name="Reader")

        client.cookies.set("venona.theme", "light")
        assert (
            '<html lang="en" data-theme="light">'
            in (await _authorize(client, client_id, challenge)).text
        )

        # System, and an unrecognised value, are the same answer: NO attribute.
        # The absence is what makes the page follow the operating system, and
        # there is no third rule for `data-theme="system"` to match.
        for stored in ("system", "purple", 'dark" onload="x'):
            client.cookies.set("venona.theme", stored)
            page = await _authorize(client, client_id, challenge)
            assert '<html lang="en">' in page.text, stored
            assert "data-theme" not in page.text.split("<head>")[0], stored


def test_the_consent_screen_carries_the_rules_the_attribute_needs() -> None:
    """Without them the attribute is inert and every `light-dark()` on the page
    keeps following the operating system -- which looks exactly like the cookie
    never having been read."""
    from venona.api.routers.oauth import _PAGE

    assert ":root[data-theme='light'] {{ color-scheme: light; }}" in _PAGE
    assert ":root[data-theme='dark'] {{ color-scheme: dark; }}" in _PAGE


# ── the service, without HTTP ────────────────────────────────────────


def test_an_expired_code_is_refused(conn, monkeypatch) -> None:
    from venona.services import user_service

    user_service.create(
        conn,
        user_id="user:x",
        email="x@example.test",
        role="admin",
        now="2026-08-17T00:00:00Z",
        allow_admin=True,
    )
    oauth_service.register(
        conn,
        client_id="client:x",
        client_name="x",
        redirect_uris=[REDIRECT],
        now="2026-08-17T00:00:00Z",
    )
    verifier, challenge = _pkce()
    code = oauth_service.new_code()
    oauth_service.issue_code(
        conn,
        code=code,
        client_id="client:x",
        user_id="user:x",
        redirect_uri=REDIRECT,
        code_challenge=challenge,
        scope="mcp",
        now="2026-08-17T00:00:00Z",
        expires_at="2026-08-17T00:02:00Z",
    )
    with pytest.raises(oauth_service.OAuthError) as raised:
        oauth_service.redeem(
            conn,
            code=code,
            client_id="client:x",
            redirect_uri=REDIRECT,
            code_verifier=verifier,
            # One second past expiry.
            now="2026-08-17T00:02:01Z",
        )
    assert raised.value.code == "invalid_grant"


def test_every_refusal_says_the_same_thing(conn) -> None:
    """Five different facts, one sentence. Telling an unknown code from a
    mismatched client from a wrong verifier is exactly what an attacker holding
    one of them wants; the operator gets the detail from the row and the log."""
    from venona.services import user_service

    user_service.create(
        conn,
        user_id="user:y",
        email="y@example.test",
        role="admin",
        now="2026-08-17T00:00:00Z",
        allow_admin=True,
    )
    oauth_service.register(
        conn,
        client_id="client:y",
        client_name="y",
        redirect_uris=[REDIRECT],
        now="2026-08-17T00:00:00Z",
    )
    verifier, challenge = _pkce()
    code = oauth_service.new_code()
    oauth_service.issue_code(
        conn,
        code=code,
        client_id="client:y",
        user_id="user:y",
        redirect_uri=REDIRECT,
        code_challenge=challenge,
        scope="mcp",
        now="2026-08-17T00:00:00Z",
        expires_at="2026-08-17T01:00:00Z",
    )

    messages = set()
    for kwargs in (
        {"code": "nope"},
        {"client_id": "client:other"},
        {"redirect_uri": "http://127.0.0.1:1/other"},
        {"code_verifier": "wrong"},
    ):
        base = {
            "code": code,
            "client_id": "client:y",
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
            "now": "2026-08-17T00:00:01Z",
        }
        with pytest.raises(oauth_service.OAuthError) as raised:
            oauth_service.redeem(conn, **{**base, **kwargs})
        messages.add(raised.value.message)
        assert raised.value.code == "invalid_grant"

    assert len(messages) == 1, f"refusals are distinguishable: {messages}"


# ── RFC 8707: the audience a token is good for ───────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(
    "spelling",
    [
        "https://venona.test/api/mcp",
        "https://venona.test/api/mcp/",  # one trailing slash, both valid URIs
        "HTTPS://VENONA.TEST/api/mcp",  # scheme and host are case-insensitive
    ],
)
async def test_the_canonical_resource_is_accepted_however_it_is_spelled(
    env, database_url, monkeypatch, spelling
) -> None:
    """MCP clients MUST send `resource`, so refusing a legal spelling of our own
    address refuses the flow itself.

    RFC 9728 §7.4 asks servers to be forgiving here and RFC 3986 §6.2.2.1 says
    which parts may differ: scheme and host fold, the PATH does not. The three
    below are the ones a real client actually produces.
    """
    verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)
        page = await _authorize(client, client_id, challenge, resource=spelling)
        assert page.status_code == 200, page.text
        code = _code_from(await _approve(client, client_id, challenge))
        token = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": spelling,
            },
        )
        assert token.status_code == 200, token.text


@pytest.mark.anyio
@pytest.mark.negative
async def test_another_servers_resource_is_refused_before_anybody_consents(
    env, database_url, monkeypatch
) -> None:
    """⭐ Refused at `authorize`, and that placement is the point.

    A client naming a resource this server does not serve is misconfigured, and
    the only honest moment to say so is BEFORE a human approves access that
    cannot be granted. Answered by redirecting, because the redirect URI is
    validated by then and `invalid_target` is what the client is waiting for
    (RFC 8707 §2); `state` rides along so the client can match the answer to its
    request.
    """
    _verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)
        response = await _authorize(
            client, client_id, challenge, resource="https://someone-else.test/api/mcp"
        )

    assert response.status_code == 302
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["error"] == ["invalid_target"]
    assert params["state"] == ["st-1"]


@pytest.mark.anyio
@pytest.mark.negative
async def test_the_exchange_refuses_a_resource_the_consent_was_not_for(
    env, database_url, monkeypatch
) -> None:
    """Checked AGAIN at the token endpoint, because the two requests are
    independent: nothing carries the earlier answer forward, so a client that
    named this server on the consent screen and a different one at the exchange
    must not walk away with a token that says otherwise."""
    verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)
        await _authorize(
            client,
            client_id,
            challenge,
            resource=oauth_service.canonical_resource("https://venona.test"),
        )
        code = _code_from(await _approve(client, client_id, challenge))
        token = await client.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": "https://someone-else.test/api/mcp",
            },
        )
    assert token.status_code == 400
    assert token.json()["error"] == "invalid_target"


@pytest.mark.anyio
async def test_an_absent_resource_still_works(env, database_url, monkeypatch) -> None:
    """The browser extension pairs through these same endpoints and sends none.

    RFC 8707 makes the parameter optional at the protocol level; it is MCP that
    requires its own clients to send one. Treating absence as a mismatch would
    have broken extension pairing (ADR 0161) to enforce a rule that is not this
    server's to enforce.
    """
    _verifier, challenge = _pkce()
    async with _client(database_url, monkeypatch) as client:
        client_id = await _register(client)
        assert (await _authorize(client, client_id, challenge)).status_code == 200


# ── the consent screen's borrowed palette ────────────────────────────


def test_the_consent_screen_copies_tokens_css_exactly() -> None:
    """⭐ The page hand-copies nine values out of `web/src/styles/tokens.css`.

    It has to: Vite hashes the built stylesheet's filename, and this screen must
    render on the day the SPA build is broken -- which is precisely when someone
    is wiring up a client. `hub/canvas/tokens.css` is the same
    deliberate copy for the same kind of reason (ADR 0146) and is guarded by
    `tests/test_marketing_tokens.py`; this is that guard, at the size this copy
    actually is.

    RESOLVED THROUGH THE PRIMITIVES, not compared as text. The page inlines
    literals because it has no cascade to resolve `var()` against, so reading
    `--surface-base: light-dark(var(--grey-075), var(--grey-950))` and following
    both rungs is the only comparison that would catch a retuned grey.
    """
    import re
    from pathlib import Path

    from venona.api.routers.oauth import _PAGE

    tokens = Path("web/src/styles/tokens.css").read_text()

    def declared(name: str) -> str:
        match = re.search(rf"^\s*{re.escape(name)}:\s*([^;]+);", tokens, re.MULTILINE)
        assert match, f"{name} is not declared in tokens.css"
        return match.group(1).strip()

    def resolved(name: str) -> str:
        """`--x: light-dark(var(--a), var(--b))` -> `light-dark(#aaa, #bbb)`."""
        value = declared(name)
        return re.sub(r"var\((--[\w-]+)\)", lambda m: declared(m.group(1)), value)

    for name in (
        "--surface-base",
        "--surface-raised",
        "--border-subtle",
        "--text-primary",
        "--text-body",
        "--text-tertiary",
        "--accent-primary",
        "--text-inverse",
    ):
        # `.format` doubles the braces in the template, so read the literal source.
        assert f"{name}: {resolved(name)};" in _PAGE, (
            f"the consent screen's {name} no longer matches tokens.css. "
            f"tokens.css says {resolved(name)}. Copy it across, or the one page "
            f"someone reaches while connecting a client is off-palette."
        )

    # And the theming mechanism itself, which is what makes the pairs above mean
    # anything: tokens.css carries ONE palette and lets `light-dark()` resolve.
    assert "color-scheme: light dark;" in _PAGE, (
        "the consent screen must follow the reader's system setting, like every "
        "other surface. Pinning it to one theme is a flash of the wrong colour "
        "on half the machines that reach it."
    )
