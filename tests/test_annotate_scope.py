"""The browser extension's credential, and what it cannot do. Contract C12.

WHAT THIS REPLACED. The extension held the ROOT token — `VENONA_AUTH_API_TOKEN`,
pasted into its options page, which is what the options page and the RUNBOOK both
told you to do. That credential can start a research run, spend the subscription,
rewrite the relevance profile and purge the corpus. It was defensible for one
operator on one machine, and it stopped being defensible the moment there were
readers: a browser profile is not a place to keep the key to everything.

The `annotate` scope existed before this file and nothing used it. What was
missing was (a) the instruction pointing at it and (b) a test proving the leash
holds, without which "scoped" is a word in a docstring.

WHY THE OWNER IS AN ADMIN HERE, deliberately. The strong claim is not "a reader's
token cannot write" — it is that a token belonging to someone allowed to do
everything still reaches four paths. Reach is a property of the credential, not of
the person, and `api/authz.py` checks it pre-routing, before any role is
consulted. If this test's owner were a reader, a passing run would prove only that
role enforcement works, which `tests/test_authz.py` already covers.
"""

from __future__ import annotations

import httpx
import pytest
from venona.api import security
from venona.api.authz import ANNOTATE_REACH, SCOPES
from venona.api.middleware import EXEMPT_PATHS


@pytest.fixture
def annotate_token(monkeypatch, tmp_path, database_url: str):
    """An `annotate`-scoped token owned by an admin. See the module docstring.

    `.env` sets `VENONA_AUTH_ENABLED=0` for local development, so without turning
    auth on every assertion here would pass for the wrong reason: the middleware
    returns before it looks at a credential at all.
    """
    from venona.db import core as db
    from venona.services import token_service, user_service

    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", "r" * 64)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", "operator@example.com")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()

    conn = db.connect(database_url)
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
        label="extension on the laptop",
        token_id="ext_test",
        now="2026-08-16T00:00:00Z",
        user_id=owner.id,
        scope="annotate",
    )
    conn.close()

    from venona.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("VENONA_DATABASE_URL", database_url)
    yield secret
    security.get_auth_config.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def client(annotate_token, database_url: str, monkeypatch):
    import importlib

    import venona.main as main
    from venona.api.middleware import AuthMiddleware

    importlib.reload(main)
    monkeypatch.setattr(AuthMiddleware, "_database_url", database_url, raising=False)
    from venona.settings import get_settings

    main.app.state.settings = get_settings()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
        base_url="https://t",
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestTheScopeIsFourPaths:
    def test_the_scope_table_has_not_quietly_grown(self) -> None:
        """A path added to `ANNOTATE_REACH` is a path in every browser profile
        holding one of these tokens, so the set is asserted rather than described.
        """
        assert ANNOTATE_REACH == {
            "/api/annotate",
            "/api/entities/cards:batch",
            "/api/sources/sites",
            "/api/health/live",
        }
        assert SCOPES["annotate"] == ANNOTATE_REACH

    async def test_it_reaches_annotate(self, client, annotate_token) -> None:
        """The whole point of the credential."""
        async with client:
            response = await client.post(
                "/api/annotate",
                json={"blocks": [{"id": "b1", "text": "The Army selected MOSA."}]},
                headers=_auth(annotate_token),
            )
        assert response.status_code == 200, response.text

    async def test_it_reaches_the_card_batch(self, client, annotate_token) -> None:
        """The hover card, which is the other half of what the extension renders."""
        async with client:
            response = await client.post(
                "/api/entities/cards:batch",
                json={"ids": ["tech:modular-open-systems-approach"]},
                headers=_auth(annotate_token),
            )
        assert response.status_code == 200, response.text

    async def test_it_reaches_the_site_list_and_liveness(self, client, annotate_token) -> None:
        async with client:
            for path in ("/api/sources/sites", "/api/health/live"):
                response = await client.get(path, headers=_auth(annotate_token))
                assert response.status_code == 200, f"{path}: {response.text}"


class TestItReachesNothingElse:
    #: One per group of the C12 write matrix, plus the two reads that would be a
    #: corpus disclosure. Not exhaustive by design -- `test_every_reachable_path_
    #: is_in_the_scope` below is the exhaustive one; these name the consequences.
    REFUSED = (
        ("POST", "/api/runs"),  # spend
        ("POST", "/api/data/purge"),  # destructive
        ("POST", "/api/curation/merges"),  # a verdict
        ("PUT", "/api/config/settings"),  # the profile
        ("POST", "/api/glossary/lookup-all"),  # spend, unbounded
        ("POST", "/api/curation/rebuild"),
        ("GET", "/api/briefs"),  # the corpus itself
        ("GET", "/api/mcp"),  # the other scope's endpoint
    )

    @pytest.mark.negative
    @pytest.mark.parametrize(("method", "path"), REFUSED)
    async def test_it_is_refused(self, client, annotate_token, method: str, path: str) -> None:
        async with client:
            response = await client.request(method, path, json={}, headers=_auth(annotate_token))
        assert response.status_code == 403, (
            f"{method} {path} answered {response.status_code}, not 403 -- "
            "this token sits in a browser profile"
        )

    @pytest.mark.negative
    async def test_the_refusal_says_insufficient_scope(self, client, annotate_token) -> None:
        """RFC 6750 §3.1. An agent that gets `invalid_token` retries with the same
        credential forever; `insufficient_scope` tells it the credential is wrong
        rather than stale."""
        async with client:
            response = await client.get("/api/briefs", headers=_auth(annotate_token))
        assert 'error="insufficient_scope"' in response.headers.get("WWW-Authenticate", "")

    @pytest.mark.negative
    async def test_a_403_still_carries_the_security_headers(self, client, annotate_token) -> None:
        """Mirrors the existing 401 test, for the reason CLAUDE.md gives: Starlette
        builds the stack over `reversed(...)`, so the LAST middleware added is the
        OUTERMOST, and getting it backwards ships every short-circuited response
        without headers while every unit test still passes."""
        async with client:
            response = await client.get("/api/briefs", headers=_auth(annotate_token))
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    @pytest.mark.negative
    async def test_it_cannot_be_exchanged_for_a_session(self, client, annotate_token) -> None:
        """THE ESCALATION SEAM, closed deliberately. C12 recorded it as closed only
        incidentally.

        `POST /api/auth/token-login` swaps a bearer for a session COOKIE, and a
        session is unscoped -- it reaches every route the owner's role allows. This
        token's owner is an admin, so a success here would turn a four-path
        credential in a browser profile into the entire write matrix.

        It was refused only because `token_login` calls `verify_bearer` rather than
        `principal_for`; consolidating the two would have reopened it with every
        test still green. `verify_bearer` now rejects the `vmcp_` prefix outright.
        """
        async with client:
            response = await client.post("/api/auth/token-login", json={"token": annotate_token})
        assert response.status_code == 401, response.text
        assert "venona_session" not in response.headers.get("set-cookie", "")

    async def test_every_reachable_path_is_in_the_scope(self, client, annotate_token) -> None:
        """THE EXHAUSTIVE HALF, so a route added later is refused by default.

        Walks every route the app mounts and asserts that anything answering
        something other than 403 is a path the scope actually names. A new endpoint
        is then covered the moment it exists, rather than the moment somebody
        remembers to add it to the list above.

        Routes come from the router MODULES, not from `app.routes`, and that is not
        a style choice: this FastAPI version wraps each `include_router` in an
        opaque `_IncludedRouter` entry, so `app.routes` yields three real routes and
        fifteen empty wrappers. A sweep over it would have walked nothing and
        passed. `tests/test_route_contracts.py::_routes` takes the same approach for
        the same reason -- and the paths there are prefix-less, so `/api` is added
        back here.
        """
        from fastapi.routing import APIRoute
        from test_route_contracts import ROUTERS

        checked = 0
        leaked: list[str] = []
        async with client:
            for module in ROUTERS:
                for route in module.router.routes:
                    if not isinstance(route, APIRoute) or "{" in route.path:
                        continue
                    path = f"/api{route.path}"
                    for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                        checked += 1
                        response = await client.request(
                            method, path, json={}, headers=_auth(annotate_token)
                        )
                        if (
                            response.status_code != 403
                            and path not in ANNOTATE_REACH
                            and path not in EXEMPT_PATHS
                        ):
                            leaked.append(f"{method} {path} -> {response.status_code}")
        assert checked > 20, f"only walked {checked} routes; the enumeration is wrong"
        assert not leaked, "reachable but not in ANNOTATE_REACH:\n" + "\n".join(leaked)

    def test_the_exempt_set_is_what_this_sweep_assumes(self) -> None:
        """The sweep above skips `EXEMPT_PATHS`, so it is only honest if that set is
        what it thinks it is.

        An exempt path is unauthenticated for EVERYONE -- the login flow and the
        public access request -- so reaching one with a scoped token is not a scope
        leak. But an exemption added later would be skipped silently, which is the
        one way this sweep could stop meaning anything. `tests/test_mcp_auth.py`
        pins the count; this pins that the skip list is the exempt list and nothing
        broader.
        """
        assert len(EXEMPT_PATHS) == 8
        # `/api/health/live` is in both, legitimately: it is unauthenticated for
        # everyone AND named by the scope, because the extension asks it whether the
        # instance is reachable before it has anything to authenticate with. Any
        # OTHER overlap would be a path the scope check can never be tested on --
        # the reach test would pass because the middleware returned early.
        assert (EXEMPT_PATHS & ANNOTATE_REACH) == {"/api/health/live"}
