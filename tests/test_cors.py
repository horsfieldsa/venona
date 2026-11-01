"""Cross-origin access, which exists for exactly one caller: the extension.

The SPA has never needed CORS -- Vite proxies `/api` in development and the reverse proxy
serves both from one origin in production -- so this is the first client that is
not same-origin, and every test here is about a way it could silently not work.

The failure this is mostly written against: **a preflight that 401s.** A browser
sends `OPTIONS` with no credentials before any cross-origin request carrying an
`Authorization` header. `AuthMiddleware` is default-deny and `OPTIONS
/api/briefs` is not exempt, so CORS placed INSIDE it would never run -- and what
the extension author sees is "CORS error" with a perfectly healthy API behind
it. Nothing about that failure points at middleware ordering.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.middleware.cors import CORSMiddleware
from venona.api import security
from venona.api.middleware import AuthMiddleware, SecurityHeadersMiddleware

EXTENSION = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef"
TOKEN = "f" * 64


@pytest.fixture
def cors_env(monkeypatch, tmp_path):
    """Auth ON and one allowed origin -- production's shape, not localhost's."""
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", "operator@example.com")
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", TOKEN)
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VENONA_CORS_ORIGINS", EXTENSION)
    security.get_auth_config.cache_clear()
    yield
    security.get_auth_config.cache_clear()


def _app(origins: list[str]):
    """The real middleware stack, in the real order.

    Assembled here rather than importing `main.app` because the stack is built
    at import time from the environment, and what these tests are about IS the
    order. Any drift between this and `main.py` is caught by
    `test_the_stack_in_main_matches_this_order`.
    """
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/api/briefs")
    def briefs():
        return {"items": []}

    @app.get("/api/health/live")
    def live():
        return {"ok": True}

    app.add_middleware(AuthMiddleware)
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["authorization", "content-type"],
            max_age=600,
        )
    app.add_middleware(SecurityHeadersMiddleware)
    return app


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://venona.example.com",
    )


async def test_the_preflight_is_answered_without_credentials(cors_env) -> None:
    """THE regression. An unauthenticated `OPTIONS` must succeed.

    Before CORS existed this was a 405 (nothing handled `OPTIONS` at all), and
    with CORS placed inside the auth middleware it would be a 401. Both present
    to the extension as an unexplained "CORS error".
    """
    async with _client(_app([EXTENSION])) as client:
        response = await client.request(
            "OPTIONS",
            "/api/briefs",
            headers={
                "Origin": EXTENSION,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == EXTENSION
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


async def test_an_allowed_origin_can_read_a_response_with_a_bearer_token(cors_env) -> None:
    async with _client(_app([EXTENSION])) as client:
        response = await client.get(
            "/api/briefs",
            headers={"Origin": EXTENSION, "Authorization": f"Bearer {TOKEN}"},
        )

    assert response.status_code == 200
    # Without this header the browser discards a 200 the server was happy to
    # send, which is the shape the extension would have hit before CORS existed.
    assert response.headers["access-control-allow-origin"] == EXTENSION


async def test_cors_does_not_authenticate_anything(cors_env) -> None:
    """An allowed origin is not a credential.

    Easy to assume once preflight passes: the browser stops complaining, so the
    request must be getting through. It is not -- CORS decides who may READ a
    response, never who may make the request.
    """
    async with _client(_app([EXTENSION])) as client:
        response = await client.get("/api/briefs", headers={"Origin": EXTENSION})

    assert response.status_code == 401


async def test_a_disallowed_origin_gets_no_cors_headers(cors_env) -> None:
    async with _client(_app([EXTENSION])) as client:
        response = await client.get(
            "/api/briefs",
            headers={"Origin": "https://evil.example.com", "Authorization": f"Bearer {TOKEN}"},
        )

    # The request itself still SUCCEEDS -- CORS is enforced in the browser, not
    # the server, and a bearer token is a bearer token. What the origin does not
    # get is permission to read the answer.
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


async def test_credentials_are_never_allowed(cors_env) -> None:
    """The header that would let an allowed origin ride the session cookie.

    With `allow_credentials=True` a page on an allowed origin could make
    authenticated requests using the operator's ambient `venona_session` -- CSRF,
    with the allowlist as the only thing standing in the way. The extension does
    not need it: it sends a bearer token, which `principal_for` prefers anyway.
    """
    async with _client(_app([EXTENSION])) as client:
        preflight = await client.request(
            "OPTIONS",
            "/api/briefs",
            headers={
                "Origin": EXTENSION,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        simple = await client.get(
            "/api/briefs",
            headers={"Origin": EXTENSION, "Authorization": f"Bearer {TOKEN}"},
        )

    assert "access-control-allow-credentials" not in preflight.headers
    assert "access-control-allow-credentials" not in simple.headers


async def test_no_configured_origin_means_no_cors_at_all(cors_env) -> None:
    """The default, and it must stay the default.

    An app that grew a permissive CORS layer "so the extension works" would hand
    every page on the internet a reader for `/api`. Off unless named.
    """
    async with _client(_app([])) as client:
        response = await client.get(
            "/api/briefs",
            headers={"Origin": EXTENSION, "Authorization": f"Bearer {TOKEN}"},
        )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


async def test_a_preflight_still_carries_the_security_headers(cors_env) -> None:
    """CORS short-circuits the preflight, so it answers INSIDE SecurityHeaders
    or the one response an attacker most reliably reaches ships bare. Same
    reasoning as the 401 in `test_auth.py`."""
    async with _client(_app([EXTENSION])) as client:
        response = await client.request(
            "OPTIONS",
            "/api/briefs",
            headers={"Origin": EXTENSION, "Access-Control-Request-Method": "GET"},
        )

    assert response.headers["x-content-type-options"] == "nosniff"


# ── the allowlist itself ─────────────────────────────────────────────


def test_origins_are_read_from_the_environment_not_settings(monkeypatch) -> None:
    """`config/settings.yaml` is writable AND readable over HTTP, so an origin
    allowlist living there would be attacker-writable -- and adding your own
    origin to it is a complete read of the API from a page you control."""
    monkeypatch.setenv("VENONA_CORS_ORIGINS", f"{EXTENSION}, https://venona.example.com ")
    assert security.cors_origins() == (EXTENSION, "https://venona.example.com")

    from venona.settings import Settings

    fields = set(Settings.model_fields)
    assert not any("cors" in f.lower() for f in fields), (
        "a CORS allowlist on Settings is writable over PUT /api/config/settings"
    )


def test_the_default_is_empty(monkeypatch) -> None:
    monkeypatch.delenv("VENONA_CORS_ORIGINS", raising=False)
    assert security.cors_origins() == ()


@pytest.mark.negative
def test_a_wildcard_origin_is_refused_at_startup(monkeypatch) -> None:
    """`*` reads as harmless on a single-user app right up until someone pairs
    it with a cookie. Refused loudly rather than honoured quietly."""
    monkeypatch.setenv("VENONA_CORS_ORIGINS", "*")
    with pytest.raises(security.AuthNotConfigured) as exc:
        security.cors_origins()
    assert "*" in str(exc.value)

    monkeypatch.setenv("VENONA_CORS_ORIGINS", f"{EXTENSION},*")
    with pytest.raises(security.AuthNotConfigured):
        security.cors_origins()


def test_the_stack_in_main_matches_this_order() -> None:
    """`_app` above rebuilds the stack, so it could drift from the real one.

    What must hold in `main.py`: CORS is added AFTER AuthMiddleware (so it is
    outside it, and answers the preflight before auth can 401 it) and BEFORE
    SecurityHeaders (so it is inside that, and the preflight carries headers).
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "backend" / "venona" / "main.py").read_text()
    # Matched WITHOUT the closing paren: these three all take keyword arguments
    # now, and an exact-call match makes this test fail for adding one — which
    # says "the middleware order is wrong" about a change that did not touch it.
    auth_at = source.index("app.add_middleware(AuthMiddleware")
    cors_at = source.index("CORSMiddleware,")
    headers_at = source.index("app.add_middleware(SecurityHeadersMiddleware")

    assert auth_at < cors_at < headers_at, (
        "last added is OUTERMOST: CORS must come after AuthMiddleware and before "
        "SecurityHeadersMiddleware, or the preflight 401s or ships bare"
    )
