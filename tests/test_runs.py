"""Run registry, event log, and SSE framing.

The event log is what makes the stream reliable — the transport is secondary.
These tests cover the two rules that are easy to get backwards (persist before
publish; allocate `seq` inside the transaction) and the reconnect path, which
only misbehaves at exactly the wrong moment and is therefore never caught by
hand.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os

import pytest
from venona.db import core as db
from venona.db.repositories import run as run_repo
from venona.pipeline.types import RunMessage
from venona.services import run_service, run_types


def _run(conn: db.Conn, run_id: str = "run:test") -> str:
    run_service.create_run(
        conn, run_id=run_id, kind="on_demand", params={}, profile_hash="abc", now="t0"
    )
    return run_id


def _abandoned_run(database_url: str, run_id: str = "run:orphan") -> str:
    """A run whose owner is provably gone.

    Created on its OWN connection, which is then closed. That is not a stand-in
    for a dead process -- it is the same event: Postgres drops a session advisory
    lock when the connection ends, whether it ended by `close()`, by SIGKILL, or
    by the machine going away.

    There is no column to null out any more. Liveness used to be `owner_pid` plus
    a `/proc` start time, both of which a test could write; a lock is not
    something a test can forge, which is most of the point.
    """
    owner = db.connect(database_url)
    try:
        run_service.create_run(
            owner, run_id=run_id, kind="on_demand", params={}, profile_hash="abc", now="t0"
        )
    finally:
        owner.close()
    return run_id


# ── the event log ────────────────────────────────────────────────────


def test_seq_is_monotonic_and_starts_at_one(conn: db.Conn) -> None:
    run_id = _run(conn)
    seqs = [
        run_service.append_event(
            conn, run_id=run_id, type_="log", stage="fetch", data={"i": i}, now="t"
        )["seq"]
        for i in range(5)
    ]
    assert seqs == [1, 2, 3, 4, 5]


def test_seq_is_per_run_not_global(conn: db.Conn) -> None:
    """Two runs must not share a sequence, or a replay from one would skip
    events in the other."""
    a, b = _run(conn, "run:a"), _run(conn, "run:b")
    run_service.append_event(conn, run_id=a, type_="log", stage=None, data={}, now="t")
    first_b = run_service.append_event(conn, run_id=b, type_="log", stage=None, data={}, now="t")
    assert first_b["seq"] == 1


def test_events_since_returns_only_what_is_missing(conn: db.Conn) -> None:
    """The core of lossless reconnect: give me everything after what I saw."""
    run_id = _run(conn)
    for i in range(6):
        run_service.append_event(
            conn, run_id=run_id, type_="log", stage=None, data={"i": i}, now="t"
        )

    missed = run_service.events_since(conn, run_id, after_seq=3)
    assert [e["seq"] for e in missed] == [4, 5, 6], "no gap, no duplicates"


def test_event_data_round_trips(conn: db.Conn) -> None:
    run_id = _run(conn)
    payload = {"total": 9, "current": 3, "nested": {"a": [1, 2]}}
    run_service.append_event(
        conn, run_id=run_id, type_="stage_progress", stage="fetch", data=payload, now="t"
    )
    assert run_service.events_since(conn, run_id)[0]["data"] == payload


# ── SSE framing (contract C6) ────────────────────────────────────────


def test_sse_frame_carries_the_seq_as_the_event_id() -> None:
    """`id:` is what the browser echoes back as `Last-Event-ID` on reconnect.
    Without it, a dropped connection replays from zero."""
    frame = run_service.format_sse(
        {
            "seq": 42,
            "run_id": "run:x",
            "ts": "t",
            "type": "stage_started",
            "stage": "fetch",
            "data": {},
        }
    )
    assert frame.startswith("id: 42\n")
    assert "event: stage_started\n" in frame
    assert frame.endswith("\n\n"), "a frame must end with a blank line"

    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["seq"] == 42


# ── run lifecycle ────────────────────────────────────────────────────


def test_partial_run_is_not_a_failure(conn: db.Conn) -> None:
    """A run that gathered something useful must not report `failed` — that
    teaches you to ignore the status field."""
    run_id = _run(conn)
    run_service.finish_run(
        conn,
        run_id=run_id,
        status="succeeded_partial",
        counters={"documents_new": 12},
        warnings=[RunMessage("warning", "one feed 403d")],
        brief_id="brief:2026-08-05",
        cost_usd=0.5,
        error=None,
        now="t1",
    )
    run = run_service.get_run(conn, run_id)
    assert run["status"] == "succeeded_partial"
    assert run["brief_id"], "a partial run still produces a brief"
    assert run["warnings"]


@pytest.mark.negative
def test_non_terminal_status_is_rejected(conn: db.Conn) -> None:
    run_id = _run(conn)
    with pytest.raises(ValueError):
        run_service.finish_run(
            conn,
            run_id=run_id,
            status="running",
            counters={},
            warnings=[],
            brief_id=None,
            cost_usd=0,
            error=None,
            now="t",
        )


def test_a_run_whose_owner_is_gone_is_reaped(conn: db.Conn, database_url: str) -> None:
    """A killed process leaves a run that looks live forever and the UI spins
    on it."""
    run_id = _abandoned_run(database_url)

    reaped = run_service.reap_interrupted(conn, now="t2")

    assert run_id in reaped
    run = run_service.get_run(conn, run_id)
    assert run["status"] == "failed"
    assert "interrupted" in run["error"]
    # AND IT IS NOT STILL INSIDE A STAGE. `runs.stage` means "what is happening
    # right now", so a finished run holding one has every surface that reads it
    # reporting work under a terminal header. The other two terminators clear
    # it; this one did not, and a production `on_demand` row settled
    # `succeeded_partial` while still carrying `stage='brief'`.
    assert run["stage"] is None


def test_a_recycled_pid_cannot_keep_a_dead_run_alive(conn: db.Conn, database_url: str) -> None:
    """THE container bug (migration 0020), and why the lease replaced `/proc`.

    The API is pid 1 -- or ~7 under an init shim -- so the previous container's
    `owner_pid` values land on the new container's own processes. The cmdline
    check passed (it really was uvicorn), liveness said True, and the lane was
    wedged forever because reaping skips live pids by design. Migration 0020
    answered it with a start time from `/proc`, which worked on one host.

    Here the pid is deliberately set to this live process, which is exactly the
    collision. It reaps anyway, because the pid is no longer what is asked.
    """
    run_id = _abandoned_run(database_url)
    conn.execute("UPDATE runs SET owner_pid = ? WHERE id = ?", (os.getpid(), run_id))
    conn.commit()

    assert run_id in run_service.reap_interrupted(conn, now="t2")
    assert run_service.lane_conflict(conn, lane=run_types.LIGHT) is None


def test_the_owner_holds_a_lease_for_as_long_as_it_holds_its_connection(
    conn: db.Conn,
) -> None:
    """The other direction: the real owner must never be reaped out from under
    itself. `create_run` claims the lease on the connection the run will use, so
    this is the live path rather than a simulation of it."""
    run_id = _run(conn)

    assert run_repo.lease_is_held(conn, run_id)
    assert run_service.reap_interrupted(conn, now="t2") == []
    assert run_service.get_run(conn, run_id)["status"] == "running"


def test_the_lease_is_released_when_the_connection_ends(database_url: str) -> None:
    """The whole mechanism in one assertion, from outside the owning session.

    Nothing times out and nothing heartbeats, so there is no window in which a
    dead run still looks alive -- and no risk of calling a live one dead because
    a heartbeat was late.
    """
    watcher = db.connect(database_url)
    owner = db.connect(database_url)
    try:
        run_service.create_run(
            owner, run_id="run:leased", kind="on_demand", params={}, profile_hash="a", now="t0"
        )
        assert run_repo.lease_is_held(watcher, "run:leased")
        owner.close()
        assert not run_repo.lease_is_held(watcher, "run:leased")
    finally:
        watcher.close()


def test_an_interrupted_run_that_did_work_is_partial_not_failed(
    conn: db.Conn, database_url: str
) -> None:
    """CLAUDE.md: "never let a partially useful run report as `failed`."

    Reaping used to follow a crash, so this was nearly theoretical. On a
    container it is routine -- every deploy SIGKILLs what is in flight, and a
    pipeline killed at minute 35 has already written documents, clusters and a
    brief.
    """
    run_id = _abandoned_run(database_url)
    run_service.record_stage(
        conn, run_id=run_id, stage="fetch", status="ok", started_at="t0", finished_at="t1"
    )

    assert run_id in run_service.reap_interrupted(conn, now="t2")
    assert run_service.get_run(conn, run_id)["status"] == "succeeded_partial"


def test_an_interrupted_run_that_did_nothing_is_still_failed(
    conn: db.Conn, database_url: str
) -> None:
    """The complement. Nothing completed, so there is nothing to preserve and
    `failed` is the honest answer."""
    run_id = _abandoned_run(database_url)
    run_service.record_stage(
        conn, run_id=run_id, stage="fetch", status="error", started_at="t0", error="boom"
    )

    assert run_id in run_service.reap_interrupted(conn, now="t2")
    assert run_service.get_run(conn, run_id)["status"] == "failed"


@pytest.mark.negative
def test_a_live_run_in_another_process_is_not_reaped(conn: db.Conn) -> None:
    """The bug this replaced, caught in the act.

    `venona run` is a separate process from the API server, so "the server
    started" is not evidence that a run died. The unqualified version reaped a
    live CLI run 25 minutes into `fetch` because an edit triggered uvicorn's
    reloader — and because the run finished normally an hour later and
    overwrote the status, the only trace was a `failed` row someone believed.
    """
    run_id = _run(conn)  # owner_pid is this pytest process, which is alive

    assert run_service.reap_interrupted(conn, now="t2") == []
    assert run_service.get_run(conn, run_id)["status"] == "running"


# ── the registry ─────────────────────────────────────────────────────
#
# Contract C11. These used to assert a single global slot; the lane split is
# what changed, not the mechanism. `submit` never refuses -- a full lane queues
# -- so "was it admitted" is `handle.admitted.is_set()`, not an exception.


async def test_only_one_research_run_may_be_active() -> None:
    """Two pipelines write `documents`/`clusters` with two separate budgets, so
    the research lane is still exactly one at a time. What is new is that the
    SECOND one waits instead of being refused outright."""
    registry = run_service.RunRegistry()
    first = await registry.submit("run:first", "on_demand")
    assert first.admitted.is_set()
    assert registry.lane_full(run_types.RESEARCH)

    second = await registry.submit("run:second", "daily")
    assert not second.admitted.is_set(), "a second research run must not start"

    await registry.release("run:first")
    assert second.admitted.is_set(), "releasing the slot must start the queued run"


async def test_a_lookup_runs_alongside_a_research_run() -> None:
    """The regression this whole contract exists to prevent.

    Grounding one acronym was refused for the tens of minutes a research run
    takes -- which is exactly the window in which someone is reading a brief and
    hits an undefined term.
    """
    registry = run_service.RunRegistry()
    await registry.submit("run:research", "on_demand")

    lookup = await registry.submit("run:lookup", "lookup")
    assert lookup.admitted.is_set()
    assert lookup.lane == run_types.LIGHT


async def test_the_light_lane_queues_in_order() -> None:
    """Excess light runs queue rather than 409. FIFO, because the order someone
    clicked things in is the order they expect them back."""
    registry = run_service.RunRegistry()
    slots = run_types.LANES[run_types.LIGHT]

    admitted = [await registry.submit(f"run:live{i}", "lookup") for i in range(slots)]
    assert all(h.admitted.is_set() for h in admitted)

    queued = [await registry.submit(f"run:wait{i}", "lookup") for i in range(2)]
    assert not any(h.admitted.is_set() for h in queued)
    assert [h.run_id for h in registry.queued(run_types.LIGHT)] == ["run:wait0", "run:wait1"]

    await registry.release("run:live0")
    assert queued[0].admitted.is_set()
    assert not queued[1].admitted.is_set(), "one slot freed admits exactly one run"


async def test_a_queued_run_can_be_cancelled_before_it_starts() -> None:
    """Cancelling a queued run must wake it so it can finish `cancelled`,
    rather than leaving it holding a queue position until a slot happens to
    free -- which on an idle system is never."""
    registry = run_service.RunRegistry()
    for i in range(run_types.LANES[run_types.LIGHT]):
        await registry.submit(f"run:live{i}", "lookup")
    waiting = await registry.submit("run:waiting", "lookup")

    assert await registry.cancel("run:waiting")
    assert waiting.cancelled
    assert waiting.admitted.is_set(), "a cancelled run must not block on admission"
    assert registry.queued(run_types.LIGHT) == []


async def test_publish_reaches_every_subscriber() -> None:
    registry = run_service.RunRegistry()
    await registry.submit("run:x", "on_demand")
    a, b = registry.subscribe("run:x"), registry.subscribe("run:x")
    assert a and b

    registry.publish("run:x", {"seq": 1, "type": "log"})
    assert (await a.get())["seq"] == 1
    assert (await b.get())["seq"] == 1


async def test_concurrent_runs_do_not_cross_talk() -> None:
    """Everything is keyed by run_id, not by "the active run".

    The old version returned early from `publish`/`subscribe` unless the id
    matched the one active run. Left alone, every light run's events would have
    been routed into the 1 Hz log-tail fallback -- the transcript would read as
    laggy, and nothing would say why.
    """
    registry = run_service.RunRegistry()
    await registry.submit("run:research", "on_demand")
    await registry.submit("run:lookup", "lookup")

    heavy = registry.subscribe("run:research")
    light = registry.subscribe("run:lookup")
    assert heavy and light

    registry.publish("run:research", {"seq": 1, "type": "log"})
    registry.publish("run:lookup", {"seq": 9, "type": "log"})

    assert (await heavy.get())["seq"] == 1
    assert (await light.get())["seq"] == 9
    assert heavy.empty() and light.empty()


async def test_a_stalled_subscriber_cannot_block_the_pipeline() -> None:
    """A reader that stops draining must not wedge the run. It catches up from
    the log on reconnect."""
    registry = run_service.RunRegistry()
    await registry.submit("run:x", "on_demand")
    queue = registry.subscribe("run:x")
    assert queue

    for i in range(queue.maxsize + 50):
        registry.publish("run:x", {"seq": i, "type": "log"})  # must not raise

    assert queue.full()


async def test_release_wakes_listeners() -> None:
    """A stream attached to a run that ended abnormally must not hang."""
    registry = run_service.RunRegistry()
    await registry.submit("run:x", "on_demand")
    queue = registry.subscribe("run:x")
    assert queue

    await registry.release("run:x")
    assert await asyncio.wait_for(queue.get(), timeout=1) is None


async def test_release_disarms_the_force() -> None:
    """A run that stops on its own inside the grace leaves a timer armed against
    it. Left running, it would wake after the run had ended and cancel whatever
    that task had become."""
    registry = run_service.RunRegistry()
    handle = await registry.submit("run:x", "on_demand")
    handle.task = asyncio.create_task(asyncio.sleep(60))

    assert await registry.cancel("run:x", grace=30)
    forcer = handle.forcer
    assert forcer is not None and not forcer.done()

    await registry.release("run:x")
    with contextlib.suppress(asyncio.CancelledError):
        await forcer
    assert forcer.cancelled() or forcer.done()
    handle.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await handle.task


async def test_a_second_cancel_forces_at_once() -> None:
    """The escape hatch when the grace was not enough, and what makes the
    endpoint idempotent: pressing again does not re-ask, it stops the run."""
    registry = run_service.RunRegistry()
    handle = await registry.submit("run:x", "on_demand")
    handle.task = asyncio.create_task(asyncio.sleep(60))

    assert await registry.cancel("run:x", grace=30)
    assert not handle.task.done()

    assert await registry.cancel("run:x", grace=30)
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(handle.task, timeout=2)
    assert handle.task.cancelled()


async def test_cancel_only_affects_the_run_it_names() -> None:
    registry = run_service.RunRegistry()
    await registry.submit("run:x", "on_demand")
    await registry.submit("run:y", "lookup")

    assert await registry.cancel("run:x")
    assert registry.handle("run:x").cancelled
    assert not registry.handle("run:y").cancelled
    assert not await registry.cancel("run:never-submitted")


# ── replay ───────────────────────────────────────────────────────────


async def test_finished_run_still_ends_with_a_terminal_event(
    conn: db.Conn,
) -> None:
    """Attaching to an already-finished run must still yield `done` — otherwise
    the client's EventSource reconnects forever."""
    run_id = _run(conn)
    run_service.append_event(
        conn, run_id=run_id, type_="stage_started", stage="fetch", data={}, now="t"
    )
    run_service.finish_run(
        conn,
        run_id=run_id,
        status="succeeded",
        counters={},
        warnings=[],
        brief_id=None,
        cost_usd=0,
        error=None,
        now="t1",
    )

    chunks = [chunk async for chunk in run_service.stream_events(conn, run_id)]
    assert any("event: done" in c for c in chunks)


async def test_replay_from_a_seq_skips_what_was_already_seen(
    conn: db.Conn,
) -> None:
    run_id = _run(conn)
    for i in range(4):
        run_service.append_event(
            conn, run_id=run_id, type_="log", stage=None, data={"i": i}, now="t"
        )
    run_service.finish_run(
        conn,
        run_id=run_id,
        status="succeeded",
        counters={},
        warnings=[],
        brief_id=None,
        cost_usd=0,
        error=None,
        now="t1",
    )

    chunks = [c async for c in run_service.stream_events(conn, run_id, after_seq=2)]
    ids = [c.split("\n")[0] for c in chunks if c.startswith("id: ")]
    assert "id: 1" not in ids and "id: 2" not in ids
    assert "id: 3" in ids and "id: 4" in ids


# ── streaming a run this process does not own ────────────────────────


async def test_a_run_in_another_process_streams_live(conn: db.Conn) -> None:
    """The bug behind "the transcript does not auto-update".

    `registry` is an in-process `asyncio.Lock` and `venona run` is a SEPARATE
    process, so `subscribe()` returns None for every CLI-launched run. The old
    code read that as "already finished", fabricated a `done` carrying the run's
    current (often non-terminal) status, and the browser's EventSource closed
    for good — replay, then silence, forever.

    The log is the source of truth. If nobody in-process owns the run, tail it.
    """
    run_id = _run(conn)  # owner_pid is this process, and it is alive
    run_service.append_event(
        conn, run_id=run_id, type_="model_message", stage="analyze", data={"kind": "text"}, now="t"
    )

    seen: list[str] = []

    async def read() -> None:
        async for chunk in run_service.stream_events(conn, run_id, after_seq=0):
            seen.append(chunk)

    task = asyncio.create_task(read())
    await asyncio.sleep(0.05)

    # An event written AFTER the stream attached must still arrive — that is
    # what "live" means, and what the fabricated `done` used to prevent.
    run_service.append_event(
        conn, run_id=run_id, type_="log", stage="analyze", data={"message": "later"}, now="t"
    )
    await asyncio.sleep(run_service.TAIL_POLL_SECONDS * 2.5)

    run_service.finish_run(
        conn,
        run_id=run_id,
        status="succeeded",
        counters={},
        warnings=[],
        brief_id=None,
        cost_usd=0.0,
        error=None,
        now="t",
    )
    await asyncio.wait_for(task, timeout=5)

    body = "".join(seen)
    assert "model_message" in body, "the replay must still work"
    assert "later" in body, "an event written after attach must arrive"
    assert "event: done" in body, "the stream must terminate so EventSource closes"


async def test_tailing_gives_up_when_the_owner_dies(conn: db.Conn, database_url: str) -> None:
    """Otherwise a client watching a crashed run holds an open stream forever."""
    run_id = _abandoned_run(database_url)

    chunks = [c async for c in run_service.stream_events(conn, run_id, after_seq=0)]
    body = "".join(chunks)
    assert "event: done" in body
    assert "owner process is gone" in body


# ── hiding run history ───────────────────────────────────────────────
#
# History is tidied by hiding, never by deleting. `documents.run_id`,
# `sightings.run_id` (NOT NULL), `assertions.run_id` (NOT NULL) and
# `briefs.run_id` all carry a run's id with no foreign key behind them, so a
# DELETE would silently orphan provenance in a system whose first invariant is
# that Postgres is the record of everything.
#
# A deletion gate that protected them would also have been useless: the
# orchestrator only reports `failed` when NO stage committed, so every run that
# gets past `fetch` is `succeeded_partial` with `documents` rows, and on the
# live database that was all nine of them.


def test_hidden_runs_leave_the_default_listing_but_are_never_destroyed(
    conn: db.Conn,
) -> None:
    kept = _run(conn, "run:kept")
    noise = _run(conn, "run:noise")
    run_service.finish_run(
        conn,
        run_id=noise,
        status="cancelled",
        counters={},
        warnings=[],
        brief_id=None,
        cost_usd=0.0,
        error=None,
        now="t1",
    )

    run_service.hide_run(conn, noise, now="t2")

    visible, total, hidden = run_service.list_runs(conn)
    assert [r["id"] for r in visible] == [kept]
    assert (total, hidden) == (1, 1)

    everything, total_all, _ = run_service.list_runs(conn, include_hidden=True)
    assert {r["id"] for r in everything} == {kept, noise}
    assert total_all == 2

    # The row itself is untouched apart from the timestamp.
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (noise,)).fetchone()
    assert row["hidden_at"] == "t2"
    assert row["status"] == "cancelled"


def _terminal(conn: db.Conn, run_id: str, status: str = "succeeded") -> str:
    """`create_run` records THIS process's pid and leaves the run `running`, so
    a freshly created run is correctly judged live. Finish it first."""
    run_service.finish_run(
        conn,
        run_id=run_id,
        status=status,
        counters={},
        warnings=[],
        brief_id=None,
        cost_usd=0.0,
        error=None,
        now="t1",
    )
    return run_id


def test_unhide_restores_the_run(conn: db.Conn) -> None:
    run_id = _terminal(conn, _run(conn))
    run_service.hide_run(conn, run_id, now="t1")
    run_service.unhide_run(conn, run_id)

    visible, _, hidden = run_service.list_runs(conn)
    assert [r["id"] for r in visible] == [run_id]
    assert hidden == 0


@pytest.mark.negative
def test_unhiding_a_run_that_does_not_exist_is_a_lookup_error(conn: db.Conn) -> None:
    """The UPDATE matched zero rows and still returned `{run_id, hidden_at: None}`,
    so a typo'd or already-reaped id read as a successful unhide. `hide_run` has
    always raised here; the two endpoints share a response model and must not
    disagree about whether they check their target."""
    with pytest.raises(LookupError):
        run_service.unhide_run(conn, "run:does-not-exist")


def test_a_live_run_can_still_be_unhidden(conn: db.Conn) -> None:
    """No `_assert_not_live` on the way out. That guard is about not hiding a run
    someone is watching; refusing to UNhide one would make a hidden run
    unrecoverable until it finished."""
    run_id = _run(conn)
    conn.execute("UPDATE runs SET hidden_at = ? WHERE id = ?", ("t1", run_id))

    assert run_service.unhide_run(conn, run_id) == {"run_id": run_id, "hidden_at": None}


def test_a_run_whose_owner_is_alive_cannot_be_hidden(conn: db.Conn) -> None:
    """The gate is `runs.status` + a held LEASE, NOT the registry.

    `RunRegistry` is an in-process `asyncio.Lock`, so it cannot see a run
    started by `venona run` in a separate process. Trusting it here would
    reproduce the bug migration 0008 exists to fix, one layer up.
    """
    run_id = _run(conn)  # `create_run` leased it on this connection

    with pytest.raises(run_service.RunIsLive):
        run_service.hide_run(conn, run_id, now="t1")

    assert run_service.list_runs(conn)[2] == 0


def test_a_running_row_whose_owner_is_gone_can_be_hidden(conn: db.Conn, database_url: str) -> None:
    """An interrupted run is exactly the kind of row this feature is for."""
    run_id = _abandoned_run(database_url)

    run_service.hide_run(conn, run_id, now="t1")
    assert run_service.list_runs(conn)[2] == 1


def test_hiding_a_missing_run_raises_rather_than_silently_succeeding(
    conn: db.Conn,
) -> None:
    with pytest.raises(LookupError):
        run_service.hide_run(conn, "run:does-not-exist", now="t1")


def test_clear_hides_everything_but_skips_a_live_run(conn: db.Conn) -> None:
    """ "Clear the history" is a request about the past. Failing the whole
    operation because one run is in flight would be obtuse; silently counting
    it as hidden would be a lie."""
    _terminal(conn, _run(conn, "run:old-a"))
    _terminal(conn, _run(conn, "run:old-b"), status="failed")
    live = _run(conn, "run:live")  # leased on this connection, so live

    result = run_service.hide_all(conn, now="t9")
    assert result == {"hidden": 2, "skipped": 1}
    assert [r["id"] for r in run_service.list_runs(conn)[0]] == [live]


# ── who started it ───────────────────────────────────────────────────


def test_a_run_carries_the_address_of_whoever_started_it(conn: db.Conn) -> None:
    """`runs.started_by` has been WRITTEN on every launch since identity existed
    -- `run_launcher` passes it -- and read by nothing.

    So "who started the run that spent this" was a question the database could
    answer and no surface asked, on the one table in the app that has a cost
    column. The id is stored; the address is what comes back, for the reason
    every other actor column in this app gives: `user:0198…` on screen tells a
    reader nothing.
    """
    from venona.services import user_service

    user_service.create(
        conn,
        user_id="user:starter",
        email="starter@example.com",
        role="admin",
        now="t0",
        allow_admin=True,
    )
    run_service.create_run(
        conn,
        run_id="run:signed",
        kind="on_demand",
        params={},
        profile_hash="abc",
        now="t0",
        started_by="user:starter",
    )

    assert run_service.get_run(conn, "run:signed")["started_by_email"] == "starter@example.com"
    items, _total, _hidden = run_service.list_runs(conn)
    assert [r["started_by_email"] for r in items if r["id"] == "run:signed"] == [
        "starter@example.com"
    ]


def test_a_scheduled_run_carries_no_name_rather_than_a_wrong_one(conn: db.Conn) -> None:
    """The scheduler and `venona run` have no principal, and NULL is the honest
    record of that. An INNER JOIN here would have hidden every such run from the
    history instead of hiding the missing name."""
    _run(conn, "run:nightly")

    assert run_service.get_run(conn, "run:nightly")["started_by_email"] is None
    items, _total, _hidden = run_service.list_runs(conn)
    assert [r["id"] for r in items] == ["run:nightly"]


# ── the replay reaches the end of the log ────────────────────────────


def test_a_replay_returns_every_event_past_one_page(conn: db.Conn) -> None:
    """⭐ A reconnect is lossless because the log is durable and gets replayed.

    A single capped read cannot deliver that: a run past one page of events --
    and `model_message` alone puts a long one there -- handed a reconnecting
    client the OLDEST page and silently dropped everything after it, which is
    the half the client is reconnecting for.
    """
    run_id = _run(conn, "run:paged")
    for i in range(7):
        run_service.append_event(
            conn, run_id=run_id, type_="log", stage="fetch", data={"i": i}, now="t1"
        )

    paged = list(run_service.iter_events_since(conn, run_id, after_seq=0, page=2))

    assert [e["seq"] for e in paged] == list(range(1, 8)), (
        "the replay must walk past the page size, not stop at it"
    )


def test_a_replay_resumes_from_a_cursor(conn: db.Conn) -> None:
    """`Last-Event-ID` is a seq, and everything after it is what a reconnect owes
    the client."""
    run_id = _run(conn, "run:resume")
    for i in range(5):
        run_service.append_event(
            conn, run_id=run_id, type_="log", stage="fetch", data={"i": i}, now="t1"
        )

    resumed = list(run_service.iter_events_since(conn, run_id, after_seq=3, page=2))
    assert [e["seq"] for e in resumed] == [4, 5]


def test_the_poll_route_says_whether_more_is_waiting(conn: db.Conn) -> None:
    """`last_seq` alone cannot tell a poller it has reached the end of the log
    rather than the end of a page, and the two demand opposite behaviour."""
    run_id = _run(conn, "run:hasmore")
    for i in range(4):
        run_service.append_event(
            conn, run_id=run_id, type_="log", stage="fetch", data={"i": i}, now="t1"
        )

    first = run_service.events_since(conn, run_id, after_seq=0, limit=2)
    assert len(first) == 2
    rest = run_service.events_since(conn, run_id, after_seq=first[-1]["seq"], limit=2)
    assert [e["seq"] for e in rest] == [3, 4]


# ── the list is where a dead run gets noticed ────────────────────────


@pytest.mark.anyio
async def test_the_runs_list_reaps_a_run_whose_owner_died(api_client, api_db, api_settings) -> None:
    """⭐ The other caller of `reap_interrupted` is the API lifespan.

    So a run killed while the API stayed up -- a `venona run` whose terminal
    was closed, a scheduler tick in a worker that died -- kept a spinner
    turning until someone restarted the server, and this page is exactly where
    a person goes to ask why nothing is happening.
    """
    owner = db.connect(api_settings.database_url)
    try:
        run_service.create_run(
            owner, run_id="run:dead", kind="on_demand", params={}, profile_hash="h", now="t0"
        )
    finally:
        # Closing the connection drops the session advisory lease, which IS the
        # evidence that the owner is gone.
        owner.close()

    assert run_service.get_run(api_db, "run:dead")["status"] == "running"

    response = await api_client.get("/api/runs")
    assert response.status_code == 200

    assert run_service.get_run(api_db, "run:dead")["status"] in ("failed", "succeeded_partial"), (
        "the list must settle a run nothing is executing rather than render it as live"
    )
