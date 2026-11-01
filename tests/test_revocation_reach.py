"""Revocation has to reach a credential that is already in use.

Suspending an account stops the NEXT request. That is enough for every route in
the app except one: `GET /api/runs/{run_id}/stream` passes `AuthMiddleware` once
and then holds its connection for up to forty minutes, yielding `model_message`
frames -- the live Claude session, prompts and all, which is exactly why the
route is admin-only. "This account is compromised, suspend it" has to reach the
session that is already reading, or suspension is a promise the product does not
keep.

WHAT CLOSING LOOKS LIKE, and why it is not a `done` frame: the generator simply
stops. A synthesised `done` would tell the client the RUN finished, which is a
statement about the run and false; the run is untouched. The client's
`EventSource` reconnects, and the reconnect meets a boundary that can answer
properly -- with a 403, through which `EventSource` does not retry again.

The second credential that outlives a check -- an authorization code approved
before suspension and exchanged after -- is refused in `routers/oauth.py`'s token
endpoint, and is pinned beside the rest of that flow in `tests/test_oauth.py`.
"""

from __future__ import annotations

import asyncio

import pytest
from venona.api import authz
from venona.db import core as db
from venona.services import run_service, user_service

NOW = "2026-08-17T00:00:00Z"
EPOCH = 1_800_000_000.0
VIEWER = "user:viewer"


@pytest.fixture(autouse=True)
def _check_every_poll(monkeypatch) -> None:
    """The gate is a timer, and a test must not wait on it.

    Zero means "due on every pass", which is the same code path with the clock
    taken out -- not a different one. The first pass therefore also checks, so
    every test here proves the stream survives an authorized check before it
    proves anything about a revoked one.
    """
    monkeypatch.setattr(run_service, "REVOCATION_RECHECK_SECONDS", 0.0)


@pytest.fixture
def watched_run(conn: db.Conn) -> str:
    """An admin, a second admin so the first can be demoted, and a live run.

    The run is created on this connection, so the process holds its lease and
    `_tail_log` keeps polling rather than giving up on a dead owner -- the same
    shape as `test_a_run_in_another_process_streams_live`.
    """
    user_service.create(
        conn,
        user_id=VIEWER,
        email="viewer@example.com",
        role=authz.ADMIN,
        now=NOW,
        allow_admin=True,
    )
    user_service.create(
        conn,
        user_id="user:other",
        email="other@example.com",
        role=authz.ADMIN,
        now=NOW,
        allow_admin=True,
    )
    run_service.create_run(
        conn, run_id="run:watched", kind="on_demand", params={}, profile_hash="abc", now=NOW
    )
    run_service.append_event(
        conn,
        run_id="run:watched",
        type_="model_message",
        stage="analyze",
        data={"kind": "text", "text": "the prompt"},
        now=NOW,
    )
    return "run:watched"


def _revoke(conn: db.Conn, **kw) -> None:
    with db.transaction(conn, what="test_revoke"):
        user_service.change_authority(
            conn,
            user_id=VIEWER,
            actor="user:other",
            event_id="uevent:revoke",
            now=NOW,
            epoch=EPOCH,
            **kw,
        )


async def _read(conn: db.Conn, run_id: str, viewer_id: str | None) -> tuple[asyncio.Task, list]:
    seen: list[str] = []

    async def pump() -> None:
        async for chunk in run_service.stream_events(
            conn, run_id, after_seq=0, viewer_id=viewer_id
        ):
            seen.append(chunk)

    task = asyncio.create_task(pump())
    # Long enough for the replay and at least one poll of the tail loop.
    await asyncio.sleep(run_service.TAIL_POLL_SECONDS * 1.5)
    assert not task.done(), "an authorized viewer's stream must still be open"
    assert any("model_message" in c for c in seen), "the replay must have arrived"
    return task, seen


async def _closed(task: asyncio.Task) -> None:
    await asyncio.wait_for(task, timeout=5)


# ── the stream ───────────────────────────────────────────────────────


async def test_suspending_a_viewer_closes_the_stream_they_are_reading(
    conn: db.Conn, watched_run: str
) -> None:
    """⭐ The one this file exists for. Everything else here is a variation."""
    task, seen = await _read(conn, watched_run, VIEWER)

    _revoke(conn, status=user_service.SUSPENDED)
    await _closed(task)

    body = "".join(seen)
    assert "event: done" not in body, (
        "the run did not finish -- a synthesised `done` would say it had, and the "
        "client would stop reconnecting for the wrong reason"
    )
    assert run_service.get_run(conn, watched_run)["status"] not in run_service.TERMINAL_STATUSES


async def test_demoting_a_viewer_closes_the_stream_too(conn: db.Conn, watched_run: str) -> None:
    """Suspension is not the only revocation. A reader may not read this route at
    all -- `model_message` is why -- so losing `admin` has to land the same way,
    and a status-only check would leave a demoted admin watching."""
    task, _ = await _read(conn, watched_run, VIEWER)

    _revoke(conn, role=authz.READER)
    await _closed(task)


async def test_a_principal_with_no_user_row_is_not_cut_off(conn: db.Conn, watched_run: str) -> None:
    """The root bearer, `via="disabled"` and the env break-glass admin all carry
    a falsy `user_id` -- there is no row to re-read. Resolving one anyway finds
    nothing, and "no row" would read as "revoked": `make dev` and every
    break-glass session would lose its stream a few seconds in.

    Those credentials are revoked by rotating the token, not by this gate.
    """
    task, seen = await _read(conn, watched_run, "")

    await asyncio.sleep(run_service.TAIL_POLL_SECONDS * 2)
    assert not task.done(), "a principal with no row must keep its stream"

    run_service.finish_run(
        conn,
        run_id=watched_run,
        status="succeeded",
        counters={},
        warnings=[],
        brief_id=None,
        cost_usd=0.0,
        error=None,
        now=NOW,
    )
    await _closed(task)
    assert "event: done" in "".join(seen), "a finished run still ends the stream normally"


async def test_a_database_that_cannot_answer_closes_the_stream(
    conn: db.Conn, watched_run: str, monkeypatch
) -> None:
    """Fail closed, matching the correction `AuthMiddleware._lookup_user` made
    for the same question: an unreachable database is not evidence that the
    reader is still authorized. The run keeps running; only the stream ends."""
    task, _ = await _read(conn, watched_run, VIEWER)

    def _explode(*_args, **_kw):
        raise RuntimeError("connection is gone")

    monkeypatch.setattr(user_service, "get", _explode)
    await _closed(task)


async def test_the_gate_is_a_timer_rather_than_a_read_per_event(
    conn: db.Conn, watched_run: str, monkeypatch
) -> None:
    """The check costs a query, and a busy run emits events faster than it is
    worth paying for one. Pinning the timer keeps a later "just check every
    iteration" from turning one open stream into a few hundred reads a minute.

    The interval is also why the check is NOT counted in heartbeats: heartbeats
    fire only when the stream is idle, so a heartbeat-counted gate would never
    fire on a busy stream -- which is the stream worth cutting.
    """
    monkeypatch.setattr(run_service, "REVOCATION_RECHECK_SECONDS", 3600.0)
    reads = 0
    real = user_service.get

    def _counted(*args, **kw):
        nonlocal reads
        reads += 1
        return real(*args, **kw)

    monkeypatch.setattr(user_service, "get", _counted)

    task, _ = await _read(conn, watched_run, VIEWER)
    await asyncio.sleep(run_service.TAIL_POLL_SECONDS * 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert reads == 0, f"{reads} reads inside one interval; the gate stopped being a timer"
