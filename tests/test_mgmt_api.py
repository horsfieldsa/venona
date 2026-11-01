"""The hub's management credential and surface (docs/adr/0142).

The leash is the thing under test: a `vmgmt_` bearer reaches exactly the four
mgmt routes, is refused everywhere else, and can never be exchanged for a
session or mistaken for the root token.
"""

from __future__ import annotations

import importlib
import pathlib

import httpx
import pytest
from venona.api import security

MGMT_TOKEN = "vmgmt_" + "a" * 64


@pytest.fixture
def env(monkeypatch, tmp_path, database_url: str):
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", "r" * 64)
    monkeypatch.setenv("VENONA_MGMT_TOKEN", MGMT_TOKEN)
    monkeypatch.setenv("VENONA_PUBLIC_URL", "https://venona.example.com")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VENONA_DATABASE_URL", database_url)
    security.get_auth_config.cache_clear()
    from venona.settings import get_settings

    get_settings.cache_clear()
    yield
    security.get_auth_config.cache_clear()
    get_settings.cache_clear()


def _app(database_url: str, monkeypatch):
    from venona.api.middleware import AuthMiddleware
    from venona.settings import get_settings

    monkeypatch.setattr(AuthMiddleware, "_database_url", database_url, raising=False)
    import venona.main as main

    importlib.reload(main)
    main.app.state.settings = get_settings()
    return main.app


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://venona.example.com",
        headers={"authorization": f"Bearer {MGMT_TOKEN}"},
    )


# ── the credential, as unit truths ──────────────────────────────────


def test_verify_bearer_refuses_a_mgmt_token(env) -> None:
    """A management credential must never become an unscoped session -- the
    same seam `vmcp_` closed on purpose, closed for the same reason."""
    cfg = security.get_auth_config()
    assert security.verify_bearer(cfg, f"Bearer {MGMT_TOKEN}") is None


def test_verify_mgmt_is_leashed(env) -> None:
    from venona.api.authz import MGMT_REACH

    cfg = security.get_auth_config()
    principal = security.verify_mgmt(cfg, MGMT_TOKEN)
    assert principal is not None
    assert principal.via == "mgmt"
    assert principal.reach == MGMT_REACH


def test_a_wrong_mgmt_token_is_refused(env) -> None:
    cfg = security.get_auth_config()
    assert security.verify_mgmt(cfg, "vmgmt_" + "b" * 64) is None


def test_the_root_slot_refuses_a_mgmt_token(env, monkeypatch) -> None:
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", MGMT_TOKEN)
    security.get_auth_config.cache_clear()
    with pytest.raises(security.AuthNotConfigured, match="vmgmt_"):
        security.assert_usable(security.get_auth_config())


def test_the_mgmt_slot_refuses_a_non_prefixed_value(env, monkeypatch) -> None:
    monkeypatch.setenv("VENONA_MGMT_TOKEN", "r" * 64)
    security.get_auth_config.cache_clear()
    with pytest.raises(security.AuthNotConfigured, match="VENONA_MGMT_TOKEN"):
        security.assert_usable(security.get_auth_config())


# ── the surface, through the real middleware ────────────────────────


async def test_the_hub_reads_status_and_nothing_else(env, database_url, monkeypatch) -> None:
    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        status = await client.get("/api/mgmt/status")
        assert status.status_code == 200
        body = status.json()
        assert body["domain_slug"] == "defense"
        assert body["setup"]["configured"] is True

        # the leash: one step outside the reach set is a 403, not a 404 --
        # the credential is valid, the path is simply not its business
        for path in ("/api/config", "/api/runs", "/api/data/stats"):
            refused = await client.get(path)
            assert refused.status_code == 403, path


async def test_status_names_the_build_not_just_the_release(env, database_url, monkeypatch) -> None:
    """What the hub tells two domains apart by.

    The release alone cannot: it is one number for the whole repo and it sat at
    0.1.0, unchanged, for the life of the project, so every domain reported the
    same thing and the field the hub renders said nothing. `commit` is the half
    that moves without anybody bumping it.

    `started_at` rather than a build time because the host exposes none to a
    Docker build, and an immutable-image deploy restarts the process -- so
    process start is how long the reported build has actually been serving.

    Patched on the ROUTER, not reloaded: the handler resolves these names at
    call time, and reloading would leave the substituted build in place for
    every test after this one.
    """
    from venona.api.routers import mgmt as mgmt_router

    monkeypatch.setattr(mgmt_router, "COMMIT", "abcdef1")

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        body = (await client.get("/api/mgmt/status")).json()

    assert body["version"] == (pathlib.Path(__file__).parents[1] / "VERSION").read_text().strip()
    assert body["commit"] == "abcdef1"
    assert body["started_at"].endswith("Z")


async def test_status_reports_no_build_off_a_build_host(env, database_url, monkeypatch) -> None:
    """Null, not a blank or a guess.

    A developer's machine has no commit to name, and the surfaces render that
    as `local`. Inventing one here would make an undeployed instance
    indistinguishable from a deployed one on the page that exists to tell them
    apart.
    """
    from venona.api.routers import mgmt as mgmt_router

    monkeypatch.setattr(mgmt_router, "COMMIT", None)

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        body = (await client.get("/api/mgmt/status")).json()
    assert body["commit"] is None


async def test_provision_add_then_suspend(env, database_url, monkeypatch) -> None:
    from venona.db import core as db
    from venona.services import user_service

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        created = await client.post(
            "/api/mgmt/provision-user",
            json={"email": "member@example.com", "role": "reader", "action": "add"},
        )
        assert created.status_code == 200 and created.json()["status"] == "created"

        suspended = await client.post(
            "/api/mgmt/provision-user",
            json={"email": "member@example.com", "action": "suspend"},
        )
        assert suspended.status_code == 200

    conn = db.connect(database_url)
    try:
        user = user_service.lookup(conn, "member@example.com")
        assert user is not None and not user.active
    finally:
        conn.close()


async def test_provision_logout_bumps_the_epoch_and_nothing_else(
    env, database_url, monkeypatch
) -> None:
    """The hub's sign-out reaching this instance (contract C12 addendum):
    `logout` ends the user's sessions by the same per-user epoch bump their
    own sign-out performs, and does NOT touch status -- a signed-out reader
    is not a suspended one."""
    from venona.db import core as db
    from venona.services import user_service

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        created = await client.post(
            "/api/mgmt/provision-user",
            json={"email": "signout@example.com", "role": "reader", "action": "add"},
        )
        assert created.status_code == 200

        out = await client.post(
            "/api/mgmt/provision-user",
            json={"email": "signout@example.com", "action": "logout"},
        )
        assert out.status_code == 200 and out.json()["status"] == "ok"

        # An unknown email is a 404 like every other non-add action: for a
        # sign-out that IS the signed-out state, and the hub treats it so.
        unknown = await client.post(
            "/api/mgmt/provision-user",
            json={"email": "nobody@example.com", "action": "logout"},
        )
        assert unknown.status_code == 404

    conn = db.connect(database_url)
    try:
        user = user_service.lookup(conn, "signout@example.com")
        assert user is not None
        assert user.active, "logout must not suspend"
        assert user.session_epoch > 0, "logout must revoke sessions via the epoch"
    finally:
        conn.close()


# ── the hub reaches the same door everything else does ───────────────


async def _provision(client, payload: dict) -> dict:
    response = await client.post("/api/mgmt/provision-user", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _shell_admin(database_url: str, email: str) -> None:
    """An administrator, created the only way one can be created: shell access.

    These tests used to arrange theirs through `_provision(role="admin")`, which
    ADR 0168 now refuses -- that refusal IS the fix, and a test that arranged
    through it would be asserting against the thing under test. `venona users add
    --role admin` is what an operator would really run here.
    """
    from venona.db import core as db
    from venona.services import user_service

    conn = db.connect(database_url)
    try:
        with db.transaction(conn, what="arrange_admin"):
            user_service.create(
                conn,
                user_id=f"user:{email}",
                email=email,
                role="admin",
                now="2026-08-17T00:00:00Z",
                allow_admin=True,
            )
    finally:
        conn.close()


async def test_a_hub_push_cannot_demote_the_last_admin(env, database_url, monkeypatch) -> None:
    """⭐ The instance left with nobody to administer it.

    Removing an administrator from the hub's directory pushes an `update` here.
    Written through `set_role` it reached around `change_authority` and so around
    every refusal in it — the sole administrator of an instance became a reader
    because a directory elsewhere said so, and the only surface that could put it
    back is the one they had just lost. The hub was told it worked.
    """
    from venona.db import core as db
    from venona.db.repositories import users as repo

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        _shell_admin(database_url, "solo@example.com")
        conn = db.connect(database_url)
        try:
            assert repo.active_admin_count(conn) == 1
        finally:
            conn.close()

        result = await _provision(
            client, {"email": "solo@example.com", "role": "reader", "action": "update"}
        )

    assert result["status"] == "refused", result
    assert "only active administrator" in (result.get("message") or "")

    conn = db.connect(database_url)
    try:
        from venona.services import user_service

        assert repo.active_admin_count(conn) == 1
        assert user_service.lookup(conn, "solo@example.com").is_admin
    finally:
        conn.close()


async def test_a_hub_push_cannot_suspend_the_last_admin(env, database_url, monkeypatch) -> None:
    """Suspension empties the instance exactly as a demotion does: the rule
    counts ACTIVE administrators, not which column moved."""
    from venona.db import core as db
    from venona.db.repositories import users as repo
    from venona.services import user_service

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        _shell_admin(database_url, "only@example.com")
        result = await _provision(client, {"email": "only@example.com", "action": "suspend"})

    assert result["status"] == "refused", result

    conn = db.connect(database_url)
    try:
        assert repo.active_admin_count(conn) == 1
        assert user_service.lookup(conn, "only@example.com").active
    finally:
        conn.close()


async def test_a_hub_push_still_demotes_when_another_admin_remains(
    env, database_url, monkeypatch
) -> None:
    """The rule is the LAST administrator, not administrators. A sync that could
    never demote anyone would be a directory the instance ignores."""
    from venona.db import core as db
    from venona.services import user_service

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        _shell_admin(database_url, "one@example.com")
        _shell_admin(database_url, "two@example.com")
        result = await _provision(
            client, {"email": "two@example.com", "role": "reader", "action": "update"}
        )

    assert result["status"] == "ok", result

    conn = db.connect(database_url)
    try:
        assert not user_service.lookup(conn, "two@example.com").is_admin
    finally:
        conn.close()


async def test_the_hub_throws_the_billing_switch(env, database_url, monkeypatch) -> None:
    """The push the operator page makes, end to end: stored, cached, reported.

    THE CACHE IS THE HALF THAT MATTERS. `billing_enforced()` answers from memory
    on every request, so a handler that wrote the row and left the cache alone
    would be a switch that takes effect on the next restart -- which is the
    deploy the page exists to avoid.
    """
    from venona.api import authz

    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "")
    monkeypatch.setattr(authz, "_HUB_BILLING", None)
    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        assert authz.billing_enforced() is False

        saved = await client.post("/api/mgmt/settings", json={"billing_enforced": True})
        assert saved.status_code == 200
        assert saved.json() == {"billing_enforced": True}
        assert authz.billing_enforced() is True, "the cache was not refreshed"

        # And the status poll now reports it, which is what the hub's settings
        # page reads back to show that a switch actually arrived.
        status = await client.get("/api/mgmt/status")
        assert status.json()["billing_enforced"] is True

        off = await client.post("/api/mgmt/settings", json={"billing_enforced": False})
        assert off.json() == {"billing_enforced": False}
        assert authz.billing_enforced() is False


async def test_an_instance_can_refuse_the_hubs_switch(env, database_url, monkeypatch) -> None:
    """`VENONA_BILLING_ENFORCED=0` beats the hub, and the instance says so.

    The break-glass: the day the hub is wrong, an operator with shell access to
    the box has to be able to open the gate without waiting for the hub to be
    fixed. Reporting the override rather than the instruction is what stops the
    hub's own page from showing its intent back to itself and calling it
    confirmation.
    """
    from venona.api import authz

    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "0")
    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        saved = await client.post("/api/mgmt/settings", json={"billing_enforced": True})
        assert saved.json() == {"billing_enforced": False}
        assert (await client.get("/api/mgmt/status")).json()["billing_enforced"] is False
    assert authz.billing_enforced() is False


async def test_the_settings_push_needs_the_hubs_credential(env, database_url, monkeypatch) -> None:
    """One step outside the reach set is the leash; this route is inside it, and
    nothing else may call it."""
    app = _app(database_url, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as anonymous:
        refused = await anonymous.post("/api/mgmt/settings", json={"billing_enforced": True})
        assert refused.status_code == 401
