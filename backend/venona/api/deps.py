"""Request-scoped dependencies.

A connection per request rather than a shared one, and transaction scope is what
makes that load-bearing: a shared connection would put two concurrent requests
inside one `db.transaction`, and the second would commit the first one's
half-written work. Postgres MVCC readers never block the writer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

from fastapi import Query, Request

from venona.db import core as db


@dataclass(frozen=True)
class Window:
    """One page of a list request: how many rows, starting where."""

    limit: int
    offset: int


def page_window(default: int, cap: int) -> Callable[..., Window]:
    """The shared `limit`/`offset` pair every paginated list endpoint takes.

    A factory rather than a dependency, because the right page size is a fact
    about each surface (runs read 25 at a time, the glossary 200) while the
    validation is not: `ge=1` everywhere because a non-positive limit reaches
    Postgres as a negative LIMIT and 500s, and `le=cap` bounds what one request
    can cost. The envelope convention these params pair with — `items` plus a
    whole-set `total` — is documented in docs/EXTENDING.md.
    """

    def dependency(
        limit: int = Query(default, ge=1, le=cap),
        offset: int = Query(0, ge=0),
    ) -> Window:
        return Window(limit=limit, offset=offset)

    return dependency


def get_db(request: Request) -> Iterator[db.Conn]:
    """One connection per request.

    The hazard this shape avoids is two callers using one connection
    CONCURRENTLY, and it is real here for a reason that is easy to miss: FastAPI
    runs a sync dependency in a threadpool worker, but an `async def` endpoint
    body runs on the event loop thread, so the connection is created in one
    thread and used in another. psycopg tolerates that -- it holds a lock per
    connection -- but serialising two requests behind one lock is not tolerance
    worth having, and a shared connection would also merge their transactions.

    This dependency hands every request its own, and the create / use / close
    sequence is strictly ordered within a single request. Sharing one across
    requests would reintroduce both problems -- don't.
    """
    with _borrow(request) as conn:
        yield conn


@contextmanager
def _borrow(request: Request) -> Iterator[db.Conn]:
    """A connection for the length of one request, pooled where there is a pool.

    THE FALLBACK IS NOT A NICETY. `app.state.db_pool` is created in the lifespan,
    and `httpx.ASGITransport` does not run one -- which is every API test in this
    repo. Without the fallback the whole suite would need a pool fixture to say
    anything about a route, so the seam is here rather than in each test.
    """
    dsn = request.app.state.settings.database_url
    source = getattr(request.app.state, "db_pool", None)
    if source is None:
        conn = db.connect(dsn)
        try:
            yield conn
        finally:
            conn.close()
        return
    with db.from_pool(source, dsn) as conn:
        yield conn


def get_db_optional(request: Request) -> Iterator[db.Conn | None]:
    """A connection, or None when there is not one to be had.

    FOR ENDPOINTS THAT MUST DEGRADE RATHER THAN FAIL, which is currently exactly
    one: `GET /api/auth/session`. Two callers need it to answer without a
    database and neither is hypothetical.

      * **The break-glass path.** A root bearer authenticates through
        `verify_bearer`, which touches no database by design -- C12 keeps it that
        way so the operator can get in when the database is the thing they are
        worried about. Making the endpoint that answers "am I signed in?" a hard
        dependant would take that away at exactly the wrong moment: the SPA
        cannot mount without it.
      * **`tests/test_auth.py`.** It exercises the whole boundary with no
        database at all -- that is why `lookup_user` is injected rather than
        imported -- and a `Depends(get_db)` on a route it sweeps turns every
        assertion in the file into a 500.

    NOT A GENERAL-PURPOSE ESCAPE. Anything that reads or writes real data wants
    `get_db`, whose failure is a 500 and should be: silently serving a partial
    answer is how a page renders "0 users" during an outage. Use this only where
    the missing fields are decoration and their absence is already a state the
    client renders.
    """
    # ACQUISITION is what degrades, and only acquisition. Wrapping the `yield`
    # in the same `try` would swallow the endpoint's own exceptions and then
    # yield a second time, which is a `RuntimeError` from the dependency
    # machinery rather than the 500 the route meant to raise.
    stack = ExitStack()
    try:
        conn = stack.enter_context(_borrow(request))
    except Exception:  # noqa: BLE001 - the whole point; see the docstring
        yield None
        return
    with stack:
        yield conn


def config_audit(request: Request, conn: db.Conn):
    """Who is writing a config document, stamped for `config_service.write_doc`.

    SHARED BY EVERY CONFIG WRITE PATH -- the editor's save, a draft apply, the
    setup wizard's first descriptor, and the hub's `/api/mgmt/domain` push. Four
    routers, one stamp: an audit that three of them produce and the fourth
    forgets is worse than none, because it reads as a complete record (ADR 0166).

    Time and ids are minted HERE because `services/` may not do either -- the
    determinism rule, and the same arrangement as `api/routers/learning.py`'s
    "Time enters HERE".

    `actor_id` is None whenever no `users` row is behind the request: the hub's
    management token, the root bearer, the env break-glass admin. That absence is
    recorded rather than filled in, for contract C1's reason -- a name nobody
    signed is invented provenance.
    """
    from datetime import UTC, datetime

    from venona import timeutil
    from venona.api import authz
    from venona.pipeline.types import SystemIdGen
    from venona.services import config_service

    return config_service.WriteAudit(
        conn=conn,
        actor_id=authz.actor_id(request) or None,
        event_id=f"cevent:{SystemIdGen().uuid7()}",
        now=timeutil.format_millis(datetime.now(UTC)),
    )
