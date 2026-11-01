"""FastAPI application.

CORS is OFF unless `VENONA_CORS_ORIGINS` names an origin. The SPA has never
needed it -- Vite proxies /api in development and the reverse proxy serves both in
production, so the browser only ever sees one origin. The browser extension is
the first caller that does not, and `api/security.py::cors_origins` explains why
the allowlist is env-only and why `*` is refused.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from venona import logging as venona_logging
from venona.api import authz, errors, openapi, spa
from venona.api.errors import RequestIdMiddleware
from venona.api.middleware import AuthMiddleware, SecurityHeadersMiddleware
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
    graph,
    ingest,
    learning,
    mcp,
    meta,
    mgmt,
    notifications,
    oauth,
    preferences,
    relevance,
    runs,
    setup,
)
from venona.api.security import assert_usable, cors_origins, get_auth_config
from venona.db import core as db
from venona.domain import get_domain
from venona.graph.client import GraphClient
from venona.llm.executor import build_executor
from venona.pipeline.types import SystemClock
from venona.release import RELEASE
from venona.scheduler import start_scheduler
from venona.services import (
    config_migrations,
    config_service,
    harness_service,
    run_service,
    run_types,
)
from venona.settings import Paths, database_dsn, get_settings, is_production

# Uvicorn configures the root logger for its own loggers only, so ours would
# emit nothing at startup -- and "did the scheduler start?" is exactly the
# question you want answered from the log rather than by introspection.
#
# JSON when stderr is not a TTY, which is to say in a container. See
# `venona/logging.py`: on a workstation the aligned format reads better and
# `make dev` interleaves two processes; in a container the line is going to be
# parsed by something, and a request id is worth more than an aligned colon.
venona_logging.configure()
log = logging.getLogger("venona")


def _refuse_metered_billing(settings) -> None:
    """Decision 1, as a startup failure rather than a sentence in `.env.example`.

    venona runs on the `claude` CLI's OAuth subscription auth. An
    `ANTHROPIC_API_KEY` in the environment silently moves every model call onto
    metered billing, and nothing in the app would look different -- the runs
    succeed, the ledger fills in, and the bill arrives a month later.

    Prose was enough while the environment was a `.env` file on one laptop. A
    container inherits its environment from a file someone else wrote, an
    orchestrator injects secrets by convention, and `ANTHROPIC_API_KEY` is the
    single most conventional name in this space. Fail loudly instead.

    THIS IS THE OUTER BELT, NOT THE GUARD. `build_executor` decides, because
    billing mode is editable over HTTP and would change under a boot check.
    What this catches is the case that one cannot: a deploy whose environment
    carries a metered key nobody meant to ship.

    The names come from the harness table rather than from a literal, so the
    other vendor's key is covered by the same sentence -- but only the
    CONFIGURED one's. `OPENAI_API_KEY` is at least as conventional as
    `ANTHROPIC_API_KEY` and does identical damage on an instance that uses it;
    on one that does not, refusing it stops a working deployment for a key that
    could not have moved its spend.
    """
    from venona.llm.harness import resolve

    # THE CONFIGURED SERVICE ONLY. Sweeping every service in the table refuses
    # a key that cannot move this instance's spend at all -- `OPENAI_API_KEY`
    # set for a sidecar would stop an instance running Claude Code from
    # booting, and the message would send its operator to a page the refusal
    # has made unreachable.
    harness = resolve(settings.llm.harness)
    if settings.llm.billing_mode != "subscription":
        return
    present = sorted(name for name in harness.metered_env if os.environ.get(name))
    if present:
        raise RuntimeError(
            f"{', '.join(present)} is set while {harness.label} is on a "
            f"subscription plan, so every call would be billed per token "
            f"instead. Unset it, or choose an API key plan under Model in Admin, "
            f"which records the choice."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):

    # Fail closed, at startup, with an actionable message. Every branch of
    # `assert_usable` is a configuration that would otherwise look like it
    # worked -- most sharply the empty allowlist, where a Google OAuth client
    # authenticates ANY Google account behind a login page that looks exactly
    # like security.
    auth_cfg = get_auth_config()
    assert_usable(auth_cfg)
    if not auth_cfg.enabled:
        log.warning(
            "AUTH IS DISABLED (VENONA_AUTH_ENABLED=0). Every endpoint is open, "
            "including the ones that spend model budget and rewrite config. "
            "This is for localhost only."
        )

    # THREE STEPS, IN THIS ORDER, ALL BEFORE `get_settings()` (ADR 0234). The
    # schema, because `config_documents` does not exist until it has run; then
    # the documents out of their rows, replacing the entrypoint's `cp -rn`; then
    # the settings migration, which needs a document to exist and must precede
    # the loader whose groups forbid a retired knob. Here as well as in the
    # entrypoint, because a scaffolded instance never runs that script.
    boot_conn = db.connect(database_dsn())
    try:
        db.migrate(boot_conn)
        config_service.materialize(boot_conn, Paths())
    finally:
        boot_conn.close()
    config_migrations.migrate(Paths())

    settings = get_settings()
    # After the migration and the load, because it reads the configured
    # service and plan -- neither of which exists before this line.
    _refuse_metered_billing(settings)
    app.state.settings = settings
    app.state.graph = GraphClient(settings)
    app.state.llm = build_executor(settings)
    # The light lane's own throttle (contract C11). Two instances differing in
    # two things. How many `claude` subprocesses each will hold open, so a
    # lookup someone is watching a spinner for is not queued behind a
    # twenty-minute `analyze` on a two-slot semaphore.
    # `outage_wait_seconds=0`: a lookup someone is watching fails fast during a
    # provider incident; only the research lane waits one out.
    app.state.llm_light = build_executor(
        settings, concurrency=settings.llm.light_concurrency, outage_wait_seconds=0
    )

    # The request pool. Opened before anything can serve a request and closed
    # last, because every authenticated request draws two connections from it.
    # A dedicated connection is still the right answer for a holder that outlives
    # a request -- the reap below, runs, and the SSE stream -- and `db.pool` says
    # which and why.
    app.state.db_pool = db.pool(settings.database_url, max_size=settings.db_pool_max_size)
    app.state.db_pool.open(wait=True, timeout=30)

    # A run left `running` by a crash or restart looks live forever and the UI
    # spins on it. Reap on startup.
    conn = db.connect(settings.database_url)
    try:
        db.migrate(conn)

        # The credential cache the executor reads. Populated HERE because a
        # scheduled run, or any run before somebody opens the admin page,
        # would otherwise spawn its child with no connected credential at
        # all -- and the page repopulating it as a side effect of rendering
        # is what would make that look intermittent.
        harness_service.load(conn)

        # And the CLI's OWN store, written back into the home it reads
        # (ADR 0234) -- the half that lets a service whose credential cannot be
        # pasted, like Codex, keep a sign-in across a deploy with no disk. Once,
        # here, before any child is spawned; `ensure_dirs` runs on every spawn.
        # Every known service, because `/admin/model` switches without a restart.
        from venona.llm.harness import known, resolve

        for key in known():
            harness_service.restore(conn, Paths(), resolve(key))

        # Carry `VENONA_AUTH_ALLOWED_EMAILS` into `users`. Idempotent, and it runs
        # before anything can serve a request: membership is a table now, and
        # without this an instance whose allow-list is its only configuration has
        # no users at all and nobody can sign in.
        #
        # Every carried-over address becomes an ADMIN, which is the conservative
        # choice rather than the generous one -- each of them could already do
        # everything, so this changes nobody's privileges, where defaulting them to
        # reader would silently remove access somebody already had.
        from venona.pipeline.types import SystemIdGen, utc_now_iso
        from venona.services import user_service

        promoted = user_service.ensure_from_env(
            conn,
            emails=auth_cfg.allowed_emails,
            now=utc_now_iso(),
            ids=SystemIdGen(),
        )
        if promoted:
            log.warning("created %d admin user(s) from the allow-list: %s", len(promoted), promoted)

        stale = run_service.reap_interrupted(conn, now=SystemClock().now())
        if stale:
            log.warning("marked %d interrupted run(s) as failed: %s", len(stale), stale)

        # Carry `VENONA_NOTIFY_URL` into a destination row, once, for the same
        # reason the allow-list is carried into `users` above: the variable used
        # to BE this feature, and an instance that had it set must not go quiet
        # on the release that moves the setting into the app. It is skipped
        # entirely once any webhook destination exists, so somebody who has been
        # to the screen keeps their own choices.
        import os

        from venona import notify

        notify.adopt_legacy_url(conn, url=os.environ.get("VENONA_NOTIFY_URL", ""))

        # The paywall's switch, read once into memory. `billing_enforced()` is
        # asked on every request and answers from a module value, so without
        # this a switch the hub threw while this process was down would not be
        # seen until the hub's next push -- up to five minutes of the wrong
        # answer, and indefinitely if the hub is unreachable.
        #
        # AFTER `db.migrate` above, because on the release that introduces
        # `hub_flags` the table does not exist until the migration has run.
        authz.refresh_billing_enforced(conn)

        # The break-glass logout's epoch, read once for the same reason: a
        # process that never read it silently un-revokes every cookie the last
        # logout killed.
        from venona.api.security import refresh_epoch

        refresh_epoch(conn)
    finally:
        conn.close()

    # Truthy means "not offline", not "the schedule is on" -- the scheduler
    # runs jobless when disabled, so a config save can enable it later without
    # needing a loop to start one from. Ask the job, not the scheduler.
    app.state.scheduler = start_scheduler(app)

    try:
        yield
    finally:
        # THE SCHEDULER FIRST, so nothing new starts while we wait.
        if app.state.scheduler:
            app.state.scheduler.shutdown(wait=False)

        # Then let the light lane finish inside the platform's grace (epic AA),
        # BEFORE the graph and pool close -- a run still executing reads both.
        drained = await run_service.drain_lane(
            run_service.registry,
            lane=run_types.LIGHT,
            seconds=settings.shutdown_drain_seconds,
        )
        if drained:
            log.warning(
                "shutdown: %d run(s) did not finish in time and will be reaped: %s",
                len(drained),
                ", ".join(drained),
            )

        await app.state.graph.close()
        app.state.db_pool.close()


# `VENONA_ENV=production` turns the interactive docs off entirely. Everywhere
# else they live UNDER `/api` rather than at the root, for one reason: the auth
# middleware guards the `/api` prefix, because that is the only thing the reverse proxy
# proxies here. At `/openapi.json` they would sit outside the guard and outside
# `test_every_api_route_requires_auth`'s sweep -- unauthenticated, and with a
# test suite reporting otherwise.
#
# `make types` does not need them: `venona dump-openapi` builds the document
# from the app object, with no server and no Neo4j.
_PRODUCTION = is_production()

# Set by a production image that bakes the web build in, and by
# nothing else: in development vite serves the SPA and proxies /api here, and
# behind the reverse proxy the SPA comes from the reverse proxy's file_server. When it points at a
# real directory this process is the public edge -- it serves the SPA and owns
# the edge headers (HSTS, CSP) that the reverse proxy owns everywhere else.
_SPA_DIR = Path(os.environ["VENONA_SPA_DIR"]) if os.environ.get("VENONA_SPA_DIR") else None
_EDGE = _SPA_DIR is not None and _SPA_DIR.is_dir()

app = FastAPI(
    title="venona",
    # Read at import, so a domain edit shows here on the next restart -- this
    # is OpenAPI display copy, not a runtime binding.
    description=get_domain().tagline,
    version=RELEASE,
    lifespan=lifespan,
    docs_url=None if _PRODUCTION else "/api/docs",
    redoc_url=None if _PRODUCTION else "/api/redoc",
    openapi_url=None if _PRODUCTION else "/api/openapi.json",
)


# ── OAuth discovery, at the root ─────────────────────────────────────
#
# These two are the ONLY routes outside `/api`, and therefore the only ones
# outside `AuthMiddleware`'s guard. That is not an oversight and it is worth
# saying next to the code that made `docs_url` move UNDER `/api` for precisely
# the opposite reason:
#
#   * RFC 9728 §3 and RFC 8414 §3 FIX these paths at the root of the origin. A
#     client that has only the venona URL derives them; it cannot be told to look
#     under `/api`.
#   * They are metadata, not data. Both are public by specification, contain no
#     corpus and no identity, and say only which endpoints exist -- which anyone
#     who can reach the login page can already see.
#
# `tests/test_auth.py::test_every_api_route_requires_auth` sweeps `/api` and so
# does not cover these; `tests/test_oauth.py` asserts they are reachable
# unauthenticated ON PURPOSE, which is the same fact written down as an
# expectation rather than as a gap.
@app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@app.get("/.well-known/oauth-protected-resource/api/mcp", include_in_schema=False)
def _protected_resource_metadata() -> dict:
    from venona.api.security import get_auth_config
    from venona.services import oauth_service

    issuer = get_auth_config().public_url.rstrip("/")
    return oauth_service.protected_resource_metadata(issuer=issuer, resource=f"{issuer}/api/mcp")


@app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
def _authorization_server_metadata() -> dict:
    from venona.api.security import get_auth_config
    from venona.services import oauth_service

    return oauth_service.authorization_server_metadata(
        issuer=get_auth_config().public_url.rstrip("/")
    )


# Order matters and is counter-intuitive. `add_middleware` inserts at position
# 0 and the stack is built over `reversed(...)`, so the LAST one added is the
# OUTERMOST. SecurityHeaders therefore goes AFTER AuthMiddleware: it has to wrap
# it to reach the 401s, which are the responses most worth having headers on.
# Adding it first would leave every unit test passing and every rejection bare.
# A response field with a DEFAULT is on every response and pydantic still marks
# it optional, so the generated client types it `| undefined` and every read site
# grows a coalesce for a case the server cannot produce. `api/openapi.py` works
# out which schemas can honestly say "every key is present" and says it.
app.openapi = lambda: openapi.build(app)  # type: ignore[method-assign]

app.add_middleware(AuthMiddleware, edge=_EDGE)

# CORS sits OUTSIDE AuthMiddleware, and that placement is the whole point.
#
# A browser sends `OPTIONS` before any cross-origin request carrying an
# `Authorization` header, and it sends it with NO credentials -- that is what a
# preflight is. AuthMiddleware is default-deny and `OPTIONS /api/briefs` is not
# in `EXEMPT_PATHS`, so an inner CORS layer never runs: the preflight answers
# 401, the browser reports a CORS failure, and the real request is never sent.
# Verified 2026-08-10, before this existed -- the preflight came back 405, because nothing
# was handling `OPTIONS` at all.
#
# It goes INSIDE SecurityHeaders and RequestId, so a preflight -- which
# `CORSMiddleware` short-circuits and answers itself -- still carries the
# security headers and a request id a log line can be found by.
#
# `allow_credentials=False`, deliberately. The cross-origin caller this exists
# for is the browser extension, and it authenticates with `Authorization:
# Bearer` -- a path `security.py::principal_for` already prefers over the
# session. With credentials off the browser will not attach `venona_session` to
# a cross-origin request at all, so an allowed origin cannot ride the operator's
# ambient session. That turns CSRF off by construction rather than by handling
# it carefully, which is the version that stays true when someone adds a route.
_CORS_ORIGINS = cors_origins()
if _CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_CORS_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        # Explicit, not `*`: with `allow_credentials=False` Starlette would
        # accept a wildcard here, but `authorization` is the only non-simple
        # header this API needs and naming it keeps the door the width of the
        # thing going through it.
        allow_headers=["authorization", "content-type"],
        max_age=600,
    )

# Host-header poisoning. The reverse proxy already matches on the site address, so this is
# belt-and-braces -- but it costs one line and it covers the case the reverse proxy cannot:
# something on the docker network reaching the API container's port directly.
# `*` in development, where the app is bound to localhost and the header is not
# reachable from anywhere that matters.
#
# Added BEFORE SecurityHeaders/RequestId so those two wrap it: last-added is
# outermost, so this must go inward of them or its invalid-host 400 short-circuits
# outside the header stamper and ships bare -- the exact failure the ordering
# comment above exists to prevent, one rung further out.
_ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("VENONA_ALLOWED_HOSTS", "*").split(",") if h.strip()
]
if _ALLOWED_HOSTS != ["*"]:
    # `localhost` stays admissible so the container healthcheck, which dials
    # 127.0.0.1 by name, is not rejected by the hardening meant for the edge.
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=[*_ALLOWED_HOSTS, "localhost", "127.0.0.1"]
    )

app.add_middleware(SecurityHeadersMiddleware, edge=_EDGE)
# Outermost of the three, so every response -- including the 401s, 429s and the
# invalid-host 400 the layers below short-circuit -- carries a request id that a
# log line can be found by. Added last: last added is outermost.
app.add_middleware(RequestIdMiddleware)

# Handlers, not try/except at each call site. Six Neo4j sites were unguarded and
# 500'd when the database was down; the seventh was already correct. Guarding
# them individually means the eighth route ships without one. `edge` so the
# unhandled-500 backstop carries HSTS and the CSP where the app owns them.
errors.install(app, edge=_EDGE)

app.include_router(auth.router, prefix="/api")
app.include_router(access.router, prefix="/api")
app.include_router(meta.router, prefix="/api")
app.include_router(briefs.router, prefix="/api")
app.include_router(entities.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(glossary.router, prefix="/api")
app.include_router(curation.router, prefix="/api")
app.include_router(learning.router, prefix="/api")
app.include_router(relevance.router, prefix="/api")
app.include_router(preferences.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(notifications.reader_router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(setup.router, prefix="/api")
app.include_router(mgmt.router, prefix="/api")
app.include_router(data.router, prefix="/api")
app.include_router(annotate.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")
app.include_router(mcp.router, prefix="/api")
app.include_router(oauth.router, prefix="/api")
app.include_router(admin.router, prefix="/api")

# LAST, always: the SPA route is a catch-all and Starlette matches in
# registration order, so anything registered after it would be unreachable.
if _EDGE:
    spa.install(app, _SPA_DIR)
