"""Run registry and the event log behind the SSE stream.

The DURABILITY is the property, not the transport: the stream is a *view* over
an append-only log in Postgres, which is what makes "close the laptop mid-run,
come back, see everything" work. Contract C6 carries the two rules that are
easy to get backwards -- persist before publish, and allocate `seq` inside the
insert transaction.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from venona.db import core as db
from venona.db.repositories import run as repo
from venona.pipeline import types
from venona.pipeline.types import RunMessage
from venona.services import run_types

log = logging.getLogger("venona.runs")

# Contract C6.
EVENT_TYPES = (
    "stage_started",
    "stage_progress",
    "stage_finished",
    "counter",
    "log",
    "warning",
    "error",
    # The live Claude Code session behind a stage. Purely additive: a client
    # that never subscribes sees an unchanged stage strip and counters.
    "model_message",
    "done",
)

TERMINAL_STATUSES = ("succeeded", "succeeded_partial", "failed", "cancelled")

#: What a run FOUND, as opposed to whether it ran. ADR 0183, contract C6.
#:
#: The collision the split ended was not theoretical: 44 of 68 lookups on the
#: defence corpus read `succeeded_partial` with an empty `warnings` array and a
#: null `error`, because "the web has no definition for this" had nowhere else
#: to go. `some` rather than `partial`, so that no row carries two
#: near-identical words meaning different things.
OUTCOMES = ("delivered", "some", "nothing_found", "nothing_to_do")

HEARTBEAT_SECONDS = 15

#: How long a cancelled run is given to stop on its own before the task is
#: cancelled for it. Long enough that a run between items unwinds through
#: `RunCancelled` and writes its own considered terminal row; short enough that
#: whoever pressed cancel does not sit through the model call already in flight.
#: Not a setting -- there is no operator decision here. ADR 0231, and
#: `test_health_says_a_cancelled_run_is_still_running` records what cooperative
#: cancellation alone cost: the active run ten minutes after an accepted cancel,
#: spending throughout, ended only by bringing the process down.
CANCEL_GRACE_SECONDS = 5.0


@dataclass
class RunHandle:
    run_id: str
    kind: str = "on_demand"
    lane: str = run_types.RESEARCH
    # What this run is about, so an identical request already in flight can be
    # handed the same run rather than paying twice. Held here rather than read
    # back from `runs`: the duplicates worth catching all come from one browser,
    # the same card hovered twice or the queue and the card asking together.
    params: dict[str, Any] = field(default_factory=dict)
    task: asyncio.Task | None = None
    cancelled: bool = False
    # The armed force, held here so it is neither garbage collected while it
    # sleeps nor left ticking after the run it was armed for has ended.
    forcer: asyncio.Task | None = None
    # Set when the run holds a lane slot and may begin work; a run submitted
    # into a full lane waits on this rather than being refused (`submit`).
    admitted: asyncio.Event = field(default_factory=asyncio.Event)
    subscribers: list[asyncio.Queue] = field(default_factory=list)


class RunRegistry:
    """Contract C11: one slot per lane, enforced in-process.

    An `asyncio.Lock` rather than a `run_locks` table, because what this object
    guards is admission WITHIN one event loop: which coroutine may begin work and
    which waits on `handle.admitted`. The cross-process question -- `venona run`
    is a separate pid -- is not answerable here at any granularity, and belongs
    to `create_run_guarded`'s lane lock and the per-run lease. `run_types.LANES`
    sizes each lane.

    **Everything here is keyed by `run_id`, not by "the active run".** Returning
    early from `publish`/`subscribe` unless the id matches one active run
    silently routes every light run's events into the 1 Hz log-tail fallback,
    and the transcript reads as laggy for no discoverable reason.
    """

    def __init__(self, lanes: Mapping[str, int] | None = None) -> None:
        self._lock = asyncio.Lock()
        self._lanes: dict[str, int] = dict(lanes if lanes is not None else run_types.LANES)
        self._handles: dict[str, RunHandle] = {}
        self._running: dict[str, list[str]] = {lane: [] for lane in self._lanes}
        self._queues: dict[str, deque[str]] = {lane: deque() for lane in self._lanes}

    # ── introspection ────────────────────────────────────────────────
    def handle(self, run_id: str) -> RunHandle | None:
        return self._handles.get(run_id)

    def capacity(self, lane: str) -> int:
        return self._lanes.get(lane, 1)

    def lane_full(self, lane: str) -> bool:
        return len(self._running.get(lane, ())) >= self.capacity(lane)

    def active(self, lane: str | None = None) -> list[RunHandle]:
        """Runs currently holding a slot, oldest first."""
        lanes = (lane,) if lane else tuple(self._running)
        return [
            self._handles[rid]
            for name in lanes
            for rid in self._running.get(name, ())
            if rid in self._handles
        ]

    def queued(self, lane: str | None = None) -> list[RunHandle]:
        lanes = (lane,) if lane else tuple(self._queues)
        return [
            self._handles[rid]
            for name in lanes
            for rid in self._queues.get(name, ())
            if rid in self._handles
        ]

    def duplicate_of(self, kind: str, params: dict[str, Any]) -> RunHandle | None:
        """An identical request already in flight, if there is one.

        Two lookups for one acronym cost twice, land the same row, and let the
        loser's `mark_undefined` overwrite the winner's result. C11.
        """
        for handle in self._handles.values():
            if handle.kind == kind and handle.params == params and not handle.cancelled:
                return handle
        return None

    def active_run_id(self, lane: str = run_types.RESEARCH) -> str | None:
        """The run a lane is executing, if any.

        Defaulted to `research`: that is what `/api/health` reports and what the
        app-level SSE stream follows, the only run worth watching stage by stage.
        """
        running = self.active(lane)
        return running[0].run_id if running else None

    # ── admission ────────────────────────────────────────────────────
    async def submit(
        self, run_id: str, kind: str, params: dict[str, Any] | None = None
    ) -> RunHandle:
        """Register a run and either admit it or queue it behind its lane.

        Never raises on a full lane. The caller creates the `runs` row as
        `queued` and awaits `handle.admitted`; refusing instead is what made
        the hover card's lookup button dead for the length of a research run.
        """
        lane = run_types.lane_for(kind)
        async with self._lock:
            if run_id in self._handles:
                raise RuntimeError(f"run {run_id} is already submitted")
            self._lanes.setdefault(lane, 1)
            self._running.setdefault(lane, [])
            self._queues.setdefault(lane, deque())

            handle = RunHandle(run_id=run_id, kind=kind, lane=lane, params=dict(params or {}))
            self._handles[run_id] = handle
            if len(self._running[lane]) < self._lanes[lane]:
                self._running[lane].append(run_id)
                handle.admitted.set()
            else:
                self._queues[lane].append(run_id)
            return handle

    async def release(self, run_id: str) -> None:
        """Free the slot and start whatever was waiting on it.

        A leaked slot means queued runs behind it never start at all, which is
        why admission and release live in `run_launcher.launch` and not in each
        endpoint -- `glossary.py` once leaked the registry because a
        `load_lexicon` raise sat outside the `try`.
        """
        async with self._lock:
            handle = self._handles.pop(run_id, None)
            if handle is None:
                return
            # The force is armed against THIS run: one that stopped on its
            # own inside the grace would leave a timer to cancel a finished task.
            if handle.forcer is not None:
                handle.forcer.cancel()
            lane = handle.lane
            with contextlib.suppress(ValueError):
                self._running.get(lane, []).remove(run_id)
            with contextlib.suppress(ValueError):
                self._queues.get(lane, deque()).remove(run_id)
            # Wake every listener so a client waiting on a stream that ended
            # abnormally is not left hanging.
            for queue in handle.subscribers:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(None)
            self._admit_next(lane)

    def _admit_next(self, lane: str) -> None:
        """Promote queued runs into free slots. Callers hold `self._lock`."""
        while len(self._running[lane]) < self._lanes[lane] and self._queues[lane]:
            next_id = self._queues[lane].popleft()
            waiting = self._handles.get(next_id)
            if waiting is None:
                continue
            self._running[lane].append(next_id)
            waiting.admitted.set()

    # ── streaming and cancellation, both keyed by run_id ──────────────
    def subscribe(self, run_id: str) -> asyncio.Queue | None:
        handle = self._handles.get(run_id)
        if handle is None:
            return None
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        handle.subscribers.append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        handle = self._handles.get(run_id)
        if handle is not None:
            with contextlib.suppress(ValueError):
                handle.subscribers.remove(queue)

    async def cancel(self, run_id: str, *, grace: float = CANCEL_GRACE_SECONDS) -> bool:
        """Ask a run to stop, and make sure it does.

        Both halves in ONE call, for the reason `run_launcher`'s docstring gives
        about the release path: a second call every caller has to remember is a
        call some caller will not make.

        A cancel on a run that is ALREADY cancelling forces immediately -- the
        escape hatch when the grace was not enough, and what makes the endpoint
        idempotent.
        """
        handle = self._handles.get(run_id)
        if handle is None:
            return False
        already = handle.cancelled
        handle.cancelled = True
        if not handle.admitted.is_set():
            # Queued and never started. Take it out of the queue and wake it so
            # its runner can finish `cancelled` immediately, rather than
            # occupying a queue position until a slot happens to free.
            with contextlib.suppress(ValueError):
                self._queues[handle.lane].remove(run_id)
            handle.admitted.set()
        self._arm_force(handle, 0.0 if already else grace)
        return True

    def _arm_force(self, handle: RunHandle, grace: float) -> None:
        """Start the timer that stops the run for it.

        Nothing is armed for a handle with no task: no work to interrupt, and a
        timer with nothing to cancel outlives the run as a task nobody awaits.
        """
        if handle.task is None or handle.task.done():
            return
        if handle.forcer is not None and not handle.forcer.done():
            if grace > 0:
                return
            handle.forcer.cancel()
        handle.forcer = asyncio.create_task(self._force_after(handle, grace))

    async def _force_after(self, handle: RunHandle, grace: float) -> None:
        """Wait out the grace, then cancel the task itself.

        Cancelling the task is what reaches a run that is inside a model call:
        `llm/executor.py::_reap` kills the subprocess on exactly this path, so
        the paid call stops rather than running to completion for an answer
        nobody will read. `run_launcher._runner` turns the `CancelledError` into
        a `cancelled` terminal row.
        """
        if grace > 0:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(grace)
        task = handle.task
        if task is not None and not task.done():
            log.info("forcing run %s to stop after %ss", handle.run_id, grace)
            task.cancel()

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        """Fan out to live listeners. Called only AFTER the event is durable."""
        handle = self._handles.get(run_id)
        if handle is None:
            return
        for queue in list(handle.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled reader must not block the pipeline. It will catch
                # up from the log on reconnect.
                pass


registry = RunRegistry()


# ── the event log ────────────────────────────────────────────────────


def append_event(
    conn: db.Conn,
    *,
    run_id: str,
    type_: str,
    stage: str | None,
    data: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    """Write one event and return it. Durable before it is ever published.

    `EVENT_TYPES` is checked here, which is what makes it a contract rather than
    a comment: it is C6's frozen list, the wire types it as an enum, and a client
    that meets a type outside it has no branch to run. Every emitter is in this
    repo, so an unknown type is a programming error and belongs in a test's
    traceback rather than in a run's event log.
    """
    if type_ not in EVENT_TYPES:
        raise ValueError(f"unknown run event type {type_!r}; the set is frozen (contract C6)")

    # C6: `seq` is per-run, dense, and starts at 1, so it cannot be a sequence --
    # a Postgres sequence is global and leaves gaps. It has to be `MAX(seq)+1`,
    # which is a read-then-write, and this is the highest-frequency write in the
    # system: every `emit` from every stage of up to one research plus three light
    # runs, all on separate connections. At READ COMMITTED two of them read the
    # same `MAX(seq)` and the second INSERT violates the (run_id, seq) primary
    # key; how the transaction BEGINS cannot fix that, so it is fixed here.
    #
    # An advisory lock keyed on the RUN, so appends to one run serialise and
    # concurrent runs never wait on each other. `pg_advisory_xact_lock` releases
    # at COMMIT or ROLLBACK -- no unlock to leak, which is why it is the `_xact_`
    # variant and not the session one.
    #
    # Deliberately NOT a retry loop on 23505. A retry would work, but it treats a
    # collision as normal and would equally mask a genuine duplicate-seq bug --
    # and this is the log the SSE stream is a view over, where `Last-Event-ID`
    # resumes by seq: a gap truncates a reconnecting client's history and a
    # duplicate makes it replay. Pinned by `tests/test_db_concurrency.py`.
    with db.transaction(conn, what="append_event"):
        repo.select_rows(conn, run_id)
        row = repo.select_run_events_by_run_id_2(conn, run_id).fetchone()
        seq = row["next"]
        repo.insert_run_events(conn, run_id, seq, now, type_, stage, db.jdump(data))

    return {"seq": seq, "run_id": run_id, "ts": now, "type": type_, "stage": stage, "data": data}


def events_since(
    conn: db.Conn, run_id: str, *, after_seq: int = 0, limit: int = 2000
) -> list[dict[str, Any]]:
    return [
        {
            "seq": r["seq"],
            "run_id": run_id,
            "ts": r["ts"],
            "type": r["type"],
            "stage": r["stage"],
            "data": db.jload(r["data"], {}),
        }
        for r in repo.select_run_events_by_run_id(conn, run_id, after_seq, limit)
    ]


def iter_events_since(
    conn: db.Conn, run_id: str, *, after_seq: int = 0, page: int = 2000
) -> Iterator[dict[str, Any]]:
    """Every durable event after `after_seq`, read a page at a time.

    A replay is what makes a reconnect lossless, so it has to reach the end of
    the log. A single capped read cannot: a run past one page -- `model_message`
    alone puts a long one there -- hands a reconnecting client the oldest page
    and drops the half it is reconnecting FOR. Paged rather than uncapped so
    that one read still cannot land the whole log on the heap.
    """
    last = after_seq
    while True:
        rows = events_since(conn, run_id, after_seq=last, limit=page)
        for event in rows:
            last = event["seq"]
            yield event
        if len(rows) < page:
            return


# ── run rows ─────────────────────────────────────────────────────────


def _domain_short_hash() -> str:
    from venona.domain import domain_hash

    return domain_hash()[:12]


def create_run(
    conn: db.Conn,
    *,
    run_id: str,
    kind: str,
    params: dict[str, Any],
    profile_hash: str | None,
    now: str,
    status: str = "running",
    started_by: str | None = None,
) -> None:
    """Write the `runs` row.

    `status` is `queued` when the run's lane is full; the column has advertised
    that value in its schema comment since 0001. `_assert_not_live` and
    `reap_interrupted` both match on `('running','queued')`, so a queued row is
    protected from hiding and reaped on a crash without further change.
    """
    db.insert(
        conn,
        "runs",
        {
            "id": run_id,
            "kind": kind,
            "status": status,
            "stage": None,
            "params": db.jdump(params),
            "counters": db.jdump({}),
            "warnings": db.jdump([]),
            "profile_hash": profile_hash,
            # Stamped centrally rather than threaded through every launch
            # site: the descriptor is process-global and this is the one writer
            # of the row. Truncated to 12 like `Profile.short_hash`.
            "domain_hash": _domain_short_hash(),
            "started_at": now,
            # WHO ASKED. The parameter was declared, threaded through two
            # call sites and dropped before the insert, so the column was NULL
            # on every row ever written -- a dead write and a dead read hiding
            # each other perfectly, which is why nothing caught it.
            "started_by": started_by,
            # DIAGNOSTIC ONLY; the lease below is the liveness mechanism.
            # The pid stays because "which process is running this" is the first
            # thing an operator asks and a lock id cannot answer it.
            "owner_pid": os.getpid(),
        },
    )
    # THE LEASE, taken on the connection the run itself will use -- which is
    # the entire mechanism, because Postgres releases a session advisory lock
    # when that connection ends, however it ends. C11 has the rest of it.
    #
    # `create_run` is the right seam because every path that starts a run goes
    # through it with that long-lived connection in hand: `run_launcher`'s
    # `run_conn` and the CLI's `_db()` block both outlive the work.
    #
    # A failure to claim means another session already owns this run id, which
    # for a freshly minted id should be impossible -- so it raises.
    if not repo.claim_lease(conn, run_id):
        raise RunIsLive(run_id)


def mark_running(conn: db.Conn, *, run_id: str) -> None:
    """A queued run has been admitted to its lane and is starting work.

    `started_at` is deliberately NOT rewritten: it records when the run was
    asked for, and the wait for a slot is the gap between that and the first
    `stage_started` event. Overwriting it would hide exactly that.
    """
    repo.update_runs_by_id(conn, run_id)


def finish_run(
    conn: db.Conn,
    *,
    run_id: str,
    status: str,
    counters: dict[str, Any],
    warnings: list[RunMessage],
    brief_id: str | None,
    cost_usd: float,
    error: str | None,
    now: str,
    outcome: str | None = None,
) -> None:
    """Write the terminal row.

    `outcome` is None for a run that has none: one that failed or was cancelled
    never reached an answer, and stamping one would be `status`'s own overload
    running the other way. C6.
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"{status!r} is not a terminal status")
    if outcome is not None and outcome not in OUTCOMES:
        raise ValueError(f"{outcome!r} is not a run outcome")
    repo.update_runs_by_status_counters(
        conn,
        status,
        outcome,
        db.jdump(counters),
        # `as_json`, not the objects: `db.jdump` is `json.dumps` and would
        # raise on a dataclass.
        db.jdump(types.as_json(warnings)),
        brief_id,
        cost_usd,
        error,
        now,
        run_id,
    )
    _settle_open_stages(conn, run_id=run_id, now=now)


def _settle_open_stages(conn: db.Conn, *, run_id: str, now: str) -> None:
    """Close any stage still marked `running` when the run itself ended.

    A stage is written at START as well as at finish, so that one in progress --
    or one that died -- has a row rather than nothing. The cost is this: a run
    cancelled or killed inside `fetch` leaves a `running` stage row that nothing
    updates, and its page shows a stage running under a `cancelled` header
    indefinitely.

    `interrupted` rather than `error`: the stage did not fail, it was stopped.
    `finished_at` is the run's end, the only honest timestamp available.
    """
    repo.update_run_stages_by_finished_at_run_id(conn, now, run_id)


def set_stage(conn: db.Conn, run_id: str, stage: str | None) -> None:
    repo.update_runs_by_stage_id(conn, stage, run_id)


def record_stage(
    conn: db.Conn,
    *,
    run_id: str,
    stage: str,
    status: str,
    counts: dict[str, Any] | None = None,
    error: str | None = None,
    started_at: str,
    finished_at: str | None = None,
) -> None:
    db.upsert(
        conn,
        "run_stages",
        {
            "run_id": run_id,
            "stage": stage,
            "status": status,
            "counts": db.jdump(counts or {}),
            "error": error,
            "started_at": started_at,
            "finished_at": finished_at,
        },
        key=["run_id", "stage"],
    )


def get_run(conn: db.Conn, run_id: str) -> dict[str, Any] | None:
    row = repo.select_runs_by_id(conn, run_id).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["params"] = db.jload(run["params"], {})
    run["counters"] = db.jload(run["counters"], {})
    run["warnings"] = hydrate_messages(run["warnings"], status=run["status"])
    run["stages"] = [
        {**dict(s), "counts": db.jload(s["counts"], {})}
        for s in repo.select_run_stages_by_run_id(conn, run_id)
    ]
    return run


def hydrate_messages(raw: Any, *, status: str | None) -> list[dict[str, Any]]:
    """A run's messages, whatever release wrote them.

    A row whose messages are a bare `list[str]` carries no level, so the level
    is DERIVED rather than guessed. `ctx.warn` is the only route to
    `succeeded_partial`, so on a `succeeded` run every message provably was a
    note; on a degraded one we cannot tell, so they stay warnings. ADR 0183's
    rule holds: a status is never rewritten after the fact.
    """
    out: list[dict[str, Any]] = []
    legacy = "note" if status == "succeeded" else "warning"
    for item in db.jload(raw, []):
        if isinstance(item, dict):
            out.append(item)
        else:
            out.append({"level": legacy, "text": str(item)})
    return out


def list_runs(
    conn: db.Conn,
    *,
    limit: int = 25,
    offset: int = 0,
    include_hidden: bool = False,
):
    """Runs, newest first.

    `total` counts what this filter can reach, so "load more" terminates; the
    separate `hidden` count is what lets the UI say "12 hidden" rather than
    silently showing a shorter list than the database holds.
    """
    rows = repo.listing(conn, include_hidden=include_hidden, limit=limit, offset=offset)
    items = []
    for row in rows:
        item = dict(row)
        item["params"] = db.jload(item["params"], {})
        item["counters"] = db.jload(item["counters"], {})
        item["warnings"] = hydrate_messages(item["warnings"], status=item["status"])
        items.append(item)
    total = repo.listing_total(conn, include_hidden=include_hidden)
    hidden = repo.select_runs_4(conn).fetchone()["n"]
    return items, total, hidden


class RunIsLive(Exception):
    """Raised when an operation would touch a run that is still executing."""


def _assert_not_live(conn: db.Conn, run_id: str) -> db.Row:
    """Refuse to touch a run whose owner process is still working.

    The check is `runs.status` plus the run's LEASE, and NOT
    `registry.active_run_id`. The registry is an in-process `asyncio.Lock`, so
    it is blind to `venona run` -- a separate process. Trusting it here would
    reproduce one layer up the bug `0004_run_leases.sql` records: the API
    confidently deciding the state of a run it cannot see.
    """
    row = repo.select_runs_by_id_2(conn, run_id).fetchone()
    if row is None:
        raise LookupError(run_id)
    if row["status"] in ("running", "queued") and repo.lease_is_held(conn, run_id):
        raise RunIsLive(run_id)
    return row


def hide_run(conn: db.Conn, run_id: str, *, now: str) -> dict[str, Any]:
    """Drop a run out of the default listing. Reversible; deletes nothing."""
    _assert_not_live(conn, run_id)
    with db.transaction(conn):
        repo.update_runs_by_hidden_at_id(conn, now, run_id)
    return {"run_id": run_id, "hidden_at": now}


def unhide_run(conn: db.Conn, run_id: str) -> dict[str, Any]:
    """Put a hidden run back in the default listing.

    Raises `LookupError` for a run that does not exist, which is what `hide_run`
    does through `_assert_not_live`. A bare UPDATE matches zero rows and still
    returns a well-formed payload, so a typo'd or already-reaped id reads to the
    client as a successful unhide, from an endpoint sharing a response model
    with one that does check its target.

    Deliberately NO `_assert_not_live` here. That guard stops you HIDING a run
    someone is watching; clearing `hidden_at` on a live run takes nothing away,
    and refusing it would leave a hidden run unrecoverable until it finished.
    """
    with db.transaction(conn):
        cursor = repo.update_runs_by_id_2(conn, run_id)
        if cursor.rowcount == 0:
            raise LookupError(run_id)
    return {"run_id": run_id, "hidden_at": None}


def hide_all(conn: db.Conn, *, now: str) -> dict[str, int]:
    """Hide every currently-visible run whose owner is not still working.

    A live run is skipped rather than refused: "clear the history" is a request
    about the past, and failing the whole operation because one run happens to
    be in flight would be obtuse.
    """
    hidden = 0
    skipped = 0
    rows = repo.select_runs_2(conn).fetchall()
    for row in rows:
        try:
            _assert_not_live(conn, row["id"])
        except RunIsLive:
            skipped += 1
            continue
        with db.transaction(conn):
            repo.update_runs_by_hidden_at_id_2(conn, now, row["id"])
        hidden += 1
    return {"hidden": hidden, "skipped": skipped}


async def drain_lane(reg: RunRegistry, *, lane: str, seconds: float) -> list[str]:
    """Let the runs in one lane finish before the process goes, up to `seconds`.

    MEASURED 2026-09-14: an `on_demand` run died 87s into shutdown with 213s of
    the platform's 300 unspent, because uvicorn waits for CONNECTIONS and the
    lifespan tore the loop down without asking what was executing.

    LIGHT LANE ONLY -- every light median fits the grace and a research run is
    tens of minutes, so it keeps its push-time guard instead. The medians are in
    `docs/pm/epics/aa-scale-out.md`.
    """
    handles = [h for h in reg.active(lane) if h.task is not None and not h.task.done()]
    if not handles:
        return []
    log.info("shutdown: waiting up to %ss for %d %s run(s)", seconds, len(handles), lane)
    _, pending = await asyncio.wait([h.task for h in handles], timeout=seconds)
    survived = [h.run_id for h in handles if h.task is not None and h.task.done()]
    if survived:
        log.info("shutdown: %d run(s) finished in time: %s", len(survived), ", ".join(survived))
    return [h.run_id for h in handles if h.task in pending]


def lane_conflict(conn: db.Conn, *, lane: str) -> dict[str, Any] | None:
    """A run in `lane` that some process is still executing, or None.

    The CROSS-PROCESS guard, and the only correct one (C11): `RunRegistry` is an
    in-process `asyncio.Lock` and `venona run` is a different pid entirely. An
    API that skips this starts a pipeline on top of a CLI run -- two writers,
    two budgets, one `documents` table.

    Two rules, both learned the hard way:

    * **Filter by LANE.** An unqualified `WHERE status = 'running'` lets a
      background acronym lookup -- a run that writes one row -- refuse `venona
      run` from a terminal.
    * **A run without a lease is not live.** Counting unknown ownership as live
      is the safe reading while ownership is ambiguous, because wrongly deciding
      a live run is dead is the expensive direction -- but it wedges a lane on
      every crash. The lease removes the ambiguity rather than resolving it: a
      run holds its lock from `create_run` until its connection ends, so "no
      lock" is positive evidence, not missing evidence.
    """
    candidates = [row for row in repo.select_runs(conn) if run_types.lane_for(row["kind"]) == lane]
    leased = repo.leased_run_ids(conn, [row["id"] for row in candidates])
    for row in candidates:
        if row["id"] in leased:
            return {"id": row["id"], "kind": row["kind"], "owner_pid": row["owner_pid"]}
    return None


class LaneBusy(Exception):
    """A run was asked for while its lane was occupied.

    Routers turn this into a 409. It lives here rather than in `run_launcher`
    because `create_run_guarded` is where the refusal becomes atomic, and
    `run_service` cannot import `run_launcher` without closing a cycle.
    """

    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id} is already active")
        self.run_id = run_id


def create_run_guarded(
    conn: db.Conn,
    *,
    lane: str,
    run_id: str,
    kind: str,
    params: dict[str, Any],
    profile_hash: str | None,
    now: str,
    status: str = "running",
    started_by: str | None = None,
) -> None:
    """Claim the lane and write the run row as one step, across processes.

    THE CHECK AND THE CLAIM HAVE TO BE THE SAME STEP. `lane_conflict` is a read
    and `create_run` is a write, and between them is a window in which another
    process -- a second `venona research`, a scheduled run, the API -- passes the
    same read. Both then write, and two full pipelines execute against one
    `documents` table with two independent budgets: the second to finish decides
    what the corpus looks like, and nothing anywhere reports that it happened.
    `RunRegistry` cannot close this, and the per-run lease cannot either -- it is
    keyed on the run id, so two DIFFERENT runs never contend for it.

    So the gate is a lock in the database, keyed on the lane, taken as the first
    statement of the transaction that writes the row, with `lane_conflict`
    re-read INSIDE it: the answer the caller got before is exactly the one that
    could be stale.

    REFUSAL IS THE RESEARCH LANE'S RULE ALONE (C11). `light` queues by design,
    so for it this is the lock and the row with no verdict -- still worth the
    round trip, because it keeps the status the row is born with from being
    decided by a race.
    """
    with db.transaction(conn):
        repo.lock_lane(conn, lane)
        if lane == run_types.RESEARCH:
            active = lane_conflict(conn, lane=lane)
            if active is not None:
                raise LaneBusy(active["id"])
        create_run(
            conn,
            run_id=run_id,
            kind=kind,
            params=params,
            profile_hash=profile_hash,
            now=now,
            status=status,
            started_by=started_by,
        )


def reap_interrupted(conn: db.Conn, *, now: str) -> list[str]:
    """Mark runs abandoned by a dead process as failed.

    Without this a killed process leaves a run that looks live forever and the
    UI shows a spinner that never resolves. But "the API server started" is NOT
    evidence that a run died: `venona run` is a separate process, and the
    unqualified version of this reaped a live CLI run 25 minutes into `fetch`
    because an edit triggered uvicorn's reloader. The run kept going and
    overwrote the status when it finished, so the only trace was a `failed` row
    the operator saw and believed.

    A run is reaped only when its owner is provably gone, which is one question:
    does anything still hold its LEASE. `0004_run_leases.sql` is why that is the
    only form of the question a second host can answer.

    A reaped run is `succeeded_partial` when any stage completed and `failed`
    only when none did. On a container that distinction is routine: every deploy
    SIGKILLs whatever is in flight, and a pipeline killed at minute 35 has
    already written documents, clusters and a brief. Calling that `failed`
    throws away the operator's only signal that the work is there.
    """
    reaped: list[str] = []
    rows = repo.select_runs_3(conn).fetchall()
    leased = repo.leased_run_ids(conn, [row["id"] for row in rows])
    for row in rows:
        if row["id"] in leased:
            continue
        done = repo.select_run_stages_by_run_id_2(conn, row["id"]).fetchone()[0]
        status = "succeeded_partial" if done else "failed"
        repo.update_runs_by_status_error(
            conn, status, f"interrupted (process died after {done} stage(s))", now, row["id"]
        )
        # The stage the dead process was inside. The count above filters on
        # `status = 'ok'`, so settling it cannot change the reaped status.
        _settle_open_stages(conn, run_id=row["id"], now=now)
        reaped.append(row["id"])
    return reaped


# ── SSE framing ──────────────────────────────────────────────────────


def format_sse(event: dict[str, Any]) -> str:
    """`id:` carries the seq so the browser's native `Last-Event-ID` reconnect
    resumes exactly where it stopped."""
    payload = json.dumps(event, separators=(",", ":"))
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {payload}\n\n"


# How often an open stream re-asks whether the person reading it is still
# allowed to. `AuthMiddleware` runs ONCE, at request start; this endpoint then
# holds the connection for up to forty minutes yielding `model_message` events
# -- the live Claude session, prompts included, which is why the route is
# admin-only. Without a re-check, "this account is compromised, suspend it"
# leaves the compromised session reading to the end of the run.
#
# Thirty seconds rather than "every N heartbeats": heartbeats fire only when the
# stream is IDLE, so a heartbeat-counted check never fires on exactly the stream
# worth cutting. Two indexed single-row reads a minute per open stream is not a
# cost worth optimising against that.
REVOCATION_RECHECK_SECONDS = 30.0


class _RevocationGate:
    """Re-resolves the viewer's `users` row on a timer, for a long-held stream.

    `viewer_id` is `Principal.user_id`, falsy for exactly the principals with no
    row to re-read: the root bearer, `via="disabled"`, and the env break-glass
    admin (`id=""`). Those are unrevokable by design -- the root token is revoked
    by rotating it -- so the gate stays open rather than pretending to check.

    A read that RAISES closes the stream, the same fail-closed reading
    `AuthMiddleware._lookup_user` takes on the same question: an unreachable
    database is not evidence that the reader is still authorized. The run itself
    is untouched, and a legitimate reconnect gets a new stream from a boundary
    that can answer properly.
    """

    def __init__(self, conn: db.Conn, viewer_id: str | None) -> None:
        self._conn = conn
        self._viewer_id = viewer_id or None
        self._due = time.monotonic() + REVOCATION_RECHECK_SECONDS

    def revoked(self) -> bool:
        if self._viewer_id is None:
            return False
        now = time.monotonic()
        if now < self._due:
            return False
        self._due = now + REVOCATION_RECHECK_SECONDS
        from venona.services import user_service

        try:
            viewer = user_service.get(self._conn, self._viewer_id)
        except Exception:  # noqa: BLE001 - see the docstring; fail closed
            return True
        # Both halves, because suspension is not the only revocation: an admin
        # demoted to reader loses this route, and `model_message` frames are the
        # reason that matters.
        return viewer is None or not viewer.active or not viewer.is_admin


# How often to re-read the log for a run owned by another process. Fast enough
# that a transcript reads as live, slow enough to be invisible: a run emits a few
# events per second at most, and MVCC keeps this indexed read off the writer's
# back.
TAIL_POLL_SECONDS = 1.0


async def _tail_log(
    conn: db.Conn, run_id: str, *, after_seq: int, viewer_id: str | None = None
) -> AsyncIterator[str]:
    """Follow a run this process does not own, by reading its durable log.

    Used for `venona run`, the scheduler in a restarted worker, and any run whose
    owner is a different process. Terminates on a `done` event, a terminal
    status, or the owning process dying -- otherwise a client watching a crashed
    run hangs on an open stream forever.
    """
    last_seq = after_seq
    gate = _RevocationGate(conn, viewer_id)
    while True:
        if gate.revoked():
            return
        saw_done = False
        saw_any = False
        for event in iter_events_since(conn, run_id, after_seq=last_seq):
            last_seq = event["seq"]
            saw_any = True
            yield format_sse(event)
            if event["type"] == "done":
                saw_done = True
                break
        if saw_done:
            return

        run = get_run(conn, run_id)
        status = (run or {}).get("status")
        if status in TERMINAL_STATUSES:
            # The owner finished without writing `done` (a kill, or a crash
            # after the status update). Synthesise one so EventSource closes
            # instead of reconnecting forever.
            yield format_sse(
                {
                    "seq": last_seq + 1,
                    "run_id": run_id,
                    "ts": "",
                    "type": "done",
                    "stage": None,
                    "data": {"status": status},
                }
            )
            return
        if run is None or not repo.lease_is_held(conn, run_id):
            yield format_sse(
                {
                    "seq": last_seq + 1,
                    "run_id": run_id,
                    "ts": "",
                    "type": "done",
                    "stage": None,
                    "data": {"status": "failed", "error": "owner process is gone"},
                }
            )
            return

        if not saw_any:
            yield ": ping\n\n"
        await asyncio.sleep(TAIL_POLL_SECONDS)


async def stream_events(
    conn: db.Conn, run_id: str, *, after_seq: int = 0, viewer_id: str | None = None
) -> AsyncIterator[str]:
    """Attach to the live queue, then replay from the log.

    Replaying is what makes a reconnect lossless: everything that happened while
    the client was away is already durable and is simply re-sent.

    SUBSCRIBE FIRST. Every `yield` in the replay loop hands control back to the
    event loop, so anything the pipeline publishes between the first replayed
    event and the subscription lands in neither -- read out of the log already,
    not yet in a queue. The UI POSTs `/api/runs` and opens `/stream` immediately,
    so the window is exactly where the early stage events are: the stage strip
    showed `fetch` running for the rest of the run because its `stage_finished`
    fell in the gap. Overlapping the two sources leaves no seam, and the
    `seq <= last_seq` check below drops the duplicates overlap produces.
    """
    queue = registry.subscribe(run_id)
    try:
        last_seq = after_seq
        for event in iter_events_since(conn, run_id, after_seq=after_seq):
            last_seq = event["seq"]
            yield format_sse(event)

        if queue is None:
            # No in-process queue means one of two things, and treating "not
            # mine" as "already finished" fabricates a `done` carrying the run's
            # CURRENT status -- often the non-terminal "running" -- after which
            # the browser's EventSource closes for good. `registry` is an
            # in-process lock and `venona run` is a SEPARATE PROCESS, so every
            # CLI-launched run streamed its replay to the UI and then went silent
            # forever: "the panel does not auto-update". The log is the system of
            # record here, so if the run is still going, tail it.
            async for chunk in _tail_log(conn, run_id, after_seq=last_seq, viewer_id=viewer_id):
                yield chunk
            return

        gate = _RevocationGate(conn, viewer_id)
        while True:
            if gate.revoked():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                # Comment frame: keeps proxies from closing an idle stream.
                yield ": ping\n\n"
                continue

            if event is None:
                return
            if event["seq"] <= last_seq:
                continue  # already delivered by the replay
            last_seq = event["seq"]
            yield format_sse(event)
            if event["type"] == "done":
                return
    finally:
        # Covers the replay too, since the subscription precedes it: a client
        # that disconnects mid-replay must not leave a queue on the handle.
        if queue is not None:
            registry.unsubscribe(run_id, queue)
