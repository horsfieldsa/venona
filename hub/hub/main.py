"""The hub application.

No docs routes: the surface is small, server-rendered, and documented in
docs/HUB.md -- an interactive schema browser on an identity provider is
attack surface with no reader.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from hub import errors, notify, poller
from hub.config import Settings, assert_usable, get_settings
from hub.db import core as db
from hub.db.migrate import migrate
from hub.errors import RequestIdMiddleware
from hub.middleware import AdminBearerMiddleware, SecurityHeadersMiddleware
from hub.release import RELEASE
from hub.routers import admin, billing, brand, oidc, ops, ops_api, portal, public

logging.basicConfig(level=logging.INFO)
# The host's orchestrator probes the health path every five seconds and every
# instance's status poller asks again, so the access log is almost entirely
# probes. Errors carry a request id through `hub.errors`; volume is on the
# metrics tab.
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
log = logging.getLogger("hub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    # Fail closed, at startup, with an actionable message -- every branch of
    # `assert_usable` is a configuration that would otherwise look like it
    # worked until the first login or the first poll.
    assert_usable(settings)

    conn = db.connect(settings.database_url)
    try:
        applied = migrate(conn)
        if applied:
            log.info("migrated: %s", ", ".join(applied))
        # AFTER the migration, in the same boot, because a notice type shipped
        # in this release has to reach the destinations that predate it -- a
        # switch nobody is ever shown is a feature that silently does not exist.
        offered = notify.offer_new_types(conn)
        if offered:
            log.info("notify: offered %d new switch(es)", offered)
    finally:
        conn.close()

    task = poller.start(settings)
    yield
    if task is not None:
        task.cancel()


def build_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        version=RELEASE,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.include_router(billing.router)
    app.include_router(portal.router)
    app.include_router(oidc.router)
    app.include_router(public.router)
    app.include_router(admin.router)
    app.include_router(ops.router)
    app.include_router(ops_api.router)
    app.include_router(brand.router)
    ops.install(app)

    # LAST ADDED IS OUTERMOST (Starlette builds the stack over `reversed`).
    # The admin gate innermost, then security headers over its 401s, then the
    # request id over everything -- reverse any two and a refused request
    # ships without headers or without an id, silently.
    app.add_middleware(AdminBearerMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, edge=settings.edge_headers)
    app.add_middleware(RequestIdMiddleware)
    errors.install(app, edge=settings.edge_headers)
    return app


app = build_app(get_settings())
