"""Shared fixtures. Everything here is offline: no network, no model calls."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from venona.api import ratelimit  # noqa: E402
from venona.db import core as db  # noqa: E402
from venona.services import token_service  # noqa: E402
from venona.services.seed import load_seed  # noqa: E402

# Every user-authored document the suite is allowed to READ and must never
# WRITE. `profile/` is what the user edits; `config/` is the ontology, sources
# and settings.
GUARDED = sorted(
    p
    for directory in ("profile", "config")
    for p in (REPO_ROOT / directory).glob("*")
    if p.is_file()
)


def _tree_state() -> dict[Path, tuple[int, str]]:
    """mtime AND content, because restoring the original is not innocent.

    A content hash alone passes the write-then-restore pattern -- which is the
    exact shape of the bug this guard exists to catch. That test was corrupt
    for its own duration, unsafe under `pytest-xdist`, and permanently
    destructive if the process died between the write and the `finally`. The
    mtime is what makes the touch visible after the evidence is gone.
    """
    return {
        p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in GUARDED
        if p.exists()
    }


#: Every environment variable that changes who may do what, or what they may
#: read. The suite sets its own baseline for all of them.
_AUTH_ENVIRONMENT = (
    "VENONA_AUTH_ENABLED",
    "VENONA_AUTH_SESSION_SECRET",
    "VENONA_AUTH_API_TOKEN",
    "VENONA_AUTH_ALLOWED_EMAILS",
    "VENONA_TOKEN_LOGIN",
    "VENONA_OIDC_ISSUER",
    "VENONA_GOOGLE_CLIENT_ID",
    "VENONA_GOOGLE_CLIENT_SECRET",
    "VENONA_MGMT_TOKEN",
    "VENONA_BILLING_ENFORCED",
    "VENONA_PUBLIC_URL",
)


@pytest.fixture(autouse=True)
def _the_suite_owns_its_model_service() -> None:
    """The suite must not inherit whichever service the app was left on.

    `config/settings.yaml` is the shipped default AND the default instance's
    live config, so switching services at `/admin/model` rewrites a tracked
    file that this suite reads. The service decides which driver parses an
    envelope, which directory a fixture is read from, and which model names the
    cache key is taken over -- so leaving it inherited makes `make test` depend
    on a button somebody pressed.

    The symptom is the one `_the_suite_owns_its_auth_environment` was written
    for, with a different variable: seven failures across four files, not one
    of which mentions the model service, on a machine where somebody was trying
    Codex. Nothing points at the cause.

    A test that wants another service hands its executor an explicit harness.
    """
    from venona.settings import get_settings

    get_settings().llm.harness = "claude_code"


@pytest.fixture(autouse=True)
def _the_suite_owns_its_auth_environment(monkeypatch) -> None:
    """The offline suite reads `.env`, and it must not.

    `api/security.py` loads `.env` into the environment on purpose, so that
    `VENONA_AUTH_ENABLED=0` there is honoured by `make dev`. The cost is that
    every test inherits whatever the developer happens to have configured --
    and this file's own docstring claims the suite is hermetic. It was hermetic
    about the network and the database and not about this.

    The symptom is 29 failures spread over ten files, none of which mentions
    auth, on a machine where somebody turned sign-in on locally. Nothing points
    at the cause.

    `VENONA_AUTH_ENABLED=0` rather than empty, because `_flag` defaults that one
    to TRUE -- an empty value would enable auth for the whole suite, which is the
    opposite of the baseline these tests were written against. Anything that
    wants auth on sets it itself, after this (`tests/test_authz.py::env` is the
    pattern), and monkeypatch puts it all back afterwards.
    """
    from venona.api import security

    # SET EMPTY, never delete. `get_auth_config` calls `load_dotenv_into_environ`
    # on every miss, and python-dotenv's `override=False` fills any key that is
    # ABSENT -- so deleting a variable hands the value straight back from `.env`
    # a moment later. An empty string is present, so it wins.
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "0")
    for name in _AUTH_ENVIRONMENT[1:]:
        monkeypatch.setenv(name, "")
    security.get_auth_config.cache_clear()

    # THE PAYWALL'S CACHE IS PROCESS STATE TOO, and it leaks the same way the
    # environment does: `refresh_billing_enforced` writes a module global, so a
    # test that pushes the hub's switch leaves every later test measuring an
    # instance that charges. Reset rather than saved-and-restored, because the
    # baseline every test is written against is "nobody has told us anything".
    from venona.api import authz

    monkeypatch.setattr(authz, "_HUB_BILLING", None)
    yield
    security.get_auth_config.cache_clear()


@pytest.fixture(autouse=True)
def working_tree_is_read_only() -> None:
    """Fail any test that writes a user-authored config document.

    `test_rubric_fingerprint_ignores_weights` mutated the real
    `profile/scoring_rubric.yaml` and restored it in a `finally` -- so the
    working tree was corrupt for the duration of the test, permanently corrupt
    if the process died in between, and `make test` moved the file's mtime on
    every run. `config_service.py` records the same incident for
    `config/ontology.yaml`, where a sandboxed test wrote the real file because
    it redirected a path the loader did not read.

    Any WRITE fails it, restored or not -- see `_tree_state`. Ten small files,
    so the cost does not register.

    Sandbox by pointing `settings.profile_dir` / `config_dir` at `tmp_path` and
    copying the documents in -- see `tests/test_config_docs.py::settings`.
    """
    before = _tree_state()
    yield
    after = _tree_state()

    touched = sorted(
        f"{p.relative_to(REPO_ROOT)}"
        + ("" if before.get(p, (0, ""))[1] == state[1] else " (CONTENT CHANGED)")
        for p, state in after.items()
        if before.get(p) != state
    )
    assert not touched, (
        f"this test wrote to the working tree: {touched}. Config documents are "
        f"the user's, not the suite's -- copy them into `tmp_path` and redirect "
        f"`settings.profile_dir` / `settings.config_dir` instead. Restoring the "
        f"original afterwards does not make it safe: the file is still wrong "
        f"for the duration, and stays wrong if the process dies in between."
    )


@pytest.fixture(autouse=True)
def _rate_limit_buckets_are_per_test() -> None:
    """Token buckets are process-global; without this, test ORDER decides outcomes.

    `httpx.ASGITransport` reports the same client address for every test in the
    process, so every request in the suite shares one bucket per name. Two route
    sweeps are enough to break that: `test_every_api_route_requires_auth` spends
    ~36 `write` tokens against a capacity of 60, and the MCP reach sweep in
    `tests/test_mcp_auth.py` walks the same table again -- so the second one
    starts collecting 429s partway through and asserts 403 against them. The
    failure is order-dependent and its message says nothing about rate limiting.

    `reset()` already existed for exactly this reason; it was just never autouse,
    and three call sites had to remember it.
    """
    ratelimit.reset()
    token_service.reset_touch_cache()
    yield


@pytest.fixture(autouse=True)
def _the_suite_never_reads_a_real_credential(tmp_path_factory, monkeypatch) -> None:
    """`$HOME` is redirected for every test, because capture falls back to it.

    `signed_in_home` reads the CLI's DEFAULT home under `$HOME` when Venona's is
    empty -- correct on an instance, catastrophic here: that is the developer's
    real `~/.codex/auth.json`, and a capture test would read the live token into
    a throwaway database.
    """
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))


@pytest.fixture(autouse=True)
def _disk_free_state_is_per_test() -> None:
    """The three module globals ADR 0234 introduced, reset around every test.

    All three replaced a FILE, which `tmp_path` isolated for free. A module value
    is not, and `security._EPOCH` proves it: `tests/test_mcp_tokens.py` bumps the
    epoch to the year 2286 to show an agent token ignores it, invalidating every
    session cookie for the rest of the run -- 28 failures across `test_oauth.py`
    and `test_ratelimit.py`, all passing alone, none mentioning sessions.
    """
    from venona.api import security
    from venona.services import config_service, harness_service

    def clear() -> None:
        config_service._STORE_DSN = ""
        security._EPOCH = 0.0
        harness_service._LAST_DIGEST.clear()

    clear()
    yield
    clear()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return REPO_ROOT / "fixtures"


# ── per-test database isolation ──────────────────────────────────────
#
# SQLite isolated by pointing `settings.data_dir` at `tmp_path`, because the
# database was a file under it. A DSN is not isolated by moving a directory, so
# isolation has to be expressible IN THE DSN -- the app opens its own connections
# through `deps.get_db` and must land in the same place the test does.
#
# DATABASE-per-test from a TEMPLATE, not schema-per-test. Measured on this
# machine: the template carries the seed data, so `CREATE DATABASE ... TEMPLATE`
# is a filesystem copy at ~93ms, against ~323ms to create a schema and then run
# the DDL and the seed into it. Seeding is 311ms of that, and paying it once per
# session instead of once per test is where the difference comes from.
#
# The honest cost of this design, stated where someone will read it: a SQLite
# file was cheaper than either, so `make test` is slower than it was. That is the
# price of the suite running against the engine that serves production, which is
# the whole point -- a class of bug that only appears on one engine is invisible
# to a suite that runs on the other.


def _with_database(dsn: str, name: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path=f"/{name}"))


TEMPLATE_DB = "venona_test_template"
EMPTY_TEMPLATE_DB = "venona_test_template_empty"


@pytest.fixture(scope="session")
def _pg_admin():
    """A connection to the maintenance database, for CREATE/DROP DATABASE.

    Those two cannot run inside a transaction, hence `autocommit`, and they
    cannot run from a connection to the database being replaced -- which is why
    this talks to `postgres` and never to a test database.
    """
    from venona.settings import get_settings

    admin = psycopg.connect(
        _with_database(get_settings().database_url, "postgres"), autocommit=True
    )
    yield admin
    admin.close()


@pytest.fixture(scope="session")
def _template_db(_pg_admin, fixtures_dir: Path) -> str:
    """Migrate and seed ONCE, into a template every test is copied from.

    Nothing may hold a connection to a template while it is being copied, so this
    closes its own before yielding and no other fixture ever connects to it.
    """
    from venona.settings import get_settings

    base = get_settings().database_url
    _pg_admin.execute(f"DROP DATABASE IF EXISTS {TEMPLATE_DB} WITH (FORCE)")
    _pg_admin.execute(f"CREATE DATABASE {TEMPLATE_DB}")

    connection = db.connect(_with_database(base, TEMPLATE_DB))
    try:
        db.migrate(connection)
        load_seed(connection, fixtures_dir, quiet=True)
    finally:
        connection.close()

    yield TEMPLATE_DB
    _pg_admin.execute(f"DROP DATABASE IF EXISTS {TEMPLATE_DB} WITH (FORCE)")


@pytest.fixture(scope="session")
def _empty_template_db(_pg_admin) -> str:
    """Migrated but NOT seeded. A second template, because "empty" is a real case.

    `test_orchestrator_smoke` drives a whole run and then counts what it produced
    -- one document, some new entities. Against the seeded template those counts
    are the seed's four documents plus one, and `entities_new` is zero because the
    seed already holds them. The distinction is the point of those tests, so it
    needs its own starting state rather than a subtraction.
    """
    from venona.settings import get_settings

    base = get_settings().database_url
    _pg_admin.execute(f"DROP DATABASE IF EXISTS {EMPTY_TEMPLATE_DB} WITH (FORCE)")
    _pg_admin.execute(f"CREATE DATABASE {EMPTY_TEMPLATE_DB}")

    connection = db.connect(_with_database(base, EMPTY_TEMPLATE_DB))
    try:
        db.migrate(connection)
    finally:
        connection.close()

    yield EMPTY_TEMPLATE_DB
    _pg_admin.execute(f"DROP DATABASE IF EXISTS {EMPTY_TEMPLATE_DB} WITH (FORCE)")


_db_counter = 0


@pytest.fixture
def make_database(_pg_admin, _template_db, _empty_template_db):
    """Mint throwaway seeded databases, and drop them all afterwards.

    A factory rather than only a single fixture because several tests need more
    than one at once -- `test_mcp_tokens` proves a token minted in one database
    does not verify in another, which is a claim you cannot make with one.

    `WITH (FORCE)` on the drop rather than trusting every connection to have been
    closed: a route that leaks one would otherwise fail the NEXT test, in a
    teardown, with a message naming neither.

    Names carry the pid so a future `pytest-xdist` run does not collide.
    """
    from venona.settings import get_settings

    base = get_settings().database_url
    created: list[str] = []

    def _make(*, seeded: bool = True) -> str:
        global _db_counter
        _db_counter += 1
        name = f"venona_test_{os.getpid()}_{_db_counter}"
        template = _template_db if seeded else _empty_template_db
        _pg_admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        _pg_admin.execute(f"CREATE DATABASE {name} TEMPLATE {template}")
        created.append(name)
        return _with_database(base, name)

    try:
        yield _make
    finally:
        for name in created:
            _pg_admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
def database_url(make_database) -> str:
    """A throwaway seeded database for this test, and the DSN that reaches it."""
    return make_database()


@pytest.fixture
def conn(database_url: str) -> db.Conn:
    """A freshly migrated, freshly seeded database per test."""
    connection = db.connect(database_url)
    yield connection
    connection.close()


# ── the real routers, over a throwaway database ──────────────────────
#
# Before these, four test files drove the app over HTTP and all four were about
# auth, errors and rate limiting. Every business router was exercised at the
# SERVICE level, which never runs FastAPI's response serialization — so a
# `response_model` could be wrong in either of its two directions with the whole
# suite green. It was: `OntologyResponse.version` typed `str` against an `int`
# served a 500 from `/api/meta/ontology` and 755 tests passed.
#
# The database is migrated and seeded per test under `tmp_path`, so the
# MUTATING endpoints are safe to call. This is not the working corpus and
# nothing survives the test.


class _NoGraph:
    """Stands in for `app.state.graph`.

    `health()` answers, because `/api/health` must report a degraded projection
    rather than fail with it (invariant 1). `driver` raises, so a route that
    quietly needs Neo4j fails loudly here instead of looking covered.
    """

    class _Health:
        ok = False
        version = None
        edition = None
        error = "no driver in tests"

    async def health(self):
        return self._Health()

    @property
    def driver(self):
        raise AssertionError(
            "this fixture serves no Neo4j; a Cypher-backed route cannot be tested here"
        )


class _NoLlm:
    """Deliberately has no `health` attribute.

    That is the replay branch in `routers/meta.py`, which returns the two-field
    `{ok, detail}` shape instead of the probe — the branch most likely to be
    broken by a field wrongly marked required.
    """


@pytest.fixture
def api_settings(tmp_path: Path, database_url: str):
    """A throwaway database, and throwaway profile and config directories.

    `profile_dir` and `config_dir` are redirected for the same reason
    `test_config_docs.py` redirects them, and it is not hypothetical: probing
    `POST /glossary/{surface}/not-an-acronym` through this fixture wrote `MOSA`
    into the real `profile/lexicon.yaml` and left a snapshot in the real
    `.history/`. That endpoint calls `config_service.add_to_stoplist`, which
    resolves its path from settings — so a fixture that only moved the database
    still hands every config-writing route the user's own documents.

    The `GUARDED` check catches it after the fact. That is a backstop, not a
    fix: the file is wrong for the duration and stays wrong if the process dies
    in between.

    NOT redirected: the ontology. `get_ontology()` reads a module constant
    derived from the source tree rather than anything in `Settings`, so a route
    that writes it needs `test_config_docs.py::ontology_file` as well.

    `routers/meta.py::enable_source` used to be a second hole — it called
    `get_settings()` directly and so read the real `config/sources.yaml`. It now
    takes `app.state.settings` like every other route, which closes it here and
    also makes it see the rebind `save_config` performs.
    """
    import copy

    from venona.services import config_service
    from venona.settings import get_settings

    real = get_settings()
    settings = copy.deepcopy(real)
    # The database is now a DSN, not a file under `data_dir`. `data_dir` is still
    # redirected because `raw_dir` hangs off it, but it no longer isolates the
    # database -- `database_url_override` is what does that, and every connection
    # the app opens through `deps.get_db` reads it.
    settings.database_url_override = database_url
    settings.data_dir = tmp_path / "data"
    settings.profile_dir = tmp_path / "profile"
    settings.config_dir = tmp_path / "config"
    for directory in (settings.data_dir, settings.profile_dir, settings.config_dir):
        directory.mkdir(parents=True, exist_ok=True)

    for meta in config_service.EDITABLE.values():
        if meta["dir"] == "ontology":
            continue
        source_dir = real.profile_dir if meta["dir"] == "profile" else real.config_dir
        target_dir = settings.profile_dir if meta["dir"] == "profile" else settings.config_dir
        source = source_dir / meta["file"]
        if source.exists():
            (target_dir / meta["file"]).write_text(source.read_text())

    return settings


@pytest.fixture
def api_db(api_settings) -> db.Conn:
    """A handle on the same database `deps.get_db` will open.

    `get_db` opens its own connection per request, so the app never touches this
    one — it is here for tests that want to look at the database directly.

    It no longer migrates or seeds: the `database_url` fixture underneath
    `api_settings` hands over a database already copied from the migrated, seeded
    template. The ordering guarantee that used to be the other reason for this
    fixture comes from that dependency now.
    """
    connection = db.connect(api_settings.database_url)
    yield connection
    connection.close()


@pytest.fixture
def api_app(api_settings, api_db):
    from fastapi import FastAPI
    from venona.api.routers import (
        access,
        admin,
        annotate,
        auth,
        briefs,
        config,
        curation,
        data,
        entities,
        glossary,
        ingest,
        learning,
        mcp,
        meta,
        notifications,
        preferences,
        runs,
    )

    app = FastAPI()
    for module in (
        admin,
        meta,
        briefs,
        entities,
        runs,
        glossary,
        curation,
        config,
        annotate,
        ingest,
        access,
        data,
        learning,
        mcp,
        # `auth` and `preferences` are a pair: the zone is WRITTEN through
        # `PATCH /api/preferences` and READ back on `GET /api/auth/session`,
        # and a test that cannot reach both cannot see them disagree.
        auth,
        preferences,
        notifications,
    ):
        app.include_router(module.router, prefix="/api")
    # The reader's half of the notification surface is a SECOND router in the
    # same module, mounted under `/api/preferences`. It is included by hand
    # because the loop above takes one `router` per module, and splitting the
    # two apart into their own files would separate an authorization boundary
    # that is only legible when both sides are read together.
    app.include_router(notifications.reader_router, prefix="/api")
    app.state.settings = api_settings
    app.state.graph = _NoGraph()
    app.state.llm = _NoLlm()
    return app


@pytest.fixture
async def api_client(api_app):
    import httpx

    transport = httpx.ASGITransport(app=api_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        yield client
