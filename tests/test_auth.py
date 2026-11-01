"""Authentication.

The app was written for one human on one machine and says so in three module
docstrings. Putting it on the internet makes every one of ~30 write endpoints
reachable -- six of which spend the operator's Claude subscription, one of which
wipes Neo4j, and one of which rewrites the config naming the binary the server
execs. Auth is what stands in front of all of it, so the tests that matter most
here are the ones that would still pass if it were quietly broken.
"""

from __future__ import annotations

import time

import httpx
import pytest
from venona.api import security
from venona.api.middleware import EXEMPT_PATHS

SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef"
TOKEN = "f" * 64
EMAIL = "operator@example.com"


@pytest.fixture
def auth_env(monkeypatch, tmp_path):
    """A fully configured, enabled auth setup."""
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", EMAIL)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", TOKEN)
    monkeypatch.setenv("VENONA_GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("VENONA_GOOGLE_CLIENT_SECRET", "client-secret")
    # With an OIDC login configured, the browser token path exists only under
    # this flag -- these tests exercise exactly the break-glass state.
    monkeypatch.setenv("VENONA_TOKEN_LOGIN", "1")
    monkeypatch.setenv("VENONA_PUBLIC_URL", "https://venona.example.com")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()
    yield security.get_auth_config()
    security.get_auth_config.cache_clear()


# ── session tokens ───────────────────────────────────────────────────


def test_a_session_round_trips(auth_env) -> None:
    now = time.time()
    token, expires = security.mint_session(auth_env, email=EMAIL, now=now)
    principal = security.verify_session(auth_env, token, now=now)

    assert principal is not None
    assert principal.email == EMAIL
    assert expires > now


@pytest.mark.negative
def test_a_tampered_signature_is_refused(auth_env) -> None:
    token, _ = security.mint_session(auth_env, email=EMAIL, now=time.time())
    payload, _, mac = token.partition(".")
    flipped = mac[:-1] + ("a" if mac[-1] != "a" else "b")

    assert security.verify_session(auth_env, f"{payload}.{flipped}", now=time.time()) is None


@pytest.mark.negative
def test_a_tampered_payload_is_refused(auth_env) -> None:
    """The interesting direction: rewriting `sub` to an address that IS on the
    allowlist still fails, because the MAC covers the payload."""
    import base64
    import json

    forged = (
        base64.urlsafe_b64encode(
            json.dumps({"sub": EMAIL, "iat": time.time(), "exp": time.time() + 999}).encode()
        )
        .decode()
        .rstrip("=")
    )
    token, _ = security.mint_session(auth_env, email=EMAIL, now=time.time())
    _, _, mac = token.partition(".")

    assert security.verify_session(auth_env, f"{forged}.{mac}", now=time.time()) is None


@pytest.mark.negative
def test_an_expired_session_is_refused(auth_env) -> None:
    long_ago = time.time() - auth_env.session_ttl - 10
    token, _ = security.mint_session(auth_env, email=EMAIL, now=long_ago)

    assert security.verify_session(auth_env, token, now=time.time()) is None


@pytest.mark.negative
def test_a_session_for_an_unlisted_address_is_refused(auth_env) -> None:
    """The allowlist is re-checked on every request, not only at login.
    Removing an address should end its sessions, not wait fourteen days."""
    token, _ = security.mint_session(auth_env, email="someone-else@example.com", now=time.time())

    assert security.verify_session(auth_env, token, now=time.time()) is None


def test_logout_revokes_sessions_and_survives_a_restart(auth_env) -> None:
    """A module-level epoch would start at zero on every process start, so a
    restart would silently un-revoke every cookie the last logout killed -- for
    the remainder of its fourteen-day life. That is the one thing revocation
    exists to do, so the epoch is persisted."""
    now = time.time()
    token, _ = security.mint_session(auth_env, email=EMAIL, now=now)
    assert security.verify_session(auth_env, token, now=now) is not None

    security.bump_epoch(auth_env, now=now + 1)
    assert security.verify_session(auth_env, token, now=now + 2) is None

    # "Restart": a brand new config object reading the same data dir.
    security.get_auth_config.cache_clear()
    restarted = security.get_auth_config()
    assert security.verify_session(restarted, token, now=now + 3) is None


# ── bearer ───────────────────────────────────────────────────────────


def test_the_api_token_is_accepted(auth_env) -> None:
    assert security.verify_bearer(auth_env, f"Bearer {TOKEN}") is not None


@pytest.mark.negative
def test_a_token_with_one_byte_flipped_is_refused(auth_env) -> None:
    wrong = TOKEN[:-1] + "e"
    assert security.verify_bearer(auth_env, f"Bearer {wrong}") is None


@pytest.mark.negative
def test_a_bare_token_without_the_bearer_scheme_is_refused(auth_env) -> None:
    assert security.verify_bearer(auth_env, TOKEN) is None


# ── configuration must fail closed ───────────────────────────────────
#
# Each of these sets VENONA_AUTH_ENABLED explicitly rather than relying on the
# default. `get_auth_config` loads `.env` into the environment (so that a
# developer's `VENONA_AUTH_ENABLED=0` is honoured by `make dev`), which means a
# test inheriting the default would assert against whatever that file happens
# to say. These test the ENABLED config; they should say so.


@pytest.mark.negative
def test_google_without_an_allowlist_refuses_to_start(monkeypatch, tmp_path) -> None:
    """THE fail-closed case. A Google OAuth client authenticates ANY Google
    account, so treating an empty allowlist as "allow everyone" would put the
    app on the internet behind a login page that looks exactly like security."""
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", "")
    monkeypatch.setenv("VENONA_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("VENONA_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()

    with pytest.raises(security.AuthNotConfigured, match="ALLOWED_EMAILS"):
        security.assert_usable(security.get_auth_config())
    security.get_auth_config.cache_clear()


@pytest.mark.negative
def test_a_short_session_secret_refuses_to_start(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", "tooshort")
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", TOKEN)
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()

    with pytest.raises(security.AuthNotConfigured, match="SESSION_SECRET"):
        security.assert_usable(security.get_auth_config())
    security.get_auth_config.cache_clear()


@pytest.mark.negative
def test_a_hand_typed_api_token_refuses_to_start(monkeypatch, tmp_path) -> None:
    """This credential is the break-glass path into a machine on the internet.
    Nothing else stops someone setting it to a word they remember."""
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", "hunter2")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()

    with pytest.raises(security.AuthNotConfigured, match="API_TOKEN"):
        security.assert_usable(security.get_auth_config())
    security.get_auth_config.cache_clear()


@pytest.mark.negative
def test_auth_enabled_with_no_way_in_refuses_to_start(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", "")
    monkeypatch.setenv("VENONA_GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()

    with pytest.raises(security.AuthNotConfigured, match="no way to log in"):
        security.assert_usable(security.get_auth_config())
    security.get_auth_config.cache_clear()


def test_the_redirect_uri_is_configured_never_derived(auth_env) -> None:
    """`request.url_for` renders http:// behind a TLS-terminating proxy and
    Google rejects the mismatch; deriving it from `Host` would take the value
    from the caller. Both are confusing and one is a vulnerability."""
    assert auth_env.redirect_uri == "https://venona.example.com/api/auth/callback"


# ── the middleware ───────────────────────────────────────────────────


def _app():
    from venona.main import app

    return app


async def _client(app) -> httpx.AsyncClient:
    """https, deliberately. The session cookie is minted `Secure`, and httpx --
    like a browser -- will not send a Secure cookie back over plain http. An
    http base URL here would make every cookie test pass for the wrong reason
    or fail for a confusing one."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t")


async def test_every_api_route_requires_auth(auth_env) -> None:
    """⭐ The guarantee. Not the middleware's existence -- this.

    Walks the real route table and asserts that everything under /api refuses
    an unauthenticated caller. A route added without auth cannot pass this,
    which is the property `Depends(require_auth)` could never give: there, a
    forgotten line looks exactly like a present one.
    """
    from venona.api import ratelimit
    from venona.api.middleware import AUTHORIZE_PATH

    app = _app()
    async with await _client(app) as client:
        checked = 0
        for path, operations in app.openapi()["paths"].items():
            if path in EXEMPT_PATHS:
                continue
            for method in operations:
                # THE SWEEP MUST NOT BE CAPPED BY A BUCKET. Rate limiting runs
                # BEFORE auth, so every operation below spends a token and `WRITE`
                # holds 60 a minute -- fewer than this table has write operations.
                # A 429 is still a refusal, so this never hid a hole; it would
                # simply have started failing on the next write route anybody
                # added, naming a bucket instead of the thing that broke. The
                # autouse reset in `conftest.py` is per TEST; this needs it per
                # REQUEST.
                ratelimit.reset()
                concrete = path.replace("{", "").replace("}", "")
                response = await client.request(method.upper(), concrete)
                # `GET /api/oauth/authorize` REFUSES BY REDIRECTING, and it is the
                # only route that does. An MCP client opens it verbatim out of the
                # RFC 8414 document, so a person arrives here with no cookie and
                # has to be sent to sign in rather than shown a JSON body (contract
                # C12 addendum). It is still guarded -- it is NOT in `EXEMPT_PATHS`
                # and it still mints nothing without a session -- so what this
                # sweep is really asserting is "answers no unauthenticated caller
                # on the merits", and 302-to-sign-in satisfies that. The target is
                # pinned so this cannot quietly become a redirect somewhere else.
                if method.upper() == "GET" and path == AUTHORIZE_PATH:
                    assert response.status_code == 302, "authorize must send a human to sign in"
                    assert response.headers["location"].startswith("/api/auth/login?next=")
                    checked += 1
                    continue
                assert response.status_code == 401, f"{method.upper()} {path} was not refused"
                checked += 1
        assert checked > 40, f"only swept {checked} operations -- did the table load?"


def test_the_exempt_set_is_exactly_what_is_documented() -> None:
    """Widening this must be a deliberate diff, reviewed against contract C12 --
    not something that happens because a route needed to work."""
    assert EXEMPT_PATHS == {
        "/api/health/live",
        "/api/auth/mode",
        "/api/auth/login",
        "/api/auth/callback",
        "/api/auth/token-login",
        # Added 2026-08-15 with `/request-access`. It is the only unauthenticated
        # WRITE in the app, so it carries two things nothing else here needs: its
        # own rate-limit bucket (`ratelimit.SIGNUP`, 3/hour per address plus a
        # global ceiling) and a response that is byte-identical whether or not a
        # row was written, so it cannot be used to enumerate who has an account.
        "/api/request-access",
        # Added 2026-08-17 with OAuth 2.1 for agents. Both are unauthenticated BY
        # SPECIFICATION and neither grants anything by itself:
        #
        #   register  RFC 7591. Hands back an identifier that can be used to ASK.
        #             The asking ends at a consent screen in front of a signed-in
        #             human, so what a hostile registration buys is a table row.
        #   token     RFC 6749 §4.1.3. The client is PUBLIC and has no secret; the
        #             exchange is authenticated by a code a human approved plus the
        #             PKCE verifier that binds it to the client that asked for it.
        #
        # `/api/oauth/authorize` is deliberately NOT exempt: it is the one step
        # that must have a person behind it, and a session is how we know.
        "/api/oauth/register",
        "/api/oauth/token",
    }


async def test_the_exempt_paths_are_actually_reachable(auth_env) -> None:
    """The other half. An exempt list nothing serves is a list of typos."""
    app = _app()
    async with await _client(app) as client:
        assert (await client.get("/api/health/live")).status_code == 200
        assert (await client.get("/api/auth/mode")).status_code == 200
        # The two OAuth paths are checked in `tests/test_oauth.py` instead: they
        # open a database connection through `Depends(get_db)`, and this file
        # deliberately builds the app with no `app.state.settings` so that the
        # whole auth boundary can be exercised without one.


async def test_a_rejection_carries_the_security_headers(auth_env) -> None:
    """The middleware-ordering test.

    `add_middleware` inserts at 0 and the stack builds over `reversed(...)`, so
    the LAST added is outermost. Get it backwards and SecurityHeaders sits
    inside Auth, every 401 and 429 ships bare -- and every other test still
    passes, because the ordering only shows on short-circuited responses.
    """
    app = _app()
    async with await _client(app) as client:
        response = await client.get("/api/briefs")

    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


async def test_a_bearer_token_gets_in(auth_env) -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.get(
            "/api/auth/session", headers={"Authorization": f"Bearer {TOKEN}"}
        )

    assert response.status_code == 200
    assert response.json()["via"] == "bearer"


async def test_the_session_carries_the_first_boot_gate(auth_env) -> None:
    """`setup_required` rides the one request the SPA answers before mount.

    The router's first-boot gate reads it from the session store, so first
    paint never waits on `/health` -- whose Neo4j and claude-CLI probes are
    the two slowest things the API can touch. This checkout is configured, so
    the field must be present and False; `setup_service`'s own tests cover
    the True side.
    """
    app = _app()
    async with await _client(app) as client:
        response = await client.get(
            "/api/auth/session", headers={"Authorization": f"Bearer {TOKEN}"}
        )

    assert response.status_code == 200
    assert response.json()["setup_required"] is False


async def test_token_login_mints_a_cookie_with_the_right_flags(auth_env) -> None:
    """`ui_smoke` cannot drive a Google consent screen, and neither can the
    operator on the day Google sign-in is broken."""
    app = _app()
    async with await _client(app) as client:
        response = await client.post("/api/auth/token-login", json={"token": TOKEN})

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "venona_session=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie.replace("samesite", "SameSite")


@pytest.mark.negative
async def test_token_login_refuses_a_wrong_token(auth_env) -> None:
    app = _app()
    async with await _client(app) as client:
        response = await client.post("/api/auth/token-login", json={"token": "nope"})

    assert response.status_code == 401


async def test_token_login_is_gated_once_oidc_is_configured(monkeypatch, tmp_path) -> None:
    """Without VENONA_TOKEN_LOGIN, an OIDC-configured server neither offers
    nor accepts the browser token path -- the operator report that led here
    was the live login page presenting the token form beside Google."""
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", EMAIL)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", TOKEN)
    monkeypatch.setenv("VENONA_GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("VENONA_GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("VENONA_TOKEN_LOGIN", "")
    monkeypatch.setenv("VENONA_PUBLIC_URL", "https://venona.example.com")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()
    try:
        app = _app()
        async with await _client(app) as client:
            mode = await client.get("/api/auth/mode")
            refused = await client.post("/api/auth/token-login", json={"token": TOKEN})
        assert mode.json()["token"] is False
        assert refused.status_code == 403
        assert refused.json()["detail"]["error"] == "token_login_disabled"
    finally:
        security.get_auth_config.cache_clear()


async def test_token_login_stays_open_while_it_is_the_only_way_in(monkeypatch, tmp_path) -> None:
    """A token-only instance (the pre-hub bootstrap) needs no flag: with no
    OIDC login configured, the token form IS the login page."""
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", EMAIL)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", TOKEN)
    monkeypatch.setenv("VENONA_GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("VENONA_GOOGLE_CLIENT_SECRET", "")
    monkeypatch.setenv("VENONA_TOKEN_LOGIN", "")
    monkeypatch.setenv("VENONA_PUBLIC_URL", "https://venona.example.com")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()
    try:
        app = _app()
        async with await _client(app) as client:
            mode = await client.get("/api/auth/mode")
            accepted = await client.post("/api/auth/token-login", json={"token": TOKEN})
        assert mode.json()["token"] is True
        assert accepted.status_code == 200
    finally:
        security.get_auth_config.cache_clear()


async def test_a_session_cookie_gets_in(auth_env) -> None:
    """Cookie auth is what makes SSE work: `EventSource` cannot set headers, so
    `/api/runs/{id}/stream` has no way to present a bearer token."""
    app = _app()
    async with await _client(app) as client:
        await client.post("/api/auth/token-login", json={"token": TOKEN})
        response = await client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["authenticated"] is True


@pytest.mark.negative
async def test_no_route_accepts_an_id_token(auth_env) -> None:
    """`oauth.py` skips id_token signature verification, which is sound ONLY
    because the token arrives over a direct authenticated channel from Google.
    The day a route accepts one from a client -- Google One Tap is the obvious
    way -- that reasoning collapses and this file becomes a total bypass.
    """
    app = _app()
    document = app.openapi()
    for path, operations in document["paths"].items():
        for method, spec in operations.items():
            names = {p.get("name") for p in spec.get("parameters", [])}
            assert "id_token" not in names, f"{method.upper()} {path} takes an id_token"

    schemas = document.get("components", {}).get("schemas", {})
    for name, schema in schemas.items():
        assert "id_token" not in (schema.get("properties") or {}), f"{name} carries an id_token"


# ── the disabled path ────────────────────────────────────────────────


async def test_auth_can_be_turned_off_for_localhost(monkeypatch, tmp_path) -> None:
    """`make dev` has to keep working. The startup log says loudly what this
    costs, and a self-hosted deployment never sets it."""
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "0")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()

    app = _app()
    async with await _client(app) as client:
        response = await client.get("/api/auth/mode")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    security.get_auth_config.cache_clear()


# ── the API's own documentation is not public ────────────────────────


async def test_the_docs_surface_refuses_an_unauthenticated_caller(auth_env) -> None:
    """The blind spot in the sweep above.

    `test_every_api_route_requires_auth` walks `app.openapi()["paths"]`, and
    FastAPI does not put `/api/docs`, `/api/redoc` or `/api/openapi.json` in
    there -- so the sweep has never covered the three routes that serve the
    document rather than the data.

    It is worth covering because of what the document is. Every route and model
    docstring becomes a `description` field, and 194KB of them ship as one JSON
    file: which endpoints exist, which are unauthenticated and why, that
    `/request-access` is byte-identical for a duplicate so it cannot be used to
    enumerate accounts, that a bearer must not fall through to the cookie. That
    is a reasonable thing to write down and not a reasonable thing to serve to
    the internet.

    Guarded by living under `GUARDED_PREFIX`. Moving any of them back to the
    root -- which is where FastAPI puts them by default -- takes them outside
    `AuthMiddleware` entirely, and nothing else would notice.
    """
    app = _app()
    async with await _client(app) as client:
        for path in ("/api/openapi.json", "/api/docs", "/api/redoc"):
            response = await client.get(path)
            assert response.status_code == 401, f"{path} was served to an anonymous caller"

        for path in ("/openapi.json", "/docs", "/redoc"):
            response = await client.get(path)
            assert response.status_code == 404, f"{path} escaped the /api guard"


def test_production_removes_the_docs_routes_entirely(monkeypatch) -> None:
    """Belt and braces, and the belt is the one that is easy to drop.

    a self-hosted deployment sets `VENONA_ENV: production`, which makes the three
    URLs `None` so the routes are never registered. Nothing else asserts that,
    so deleting that one line from one compose file would put the document back
    on a deployed instance with only the auth middleware in front of it -- and
    the test above would still pass.

    Reloads `venona.main` under the flag and reloads it back, because
    `_PRODUCTION` is read at import.
    """
    import importlib

    import venona.main

    monkeypatch.setenv("VENONA_ENV", "production")
    try:
        production = importlib.reload(venona.main)
        assert production.app.openapi_url is None
        assert production.app.docs_url is None
        assert production.app.redoc_url is None

        paths = {getattr(route, "path", None) for route in production.app.routes}
        assert "/api/openapi.json" not in paths
        assert "/api/docs" not in paths
    finally:
        monkeypatch.delenv("VENONA_ENV", raising=False)
        importlib.reload(venona.main)


async def test_a_bad_host_400_still_carries_the_security_headers(monkeypatch, tmp_path) -> None:
    """TrustedHostMiddleware must sit INSIDE SecurityHeaders, or its invalid-host
    400 -- the one an anonymous caller can trigger against the hosted edge --
    short-circuits outside the header stamper and ships bare. The same
    last-added-is-outermost trap as the 401 test, one rung further out.
    """
    import importlib

    import venona.main

    spa = tmp_path / "spa"
    spa.mkdir()
    (spa / "index.html").write_text("<!doctype html>")
    monkeypatch.setenv("VENONA_SPA_DIR", str(spa))  # makes this process the edge
    monkeypatch.setenv("VENONA_ALLOWED_HOSTS", "defense.venona.example")
    try:
        reloaded = importlib.reload(venona.main)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=reloaded.app), base_url="https://t"
        ) as client:
            response = await client.get("/", headers={"host": "attacker.example"})

        assert response.status_code == 400
        assert response.headers["x-content-type-options"] == "nosniff"
        # The edge owns HSTS and the CSP, and a short-circuit must still carry them.
        assert "strict-transport-security" in response.headers
        assert "content-security-policy" in response.headers
    finally:
        monkeypatch.delenv("VENONA_SPA_DIR", raising=False)
        monkeypatch.delenv("VENONA_ALLOWED_HOSTS", raising=False)
        importlib.reload(venona.main)
