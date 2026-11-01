"""The request connection pool.

What this guards is not throughput, it is a failure mode that lies about its
cause: every authenticated request opens two connections of its own before it
reaches a route, so an unbounded request path walks into the server's
`max_connections` under ordinary concurrency. Past that line `psycopg.connect`
raises, the session lookup fails CLOSED by design, and the app renders as every
signed-in user being signed out while the database sits there healthy.
"""

from __future__ import annotations

from types import SimpleNamespace

from venona.api import deps
from venona.db import core as db


def _request(database_url: str, pool=None) -> SimpleNamespace:
    state = SimpleNamespace(settings=SimpleNamespace(database_url=database_url))
    if pool is not None:
        state.db_pool = pool
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_a_pooled_connection_is_returned_and_reused(database_url: str) -> None:
    """⭐ The leak test. `max_size=1` means the second borrow can only succeed if
    the first was given back: a connection that escapes the pool does not raise,
    it makes the next caller wait for a slot that is never coming."""
    pool = db.pool(database_url, max_size=1)
    pool.open(wait=True, timeout=10)
    try:
        answers = []
        for _ in range(3):
            with db.from_pool(pool, database_url) as conn:
                answers.append(conn.execute("SELECT 1 AS n").fetchone()["n"])
        assert answers == [1, 1, 1]
    finally:
        pool.close()


def test_pooled_connections_carry_the_autocommit_contract(database_url: str) -> None:
    """AUTOCOMMIT IS LOAD-BEARING (`db/core.py`'s module docstring): ~52 write
    sites live outside a `transaction()` block and depend on it. A pooled
    connection without it opens an implicit transaction that is discarded when
    the connection returns to the pool -- silent write loss on most write paths,
    and invisible to any test that reads its own writes back on one connection."""
    pool = db.pool(database_url, max_size=1)
    assert pool.kwargs["autocommit"] is True
    assert pool.kwargs["row_factory"] is db._row_factory  # noqa: SLF001 - the contract
    pool.open(wait=True, timeout=10)
    try:
        with db.from_pool(pool, database_url) as conn:
            assert conn.raw.autocommit is True
            # The row factory reaches the caller as `Row`, which is what lets
            # `row[0]` and `row["col"]` both work across ~125 modules.
            row = conn.execute("SELECT 1 AS n").fetchone()
            assert row["n"] == row[0] == 1
    finally:
        pool.close()


def test_the_request_path_borrows_from_the_pool_and_gives_it_back(database_url: str) -> None:
    """`get_db` is the dependency every route takes. If it opened its own
    connection while a pool existed, the pool would bound nothing."""
    pool = db.pool(database_url, max_size=1)
    pool.open(wait=True, timeout=10)
    try:
        request = _request(database_url, pool)
        generator = deps.get_db(request)
        conn = next(generator)
        assert conn.execute("SELECT 1 AS n").fetchone()["n"] == 1
        assert pool.get_stats()["pool_available"] == 0, "the request did not draw from the pool"
        generator.close()
        assert pool.get_stats()["pool_available"] == 1, "the request did not return its connection"
    finally:
        pool.close()


def test_the_request_path_still_works_with_no_pool(database_url: str) -> None:
    """`httpx.ASGITransport` runs no lifespan, so `app.state.db_pool` does not
    exist under any API test in this repo. The fallback is what keeps those
    tests from needing a pool fixture to say anything about a route."""
    generator = deps.get_db(_request(database_url))
    conn = next(generator)
    assert conn.execute("SELECT 1 AS n").fetchone()["n"] == 1
    generator.close()


def test_the_optional_dependency_yields_a_connection_when_it_can(database_url: str) -> None:
    """Degrading is the exception, not the habit: when there is a database the
    session endpoint reads the user's row like anything else."""
    generator = deps.get_db_optional(_request(database_url))
    conn = next(generator)
    assert conn is not None
    assert conn.execute("SELECT 1 AS n").fetchone()["n"] == 1
    generator.close()


def test_the_optional_dependency_degrades_instead_of_raising() -> None:
    """`GET /api/auth/session` must answer without a database: the break-glass
    bearer path is exactly the moment the database is the thing you are worried
    about, and the SPA cannot mount without that endpoint."""
    generator = deps.get_db_optional(_request("postgresql://nobody@127.0.0.1:1/nothing"))
    assert next(generator) is None
    generator.close()
