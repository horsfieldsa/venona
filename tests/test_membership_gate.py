"""Who may obtain a session. Contract C12's authorization addendum.

THE BUG THIS FILE EXISTS FOR. Authorization and authentication disagreed about
what membership meant. `AuthMiddleware._lookup_user` decided every request from
the `users` row; `routers/auth.py::callback` decided sign-in from
`VENONA_AUTH_ALLOWED_EMAILS` and never read the table at all. So a row created by
`venona users add` -- or, once the admin surface exists, by approving an access
request -- was authorized for everything a reader may do and could not get
through the door. Nothing tested the callback, which is why the two halves could
drift that far apart.

The rule now: **the row decides, and the environment is consulted only when
there is no row.** These tests pin both directions and the ordering between them,
because the natural way to write it -- `email_is_allowed(...) or user`-- is the
same fall-through bug C12 already records for bearers versus cookies, and it
fails open for exactly the person you most recently suspended.
"""

from __future__ import annotations

import importlib
import time

import httpx
import pytest
from venona.api import authz, security
from venona.api.routers import auth as auth_router

GOOGLE = "https://accounts.google.com"
NOW = 1_800_000_000.0


@pytest.fixture
def env(monkeypatch, tmp_path, database_url: str):
    """Auth ON, Google configured, and a database with three kinds of user.

    The allow-list holds ONE address, and deliberately none of the three rows:
    every assertion here is about whether the row or the environment decided, and
    an address in both proves nothing about which one was read.
    """
    from venona.db import core as db
    from venona.services import user_service

    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", "r" * 64)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", "bootstrap@example.com")
    monkeypatch.setenv("VENONA_AUTH_GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("VENONA_AUTH_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("VENONA_PUBLIC_URL", "https://venona.example.com")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VENONA_DATABASE_URL", database_url)
    security.get_auth_config.cache_clear()

    conn = db.connect(database_url)
    try:
        user_service.create(
            conn,
            user_id="user:reader",
            email="reader@example.com",
            role=authz.READER,
            now="2026-08-17T00:00:00Z",
        )
        user_service.create(
            conn,
            user_id="user:gone",
            email="gone@example.com",
            role=authz.READER,
            now="2026-08-17T00:00:00Z",
        )
        user_service.set_status(conn, user_id="user:gone", status=user_service.SUSPENDED, now=NOW)
    finally:
        conn.close()

    from venona.settings import get_settings

    get_settings.cache_clear()
    yield
    security.get_auth_config.cache_clear()
    get_settings.cache_clear()


def _app(database_url: str, monkeypatch):
    """The REAL app and its real middleware stack.

    `conftest.api_app` builds a bare FastAPI with no middleware, which cannot
    exercise a boundary -- the same reason `tests/test_authz.py` reloads
    `venona.main` rather than using it.
    """
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
    )


def _stub_identity(monkeypatch, email: str, *, name: str | None = "Ada Reyes", sub: str = "sub-1"):
    """Stand in for the Google exchange.

    Patched on the `oauth` module object the router holds, not on the router's
    own name, so the test cannot pass because it replaced something the code
    stopped calling.
    """
    from venona.api import oauth

    async def _identity(cfg, *, code, nonce):
        return oauth.OidcIdentity(email=email, issuer=GOOGLE, subject=sub, display_name=name)

    monkeypatch.setattr(oauth, "identity_for_code", _identity)


async def _callback(
    client, monkeypatch, email: str, *, stub: bool = True, **kwargs
) -> httpx.Response:
    """Drive a full sign-in: mint real state, then answer the exchange.

    The state cookie is set on the CLIENT rather than passed per request, which
    is both what httpx wants and what a browser actually does -- `/api/auth/login`
    sets it on one response and the callback arrives carrying it.
    """
    cfg = security.get_auth_config()
    cookie, state, _nonce = security.mint_state(cfg, now=time.time(), next_path="/")
    client.cookies.set(security.STATE_COOKIE, cookie)
    if stub:
        _stub_identity(monkeypatch, email, **kwargs)
    return await client.get("/api/auth/callback", params={"code": "authz-code", "state": state})


def _signed_in(response: httpx.Response) -> bool:
    """A session cookie was set and we were not bounced to /login."""
    return (
        response.status_code == 302
        and security.COOKIE_NAME in response.cookies
        and not response.headers.get("location", "").startswith("/login")
    )


# ── the row decides ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_user_row_alone_is_enough_to_sign_in(env, database_url, monkeypatch) -> None:
    """⭐ THE POINT OF THE SLICE. `reader@example.com` is not in the allow-list.

    Before this, `venona users add` produced a row that could never obtain a
    session, so approving an access request granted nothing a person could use.
    """
    async with _client(_app(database_url, monkeypatch)) as client:
        response = await _callback(client, monkeypatch, "reader@example.com")
        assert _signed_in(response), (
            f"a user with a row was refused sign-in: "
            f"{response.status_code} {response.headers.get('location')}"
        )


@pytest.mark.anyio
@pytest.mark.negative
async def test_a_suspended_row_is_refused_at_the_callback(env, database_url, monkeypatch) -> None:
    """Not merely on the next request. Suspension has to reach the door itself,
    or a suspended person gets a fresh fourteen-day cookie every time they click
    sign-in and is refused only once they use it."""
    async with _client(_app(database_url, monkeypatch)) as client:
        response = await _callback(client, monkeypatch, "gone@example.com")
        assert not _signed_in(response)
        assert auth_router.NOT_ALLOWED in response.headers.get("location", "")


@pytest.mark.anyio
@pytest.mark.negative
async def test_a_stranger_with_neither_is_refused(env, database_url, monkeypatch) -> None:
    """And carries the code the login page renders as its own state, because an
    unlisted visitor is the one failure here with somewhere to go next."""
    async with _client(_app(database_url, monkeypatch)) as client:
        response = await _callback(client, monkeypatch, "stranger@example.com")
        assert not _signed_in(response)
        assert auth_router.NOT_ALLOWED in response.headers.get("location", "")


@pytest.mark.anyio
async def test_the_env_list_still_bootstraps_an_address_with_no_row(
    env, database_url, monkeypatch
) -> None:
    """The other direction. `ensure_from_env` runs in the lifespan and
    `httpx.ASGITransport` never runs one, so without this branch a correctly
    configured first boot would refuse the operator."""
    async with _client(_app(database_url, monkeypatch)) as client:
        response = await _callback(client, monkeypatch, "bootstrap@example.com")
        assert _signed_in(response)


@pytest.mark.anyio
@pytest.mark.negative
async def test_a_suspended_address_in_the_env_list_is_still_refused(
    env, database_url, monkeypatch
) -> None:
    """⭐ THE ORDERING, and the reason this is not written as
    `email_is_allowed(...) or user is not None`.

    That `or` reads correctly and fails open for precisely the person you most
    recently suspended -- and every address `ensure_from_env` ever created is in
    the environment, so it is the common case rather than a corner.
    """
    from venona.db import core as db
    from venona.services import user_service

    conn = db.connect(database_url)
    try:
        user_service.create(
            conn,
            user_id="user:bootstrap",
            email="bootstrap@example.com",
            role=authz.ADMIN,
            now="2026-08-17T00:00:00Z",
            allow_admin=True,
        )
        user_service.set_status(
            conn, user_id="user:bootstrap", status=user_service.SUSPENDED, now=NOW
        )
    finally:
        conn.close()

    async with _client(_app(database_url, monkeypatch)) as client:
        response = await _callback(client, monkeypatch, "bootstrap@example.com")
        assert not _signed_in(response), (
            "a suspended user in VENONA_AUTH_ALLOWED_EMAILS signed in -- the env "
            "list was consulted instead of the row"
        )


# ── the identity is recorded ─────────────────────────────────────────


@pytest.mark.anyio
async def test_signing_in_stamps_the_oidc_identity(env, database_url, monkeypatch) -> None:
    """`iss` + `sub` is the identity, not the address, and the callback is the
    only moment the provider tells us. Both columns and `display_name` were
    unwritable before this: `record_oidc_identity` existed and had no caller."""
    from venona.db import core as db
    from venona.db.repositories import users as repo

    async with _client(_app(database_url, monkeypatch)) as client:
        assert _signed_in(await _callback(client, monkeypatch, "reader@example.com"))

    conn = db.connect(database_url)
    try:
        row = repo.by_id(conn, "user:reader")
        assert row["display_name"] == "Ada Reyes"
        full = conn.execute(
            "SELECT oidc_issuer, oidc_subject, last_seen_at FROM users WHERE id = ?",
            ("user:reader",),
        ).fetchone()
    finally:
        conn.close()

    assert full["oidc_issuer"] == GOOGLE
    assert full["oidc_subject"] == "sub-1"
    assert full["last_seen_at"], "last_seen_at is what makes a users table answerable"


@pytest.mark.anyio
async def test_a_later_login_without_a_name_claim_does_not_blank_the_name(
    env, database_url, monkeypatch
) -> None:
    """`record_oidc_identity` omits the key rather than writing NULL, and nothing
    has ever exercised it -- the function had no callers at all."""
    from venona.db import core as db
    from venona.db.repositories import users as repo

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        assert _signed_in(await _callback(client, monkeypatch, "reader@example.com"))
        assert _signed_in(await _callback(client, monkeypatch, "reader@example.com", name=None))

    conn = db.connect(database_url)
    try:
        assert repo.by_id(conn, "user:reader")["display_name"] == "Ada Reyes"
    finally:
        conn.close()


# ── an unreachable database fails CLOSED ─────────────────────────────


@pytest.mark.anyio
@pytest.mark.negative
async def test_an_unreachable_database_does_not_mint_a_synthetic_admin(
    env, database_url, monkeypatch
) -> None:
    """⭐ `_lookup_user` used to answer a connect failure with `_env_admin()`,
    which reads the environment WITHOUT reading the row.

    So anyone demoted or suspended who was still named in the environment -- and
    nothing removes an address from it -- got admin back for as long as they
    could keep connections exhausted. The synthetic record also carries `id=""`,
    so `actor_id` returns None and the principal signs every audit row as nobody.

    Break-glass for an unreachable database is `VENONA_AUTH_API_TOKEN`, which
    needs no database, so failing closed here costs nothing.
    """
    from venona.api.middleware import AuthMiddleware

    app = _app(database_url, monkeypatch)
    cfg = security.get_auth_config()
    token, _expires = security.mint_session(cfg, email="bootstrap@example.com", now=time.time())

    def _unreachable(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("venona.db.core.connect", _unreachable)

    async with _client(app) as client:
        client.cookies.set(security.COOKIE_NAME, token)
        response = await client.get("/api/briefs")
        assert response.status_code == 401, (
            f"an env-listed address authenticated with no database to check the "
            f"row against, and would have been an admin: {response.status_code}"
        )

    assert AuthMiddleware  # the import is the point of the reference


# ── what a person may change about themselves ────────────────────────


@pytest.mark.anyio
async def test_a_reader_can_rename_themselves_and_it_round_trips(
    env, database_url, monkeypatch
) -> None:
    """The account page's only control, and the first reader-reachable write."""
    async with _client(_app(database_url, monkeypatch)) as client:
        assert _signed_in(await _callback(client, monkeypatch, "reader@example.com"))

        patched = await client.patch("/api/auth/me", json={"display_name": "  Ada R.  "})
        assert patched.status_code == 200, patched.text
        # Trimmed on the way in, and the response is the STORED value rather than
        # an echo of the request.
        assert patched.json()["display_name"] == "Ada R."

        session = await client.get("/api/auth/session")
        assert session.json()["display_name"] == "Ada R."
        assert session.json()["provider"] == "Google"
        assert session.json()["created_at"], "the account page's 'since' date"


@pytest.mark.anyio
async def test_clearing_the_name_is_distinct_from_a_provider_omitting_it(
    env, database_url, monkeypatch
) -> None:
    """A person emptying the field MUST blank it; a login whose provider sent no
    `name` claim must NOT. `set_display_name` and `record_oidc_identity` are two
    functions for exactly this reason, and the two cases are opposites."""
    from venona.db import core as db
    from venona.db.repositories import users as repo

    async with _client(_app(database_url, monkeypatch)) as client:
        assert _signed_in(await _callback(client, monkeypatch, "reader@example.com"))
        assert (await client.patch("/api/auth/me", json={"display_name": "   "})).json()[
            "display_name"
        ] is None

    conn = db.connect(database_url)
    try:
        assert repo.by_id(conn, "user:reader")["display_name"] is None
    finally:
        conn.close()


@pytest.mark.anyio
@pytest.mark.negative
async def test_renaming_yourself_cannot_smuggle_a_role(env, database_url, monkeypatch) -> None:
    """⭐ MASS ASSIGNMENT. `DisplayNameUpdate` is the whole allowlist.

    pydantic ignores undeclared keys by default rather than rejecting them, which
    is the right behaviour for a browser-filled form and the wrong thing to rely
    on silently -- so this pins it. A model that grew a `role` field "for
    completeness" would turn the one reader-writable endpoint in the application
    into self-elevation, and it would review clean.
    """
    from venona.db import core as db
    from venona.db.repositories import users as repo

    async with _client(_app(database_url, monkeypatch)) as client:
        assert _signed_in(await _callback(client, monkeypatch, "reader@example.com"))
        response = await client.patch(
            "/api/auth/me",
            json={
                "display_name": "Ada",
                "role": "admin",
                "status": "active",
                "id": "user:admin",
                "user_id": "user:admin",
                "email": "someone-else@example.com",
            },
        )
        assert response.status_code == 200
        assert response.json()["role"] == authz.READER

    conn = db.connect(database_url)
    try:
        row = repo.by_id(conn, "user:reader")
        assert row["role"] == authz.READER, "a reader renamed themselves into an admin"
        assert row["email"] == "reader@example.com"
    finally:
        conn.close()


@pytest.mark.anyio
@pytest.mark.negative
async def test_a_principal_with_no_row_is_told_rather_than_silently_ignored(
    env, database_url, monkeypatch
) -> None:
    """`actor_id` is None for a root bearer, for the env break-glass admin whose
    `id` is "", and for `via="disabled"` -- which is how `make dev` runs. Without
    this the first person to try the feature gets a 200 having changed nothing."""
    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        await client.post("/api/auth/token-login", json={"token": "r" * 64})
        response = await client.patch("/api/auth/me", json={"display_name": "Nobody"})
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "no_account"


# ── the hub's membership claim ───────────────────────────────────────
#
# With VENONA_OIDC_ISSUER set, the id_token carries `venona_role` and the hub
# is the membership authority (docs/adr/0141): first login provisions the row,
# later logins sync the role, and local suspension still always wins.

HUB = "https://hub.example.com"


def _stub_hub_identity(monkeypatch, email: str, *, role: str, sub: str = "hub-sub-1"):
    from venona.api import oauth

    async def _identity(cfg, *, code, nonce):
        return oauth.OidcIdentity(
            email=email, issuer=HUB, subject=sub, display_name="Hub User", role=role
        )

    monkeypatch.setattr(oauth, "identity_for_code", _identity)


@pytest.fixture
def hub_env(env, monkeypatch):
    monkeypatch.setenv("VENONA_OIDC_ISSUER", HUB)
    monkeypatch.setenv("VENONA_OIDC_CLIENT_ID", "inst-client")
    monkeypatch.setenv("VENONA_OIDC_CLIENT_SECRET", "inst-secret")
    security.get_auth_config.cache_clear()
    yield
    security.get_auth_config.cache_clear()


async def test_a_hub_role_claim_provisions_a_stranger(hub_env, database_url, monkeypatch) -> None:
    from venona.db import core as db
    from venona.services import user_service

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        _stub_hub_identity(monkeypatch, "newcomer@example.com", role="reader")
        response = await _callback(client, monkeypatch, "newcomer@example.com", stub=False)
        assert _signed_in(response)

    conn = db.connect(database_url)
    try:
        user = user_service.lookup(conn, "newcomer@example.com")
        assert user is not None and user.role == authz.READER
    finally:
        conn.close()


async def test_a_hub_claim_of_admin_does_not_raise_authority(
    hub_env, database_url, monkeypatch
) -> None:
    """⭐ ADR 0168. This test asserted the OPPOSITE until 2026-08-27, and the
    behaviour it pinned is the finding: the hub could mint an administrator on
    every instance that trusted it, while venona's own admin surface was
    forbidden to grant one (C12's `reader` ceiling).

    The sign-in still SUCCEEDS -- refusing the login would punish a reader for
    their directory being wrong -- and the role they keep is the one they had.
    The disagreement goes to the log, not to the row.
    """
    from venona.db import core as db
    from venona.services import user_service

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        _stub_hub_identity(monkeypatch, "reader@example.com", role="admin")
        response = await _callback(client, monkeypatch, "reader@example.com", stub=False)
        assert _signed_in(response), "the person still signs in; only the elevation is refused"

    conn = db.connect(database_url)
    try:
        user = user_service.lookup(conn, "reader@example.com")
        assert user is not None and user.role == authz.READER
    finally:
        conn.close()


async def test_a_hub_role_change_still_reduces_on_login(hub_env, database_url, monkeypatch) -> None:
    """The ceiling is one-directional, exactly as C12 words it: authority may be
    REDUCED without limit and extended only as far as `reader`. A directory that
    cannot demote would be worse than no directory -- which is 0141's argument,
    and this half of it survives 0168 untouched."""
    from venona.db import core as db
    from venona.services import user_service

    conn = db.connect(database_url)
    try:
        with db.transaction(conn, what="arrange"):
            # TWO admins, and the second is not padding: `change_authority`
            # refuses to demote the LAST active administrator, so a lone admin
            # would keep their role for that reason and this test would pass
            # while proving nothing about the ceiling.
            for ident, email in (
                ("user:demoted", "demoted@example.com"),
                ("user:keeper", "keeper@example.com"),
            ):
                user_service.create(
                    conn,
                    user_id=ident,
                    email=email,
                    role=authz.ADMIN,
                    now="2026-08-17T00:00:00Z",
                    # Shell access, standing in for `venona users add --role admin`.
                    allow_admin=True,
                )
    finally:
        conn.close()

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        _stub_hub_identity(monkeypatch, "demoted@example.com", role="reader")
        response = await _callback(client, monkeypatch, "demoted@example.com", stub=False)
        assert _signed_in(response)

    conn = db.connect(database_url)
    try:
        user = user_service.lookup(conn, "demoted@example.com")
        assert user is not None and user.role == authz.READER
    finally:
        conn.close()


async def test_local_suspension_beats_the_hub_claim(hub_env, database_url, monkeypatch) -> None:
    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        _stub_hub_identity(monkeypatch, "gone@example.com", role="admin")
        response = await _callback(client, monkeypatch, "gone@example.com", stub=False)
        assert not _signed_in(response)


async def test_a_hub_admin_with_no_local_row_is_refused_not_crashed(
    hub_env, database_url, monkeypatch
) -> None:
    """THE FIRST SIGN-IN OF AN INSTANCE'S FIRST ADMINISTRATOR.

    The hub calls them an admin and this instance may not mint one (ADR 0168),
    so `create` hits its ceiling -- and the callback answered a 500 with a
    reference code, on the single most likely first interaction anyone has with
    a new deployment.

    It cannot degrade the way the role-SYNC branch does: there the person keeps
    the role they already had, and here there is no row at all. Refusing legibly
    is the only honest outcome. A quiet downgrade to reader would strand them,
    because `change_authority` refuses to raise anyone over a wire, so they
    would be a reader with no way up and no explanation.
    """
    from venona.db import core as db

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        _stub_hub_identity(monkeypatch, "firstadmin@example.com", role="admin")
        response = await _callback(client, monkeypatch, "firstadmin@example.com", stub=False)

    assert response.status_code == 302, f"expected a redirect, got {response.status_code}"
    assert response.headers["location"].startswith("/login"), response.headers["location"]
    assert not _signed_in(response)

    conn = db.connect(database_url)
    try:
        row = conn.execute(
            "SELECT count(*) AS n FROM users WHERE email = ?", ("firstadmin@example.com",)
        ).fetchone()
        assert row["n"] == 0, "a refused login must not leave a half-made account behind"
    finally:
        conn.close()


async def test_a_hub_reader_with_no_local_row_is_still_provisioned(
    hub_env, database_url, monkeypatch
) -> None:
    """The other half: the ceiling is about ADMINS, and a reader must still sail
    through. Without this, 'refuse on AuthorityError' could close the door on
    everybody and the test above would still pass."""
    from venona.db import core as db

    app = _app(database_url, monkeypatch)
    async with _client(app) as client:
        _stub_hub_identity(monkeypatch, "ordinary@example.com", role="reader")
        response = await _callback(client, monkeypatch, "ordinary@example.com", stub=False)

    assert _signed_in(response)
    conn = db.connect(database_url)
    try:
        row = conn.execute(
            "SELECT role FROM users WHERE email = ?", ("ordinary@example.com",)
        ).fetchone()
        assert row["role"] == "reader"
    finally:
        conn.close()
