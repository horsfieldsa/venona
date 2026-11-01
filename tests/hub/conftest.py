"""Hub fixtures. Offline, like everything under tests/.

Lives under `tests/` so the root `testpaths` picks it up with no pytest
config change, and so the root conftest's `_pg_admin` (a session connection to
the maintenance database) can be reused for CREATE/DROP DATABASE. The root
autouse guards are harmless here -- the hub writes no config documents and
holds no rate-limit buckets.

The database is a plain create-and-migrate per test, NOT the instance's
template-copy pattern: the template copy amortizes a 311ms seed, and the hub
has one migration file, five empty tables and no seed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "hub"))
sys.path.insert(0, str(Path(__file__).parent))

from hub.config import Settings  # noqa: E402
from hub.db import core as hubdb  # noqa: E402
from hub.db.migrate import migrate  # noqa: E402
from hubmaterial import HUB_URL, TEST_SEAL_KEY, TEST_SIGNING_KEY  # noqa: E402


@pytest.fixture(autouse=True)
def _the_site_is_never_really_fetched(monkeypatch) -> None:
    """Every `poll_once` reads a separate front site, so this has to be AUTOUSE.

    Unlike the mgmt transport, which is bound per test because a call only
    happens for a seeded instance, the site read happens on every cycle -- so
    without this the offline suite would open a real connection to example.com
    ten times over, and `make test` promises it makes none. It refuses rather
    than answering: a test that wants a version binds its own transport, and one
    that does not should exercise the failure path, which must not take the
    cycle down.
    """
    import httpx

    from hub import site_client

    monkeypatch.setattr(
        site_client,
        "SITE_TRANSPORT",
        httpx.MockTransport(lambda _request: httpx.Response(503, text="no")),
    )


@pytest.fixture(autouse=True)
def _hub_rate_limits_are_per_test() -> None:
    from hub import ratelimit

    ratelimit.reset()
    yield


def _with_database(dsn: str, name: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path=f"/{name}"))


_hub_db_counter = 0


@pytest.fixture
def hub_database_url(_pg_admin) -> str:
    """A freshly migrated, empty hub database, dropped afterwards."""
    from venona.settings import get_settings

    global _hub_db_counter
    _hub_db_counter += 1
    name = f"hub_test_{os.getpid()}_{_hub_db_counter}"
    base = get_settings().database_url
    _pg_admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    _pg_admin.execute(f"CREATE DATABASE {name}")
    dsn = _with_database(base, name)
    conn = hubdb.connect(dsn)
    try:
        migrate(conn)
    finally:
        conn.close()
    yield dsn
    _pg_admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
def hub_settings(hub_database_url: str) -> Settings:
    # `_env_file=None` so a developer's real `.env.hub` never bleeds into the
    # suite through pydantic-settings.
    return Settings(
        _env_file=None,
        public_url=HUB_URL,
        site_url="https://site.example",
        database_url=hub_database_url,
        session_secret="s" * 48,
        seal_key=TEST_SEAL_KEY,
        admin_token="a" * 40,
        oidc_signing_key=TEST_SIGNING_KEY,
        google_client_id="test-google-client",
        google_client_secret="test-google-secret",
        operator_emails="op@example.com",
        cors_origins="https://example.com",
        cookie_secure=False,
        poll_enabled=False,
    )


@pytest.fixture
def hub_conn(hub_settings: Settings) -> hubdb.Conn:
    conn = hubdb.connect(hub_settings.database_url)
    yield conn
    conn.close()


@pytest.fixture
def hub_app(hub_settings: Settings):
    from hub.main import build_app

    return build_app(hub_settings)


@pytest.fixture
async def hub_client(hub_app):
    """The real app over ASGI. Lifespan does NOT run under ASGITransport,
    which is fine: `hub_database_url` already migrated the throwaway
    database, and `assert_usable` has its own direct tests."""
    import httpx

    transport = httpx.ASGITransport(app=hub_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as client:
        yield client
