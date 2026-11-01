"""Contract C11 -- run types, lanes, and the queue.

The registry mechanics live in `test_runs.py` alongside the rest of the
registry. What is here is the part that spans layers: the run-type table itself,
the cross-process guard's lane filter, and the end-to-end behaviour this whole
contract exists for -- a lookup starting while a research run is in flight.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any

import pytest
from helpers import NOW
from venona.api.schemas import run as run_schema
from venona.db import core as db
from venona.db.repositories import run as run_repo
from venona.pipeline import orchestrator
from venona.services import run_launcher, run_service, run_types

# ── the table ────────────────────────────────────────────────────────


def test_the_research_stage_list_matches_the_orchestrator() -> None:
    """`run_types.RESEARCH_STAGES` is a deliberate copy.

    Importing `orchestrator.STAGES` would close a cycle: `orchestrator` imports
    `run_service`, and `run_service` imports `run_types` for `lane_for`. So the
    duplicate is pinned here instead, exactly as `test_the_ui_knows_every_stage`
    pins the frontend's copy.

    An enforced duplicate is fine. An unenforced one is how `pipeline/types.py`
    ended up with a nine-name `STAGES` contradicting the real four.
    """
    assert run_types.RESEARCH_STAGES == orchestrator.STAGES


def test_a_lookup_declares_a_stage_the_pipeline_has_never_heard_of() -> None:
    """The two stage lists answer different questions, and must not be merged.

    `--only` validates against `orchestrator.STAGES`. If `lookup` were in it,
    `venona run --only lookup` would be a valid-looking no-op -- the exact
    failure the "two stage lists is one too many" decision was written about.
    """
    assert run_types.type_for("lookup").stages == ("lookup",)
    assert "lookup" not in orchestrator.STAGES


def test_every_run_type_lands_in_a_declared_lane() -> None:
    for run_type in run_types.RUN_TYPES.values():
        assert run_type.lane in run_types.LANES, run_type.kind


def test_an_unknown_kind_renders_rather_than_raising() -> None:
    """`services/seed.py` writes assertions under `run:seed-0001` with no `runs`
    row at all, and two kinds have been added without a migration. A history
    table that raises on an old row is worse than one that renders it plainly.
    """
    unknown = run_types.type_for("kind-from-2029")
    assert unknown.label == "kind-from-2029"
    assert unknown.lane == run_types.RESEARCH, "unknown must not be handed a light slot"
    assert run_types.lane_for("") == run_types.RESEARCH


def test_a_personal_brief_is_light_and_single() -> None:
    """It writes `personal_briefs` rows and nothing else -- no documents, no
    clusters, no shared brief -- so it must not contend for the research slot
    (the `questions` argument). And its stage is its own, so `venona run
    --only personal_brief` stays invalid."""
    assert run_types.lane_for("personal_brief") == run_types.LIGHT
    assert run_types.type_for("personal_brief").stages == ("personal_brief",)
    assert "personal_brief" not in orchestrator.STAGES


def test_the_lookup_endpoints_share_one_kind() -> None:
    """Both lookup endpoints have written `kind="lookup"` since they shipped.
    Splitting the kind would mislabel every historical row; the target lives in
    `params` instead."""
    assert run_types.lane_for("lookup") == run_types.LIGHT
    assert run_types.type_for("lookup").detail == "single"


# ── the payload the UI renders from ──────────────────────────────────


def test_a_run_row_carries_its_lane_and_label() -> None:
    """Derived, never stored -- so a row written by an older build still
    describes itself."""
    decorated = run_schema.decorate({"id": "run:x", "kind": "lookup"})
    assert decorated["lane"] == run_types.LIGHT
    assert decorated["label"] == "Definition"
    assert decorated["detail"] == "single"
    assert decorated["trigger"] == "on_demand"


def test_proposing_placements_runs_in_the_light_lane() -> None:
    """It reads the graph and files suggestions; it writes no document, no
    relationship and no configuration. Putting it in the research lane would make
    it refuse for the tens of minutes a research run takes, which is exactly when
    someone is looking at the graph and noticing what is unattached."""
    assert run_types.lane_for("anchors") == run_types.LIGHT

    kind = run_types.type_for("anchors")
    assert kind.detail == "single"
    assert kind.trigger == "on_demand"
    # Every verdict `propose_placements` can return has words of its own. A kind
    # that reaches an outcome it declares no phrase for renders a bare status.
    for outcome in ("delivered", "some", "nothing_found", "nothing_to_do"):
        assert outcome in kind.outcomes
        assert outcome in kind.lines


def test_the_run_type_payload_covers_every_kind() -> None:
    payload = run_types.as_payload()
    assert {t["kind"] for t in payload["types"]} == set(run_types.RUN_TYPES)
    assert payload["lanes"]["research"] == 1
    assert payload["lanes"]["light"] > 1, "the light lane exists to run several at once"


# ── the cross-process guard ──────────────────────────────────────────


def _insert_run(conn: db.Conn, run_id: str, kind: str, status: str) -> None:
    """A run row that is LEASED, which is what makes it count as in flight.

    The row alone stopped being enough when liveness moved from `owner_pid` to a
    session advisory lock. Taking the lease on `conn` is what a real owner does
    -- `create_run` claims it on the connection the run will use -- so a test
    that wants a live run has to hold one too.
    """
    conn.execute(
        "INSERT INTO runs (id, kind, status, started_at) VALUES (?,?,?,?)",
        (run_id, kind, status, NOW),
    )
    run_repo.claim_lease(conn, run_id)
    conn.commit()


def _insert_unleased_run(conn: db.Conn, run_id: str, kind: str, status: str) -> None:
    """A `running` row nobody holds: a crashed owner, or a restarted container."""
    conn.execute(
        "INSERT INTO runs (id, kind, status, started_at) VALUES (?,?,?,?)",
        (run_id, kind, status, NOW),
    )
    conn.commit()


def test_a_research_run_blocks_another_one(conn: db.Conn) -> None:
    _insert_run(conn, "run:pipeline", "on_demand", "running")
    conflict = run_service.lane_conflict(conn, lane=run_types.RESEARCH)
    assert conflict and conflict["id"] == "run:pipeline"


@pytest.mark.negative
def test_a_lookup_does_not_wedge_the_cli(conn: db.Conn) -> None:
    """The regression the lane split would otherwise have introduced silently.

    `venona run`'s pre-flight asked `WHERE status = 'running'` with no further
    test, which was right while only one run could exist. Left alone, a
    background acronym lookup -- one definition row -- would refuse a full
    research run from a terminal.
    """
    _insert_run(conn, "run:lookup", "lookup", "running")
    assert run_service.lane_conflict(conn, lane=run_types.RESEARCH) is None
    assert run_service.lane_conflict(conn, lane=run_types.LIGHT) is not None


@pytest.mark.negative
def test_a_finished_run_does_not_wedge_anything(conn: db.Conn) -> None:
    """Only in-flight rows block. A crash must not be unrecoverable without SQL."""
    _insert_run(conn, "run:done", "on_demand", "succeeded")
    assert run_service.lane_conflict(conn, lane=run_types.RESEARCH) is None


def test_a_queued_run_still_blocks_its_lane(conn: db.Conn) -> None:
    """`queued` is in flight. It holds a place in a lane and will start."""
    _insert_run(conn, "run:waiting", "on_demand", "queued")
    assert run_service.lane_conflict(conn, lane=run_types.RESEARCH) is not None


def test_an_unleased_running_row_does_not_block(conn: db.Conn) -> None:
    """THE RULE THAT INVERTED, and it inverted on purpose.

    It used to read the other way: unknown ownership counted as LIVE, because a
    NULL `owner_pid` could mean "a row predating migration 0008" as easily as
    "nothing is running", and deciding a live run is dead is the expensive
    direction.

    The lease removes the ambiguity rather than resolving it. A run holds its
    lock from `create_run` until its connection ends, so "no lock" is positive
    evidence that nobody is executing this row -- and keeping the old asymmetry
    would now mean a lane wedged by every single crash, permanently, which is the
    failure `run_launcher` warns about in the loudest terms it has.
    """
    _insert_unleased_run(conn, "run:crashed", "on_demand", "running")
    assert run_service.lane_conflict(conn, lane=run_types.RESEARCH) is None


def test_a_lease_held_by_another_session_blocks(conn: db.Conn, database_url: str) -> None:
    """The cross-PROCESS case, which is the only one that ever mattered.

    `RunRegistry` is an in-process `asyncio.Lock` and cannot see `venona run` in
    another terminal. Here the owner is a genuinely separate connection, and the
    lane is held until it goes away -- which is the property that also makes this
    work across two machines, where `/proc` could not.
    """
    owner = db.connect(database_url)
    try:
        _insert_run(owner, "run:elsewhere", "on_demand", "running")
        conflict = run_service.lane_conflict(conn, lane=run_types.RESEARCH)
        assert conflict and conflict["id"] == "run:elsewhere"
    finally:
        owner.close()
    assert run_service.lane_conflict(conn, lane=run_types.RESEARCH) is None


# ── claiming the lane, across processes ──────────────────────────────


def _guarded(conn: db.Conn, run_id: str, *, kind: str = "cli", lane: str = run_types.RESEARCH):
    return run_service.create_run_guarded(
        conn,
        lane=lane,
        run_id=run_id,
        kind=kind,
        params={},
        profile_hash=None,
        now=NOW,
    )


@pytest.mark.negative
def test_two_processes_cannot_both_claim_the_research_lane(
    conn: db.Conn, database_url: str
) -> None:
    """⭐ THE ONE THE LANE LOCK EXISTS FOR, and the one no single-threaded test
    sees.

    Asking `lane_conflict` and writing the row are two statements on two reads.
    Between them another process passes the same check, so both write, and two
    full pipelines run against one `documents` table with two independent
    budgets -- the second to finish decides what the corpus looks like, silently.

    Both connections are real and both attempt at once. The first barrier makes
    them overlap rather than hoping they do; the second holds both connections
    open until both have attempted, because closing one releases its lease and
    would let the loser through for the wrong reason.
    """
    ready, attempted = threading.Barrier(2), threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def launch(name: str) -> None:
        own = db.connect(database_url)
        try:
            ready.wait(timeout=10)
            try:
                _guarded(own, f"run:{name}")
                outcomes[name] = "started"
            except run_service.LaneBusy:
                outcomes[name] = "refused"
            attempted.wait(timeout=10)
        finally:
            own.close()

    threads = [threading.Thread(target=launch, args=(name,)) for name in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert sorted(outcomes.values()) == ["refused", "started"], (
        f"both launches claimed the research lane: {outcomes}"
    )
    live = conn.execute("SELECT COUNT(*) AS n FROM runs WHERE status = 'running'").fetchone()
    assert live["n"] == 1


@pytest.mark.negative
def test_a_guarded_create_refuses_while_another_session_holds_the_lane(
    conn: db.Conn, database_url: str
) -> None:
    """`venona research` ran the whole pipeline without asking this question at
    all, so it would start a second research run on top of a live one."""
    owner = db.connect(database_url)
    try:
        _insert_run(owner, "run:elsewhere", "on_demand", "running")
        with pytest.raises(run_service.LaneBusy) as refusal:
            _guarded(conn, "run:mine", kind="research")
        assert refusal.value.run_id == "run:elsewhere"
        # The refusal leaves no half-claimed row behind.
        row = conn.execute("SELECT COUNT(*) AS n FROM runs WHERE id = 'run:mine'").fetchone()
        assert row["n"] == 0
    finally:
        owner.close()


def test_the_light_lane_is_serialised_but_never_refused(conn: db.Conn, database_url: str) -> None:
    """C11's other half: `light` queues by design, so the lock orders its
    admissions without ever turning one into an error."""
    owner = db.connect(database_url)
    try:
        _insert_run(owner, "run:lookup-one", "lookup", "running")
        _guarded(conn, "run:lookup-two", kind="lookup", lane=run_types.LIGHT)
        row = conn.execute("SELECT COUNT(*) AS n FROM runs WHERE id = 'run:lookup-two'").fetchone()
        assert row["n"] == 1
    finally:
        owner.close()


def test_lane_busy_is_one_class_wherever_it_is_caught() -> None:
    """Routers catch `run_launcher.LaneBusy`; the refusal is raised in
    `run_service`. Two classes would make every `except` in the API a silent
    pass-through."""
    assert run_launcher.LaneBusy is run_service.LaneBusy


# ── end to end, through `launch` ─────────────────────────────────────


class _FakeState:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.llm = object()
        self.llm_light = object()
        self.graph = None


class _FakeApp:
    def __init__(self, settings: Any) -> None:
        self.state = _FakeState(settings)


class _FakeRequest:
    def __init__(self, settings: Any) -> None:
        self.app = _FakeApp(settings)


@pytest.fixture
def launchable(conn: db.Conn, database_url: str, fixtures_dir):
    """A settings object pointing at a real migrated database, plus a fresh
    registry -- `run_service.registry` is a module global and would otherwise
    carry state between tests.

    This used to copy the seeded database with SQLite's online backup API, so
    that `settings` had a second file to point at while `conn` kept its own. There
    is nothing to copy now, and no `backup()` to copy it with: `conn` and
    `database_url` are two connections to the SAME per-test database, which is
    what the copy was emulating.
    """
    from venona.settings import get_settings

    settings = get_settings()

    original = run_service.registry
    run_service.registry = run_service.RunRegistry()
    try:
        yield _FakeRequest(settings), database_url
    finally:
        run_service.registry = original


async def _drain(run_id: str, timeout: float = 2.0) -> None:
    handle = run_service.registry.handle(run_id)
    if handle and handle.task:
        await asyncio.wait_for(handle.task, timeout=timeout)


async def test_a_lookup_starts_while_a_research_run_is_in_flight(launchable, monkeypatch) -> None:
    """The regression this whole contract exists to prevent.

    A research run takes tens of minutes. For that entire window the hover
    card's "Look this up" button answered 409 -- which is exactly when someone
    is reading a brief and hits an undefined acronym.
    """
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    await run_service.registry.submit("run:pipeline", "on_demand")

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        return run_launcher.LaunchOutcome(data={"grounded": True})

    started = await run_launcher.launch(
        request, kind="lookup", params={"surface": "MQ-9"}, work=work, stage="lookup"
    )
    assert started["status"] == "running", "a lookup must not wait on the pipeline"
    assert started["lane"] == run_types.LIGHT

    await _drain(started["run_id"])
    probe = db.connect(database_url)
    try:
        row = probe.execute("SELECT status FROM runs WHERE id=?", (started["run_id"],)).fetchone()
        assert row["status"] == "succeeded"
    finally:
        probe.close()


async def test_an_overflowing_light_lane_queues_rather_than_refusing(
    launchable, monkeypatch
) -> None:
    """Queued, not 409. A lookup someone asked for is still wanted 30 seconds
    later, so refusing it makes them click again rather than saving any work."""
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    release = asyncio.Event()

    async def blocking(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        await release.wait()
        return run_launcher.LaunchOutcome()

    slots = run_types.LANES[run_types.LIGHT]
    running = [
        await run_launcher.launch(
            request, kind="lookup", params={"surface": f"A{i}"}, work=blocking, stage="lookup"
        )
        for i in range(slots)
    ]
    assert all(r["status"] == "running" for r in running)

    queued = await run_launcher.launch(
        request, kind="lookup", params={"surface": "LATE"}, work=blocking, stage="lookup"
    )
    assert queued["status"] == "queued"

    probe = db.connect(database_url)
    try:
        row = probe.execute("SELECT status FROM runs WHERE id=?", (queued["run_id"],)).fetchone()
        assert row["status"] == "queued", "the row must say so, not just the registry"
    finally:
        probe.close()

    release.set()
    for started in [*running, queued]:
        await _drain(started["run_id"])

    probe = db.connect(database_url)
    try:
        statuses = {
            r["id"]: r["status"] for r in probe.execute("SELECT id, status FROM runs").fetchall()
        }
    finally:
        probe.close()
    assert statuses[queued["run_id"]] == "succeeded", "a queued run must eventually run"


async def test_a_failing_lookup_frees_its_slot(launchable, monkeypatch) -> None:
    """A leaked slot used to mean 409 until restart. It is worse now: every run
    queued behind it never starts at all, silently and permanently."""
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    async def explode(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        raise RuntimeError("the lexicon has a stray tab in it")

    started = await run_launcher.launch(
        request, kind="lookup", params={}, work=explode, stage="lookup"
    )
    await _drain(started["run_id"])

    assert not run_service.registry.lane_full(run_types.LIGHT)
    assert run_service.registry.handle(started["run_id"]) is None

    probe = db.connect(database_url)
    try:
        row = probe.execute(
            "SELECT status, error FROM runs WHERE id=?", (started["run_id"],)
        ).fetchone()
        assert row["status"] == "failed"
        assert "stray tab" in row["error"]
    finally:
        probe.close()


async def test_a_lookup_that_grounds_nothing_ran_cleanly_and_says_so(
    launchable, monkeypatch
) -> None:
    """⭐ A run that tried and landed nothing must not read like one that
    worked -- and must not read like one that BROKE either.

    This is the shape of 44 of the 68 lookups on the defence corpus: a run that
    made its calls, read its pages, found no definition, and wrote an empty
    `warnings` array and a null `error`. There was nowhere to say "the answer
    is no" except the status field, so it said `succeeded_partial`: a claim of
    degradation on a run that degraded in no way at all.

    Two fields, two claims. ADR 0183.
    """
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    async def rejected(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        # No `status`: the work says nothing about execution, so `_runner`
        # derives it from `ctx.degraded`, which nothing here set.
        return run_launcher.LaunchOutcome(
            outcome="nothing_found", data={"grounded": False, "rejected": []}
        )

    started = await run_launcher.launch(
        request, kind="lookup", params={}, work=rejected, stage="lookup"
    )
    await _drain(started["run_id"])

    probe = db.connect(database_url)
    try:
        row = probe.execute(
            "SELECT status, outcome FROM runs WHERE id=?", (started["run_id"],)
        ).fetchone()
        assert row["status"] == "succeeded"
        assert row["outcome"] == "nothing_found"
        # And it reaches a reader in that kind of run's own words.
        assert run_schema.decorate(dict(row) | {"kind": "lookup"})["outcome_text"] == "not found"
    finally:
        probe.close()


async def test_a_degraded_lookup_that_grounds_nothing_says_both(launchable, monkeypatch) -> None:
    """The pair that one field could never carry.

    A lookup whose every candidate refused us, timed out, or served only a PDF
    has the same ANSWER as one that read six pages and found nothing in them --
    no definition -- and a completely different machine story. The first is a
    degradation and should be retried when the network is fixed; the second is
    finished business.
    """
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    async def unreachable(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        target.ctx.warn("Tried 4 source(s) and read none: every host refused us.")
        return run_launcher.LaunchOutcome(
            outcome="nothing_found", data={"grounded": False, "pages_read": 0}
        )

    started = await run_launcher.launch(
        request, kind="lookup", params={}, work=unreachable, stage="lookup"
    )
    await _drain(started["run_id"])

    probe = db.connect(database_url)
    try:
        row = probe.execute(
            "SELECT status, outcome, warnings FROM runs WHERE id=?", (started["run_id"],)
        ).fetchone()
        # Not `failed`, which is what "we could not look" reported when the
        # only way to distinguish it was to spend the status field on it.
        assert row["status"] == "succeeded_partial"
        assert row["outcome"] == "nothing_found"
        assert "refused us" in row["warnings"]
    finally:
        probe.close()


async def test_a_light_run_terminates_its_event_stream(launchable, monkeypatch) -> None:
    """`done` is what closes the browser's `EventSource`; without it the client
    reconnects forever. Neither lookup endpoint ever emitted one -- they wrote a
    terminal `runs` row and stopped -- which only went unnoticed because the
    hover card polls instead of streaming."""
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        return run_launcher.LaunchOutcome()

    started = await run_launcher.launch(
        request, kind="lookup", params={}, work=work, stage="lookup"
    )
    await _drain(started["run_id"])

    probe = db.connect(database_url)
    try:
        types = [
            r["type"]
            for r in probe.execute(
                "SELECT type FROM run_events WHERE run_id=? ORDER BY seq", (started["run_id"],)
            )
        ]
    finally:
        probe.close()
    assert types[-1] == "done"
    assert "stage_started" in types and "stage_finished" in types


async def test_a_light_run_records_its_stage(launchable, monkeypatch) -> None:
    """So the detail view has something to render. Lookups never wrote a
    `run_stages` row, which is why their detail page said the run "predates
    per-stage accounting"."""
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        return run_launcher.LaunchOutcome(data={"grounded": True})

    started = await run_launcher.launch(
        request, kind="lookup", params={}, work=work, stage="lookup"
    )
    await _drain(started["run_id"])

    probe = db.connect(database_url)
    try:
        rows = probe.execute(
            "SELECT stage, status FROM run_stages WHERE run_id=?", (started["run_id"],)
        ).fetchall()
    finally:
        probe.close()
    assert [(r["stage"], r["status"]) for r in rows] == [("lookup", "ok")]


async def test_a_second_research_run_is_refused_at_the_atomic_point(
    launchable, monkeypatch
) -> None:
    """The endpoint's pre-flight and the actual submit are separated by an
    `await`. Losing that race must still 409 -- not quietly queue a second
    forty-minute pipeline that starts by itself some minutes after the caller
    was told nothing had happened."""
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    await run_service.registry.submit("run:incumbent", "on_demand")

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        return run_launcher.LaunchOutcome()

    with pytest.raises(run_launcher.LaneBusy):
        await run_launcher.launch(request, kind="on_demand", params={}, work=work)

    # And the refusal must not leave a row or a queue entry behind.
    probe = db.connect(database_url)
    try:
        assert probe.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0
    finally:
        probe.close()
    assert run_service.registry.queued(run_types.RESEARCH) == []


async def test_cancelling_a_running_light_run_reaches_its_loop(launchable, monkeypatch) -> None:
    """`RunContext.check_cancel` only fires if something set `ctx._cancelled`,
    and the cancel endpoint sets `handle.cancelled` instead. `execute_run`
    bridges the two in its own closure; without the same bridge in the launcher,
    a multi-item light run would call `check_cancel()` between every item and
    never once stop."""
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    from venona.pipeline.types import RunCancelled

    reached = asyncio.Event()
    stopped: list[str] = []

    async def looping(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        for _ in range(100):
            try:
                target.check_cancel()
            except RunCancelled:
                stopped.append("yes")
                raise
            reached.set()
            await asyncio.sleep(0.01)
        return run_launcher.LaunchOutcome()

    started = await run_launcher.launch(
        request, kind="definitions", params={}, work=looping, stage="lookup"
    )
    await asyncio.wait_for(reached.wait(), timeout=2)
    assert await run_service.registry.cancel(started["run_id"])
    await _drain(started["run_id"])

    assert stopped == ["yes"], "the loop must observe the cancel between items"
    probe = db.connect(database_url)
    try:
        row = probe.execute("SELECT status FROM runs WHERE id=?", (started["run_id"],)).fetchone()
        assert row["status"] == "cancelled"
    finally:
        probe.close()


async def test_a_run_that_never_checks_is_stopped_for_it(launchable, monkeypatch) -> None:
    """⭐ A cancel that only sets a flag is not a cancel.

    Every `check_cancel()` in the pipeline sits BETWEEN items, so a run inside a
    model call notices nothing until the call it is paying for comes back --
    observed as the active run ten minutes after an accepted cancel. After the
    grace the task itself is cancelled, which is the path
    `llm/executor.py::_reap` kills the subprocess on. ADR 0231.
    """
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    reached = asyncio.Event()

    async def deaf(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        reached.set()
        # Never checks, exactly like a stage waiting on a model call.
        await asyncio.sleep(60)
        return run_launcher.LaunchOutcome()

    started = await run_launcher.launch(
        request, kind="definitions", params={}, work=deaf, stage="lookup"
    )
    await asyncio.wait_for(reached.wait(), timeout=2)
    handle = run_service.registry.handle(started["run_id"])
    assert await run_service.registry.cancel(started["run_id"], grace=0.05)

    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(handle.task, timeout=2)
    assert handle.task.done(), "the grace passed and nothing stopped the run"

    probe = db.connect(database_url)
    try:
        row = probe.execute(
            "SELECT status, error FROM runs WHERE id=?", (started["run_id"],)
        ).fetchone()
        # `cancelled`, and with nothing in `error`: a run stopped on purpose is
        # not a run that broke, and the person who stopped it must not be shown
        # a failure.
        assert row["status"] == "cancelled"
        assert row["error"] is None
    finally:
        probe.close()


async def test_a_forced_cancel_settles_the_run_and_its_open_stage(launchable, monkeypatch) -> None:
    """⭐ Forcing a run must leave neither the row nor its stage `running`.

    This was the case a run that settled ITSELF could not survive: it wrote its
    terminal row after its own try/finally, the launcher stood back for it, and
    a task cancelled inside reached neither -- so the row stayed `running` for
    ever with the stage it died in still open under it. One writer removes the
    gap rather than patching it, and this holds the guarantee either way.
    """
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    reached = asyncio.Event()

    async def owns_its_ending(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        run_service.set_stage(target.conn, target.run_id, "curate")
        run_service.record_stage(
            target.conn,
            run_id=target.run_id,
            stage="curate",
            status="running",
            started_at="2026-09-13T00:00:00Z",
        )
        target.conn.commit()
        reached.set()
        await asyncio.sleep(60)
        return run_launcher.LaunchOutcome()

    started = await run_launcher.launch(request, kind="on_demand", params={}, work=owns_its_ending)
    await asyncio.wait_for(reached.wait(), timeout=2)
    handle = run_service.registry.handle(started["run_id"])
    assert await run_service.registry.cancel(started["run_id"], grace=0.05)

    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(handle.task, timeout=2)

    probe = db.connect(database_url)
    try:
        row = probe.execute(
            "SELECT status, finished_at FROM runs WHERE id=?", (started["run_id"],)
        ).fetchone()
        assert row["status"] == "cancelled"
        assert row["finished_at"]
        open_stages = probe.execute(
            "SELECT COUNT(*) AS n FROM run_stages WHERE run_id=? AND status='running'",
            (started["run_id"],),
        ).fetchone()["n"]
        assert open_stages == 0, "a cancelled run must not leave a stage running under it"
    finally:
        probe.close()


async def test_a_run_that_stops_inside_the_grace_is_never_forced(launchable, monkeypatch) -> None:
    """The grace is what keeps a clean exit clean. A run that notices at its
    next check unwinds through `RunCancelled`, records its stage and writes its
    own row -- and must never have its task cancelled out from under that."""
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    from venona.pipeline.types import RunCancelled

    reached = asyncio.Event()

    async def attentive(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        for _ in range(1000):
            target.check_cancel()
            reached.set()
            await asyncio.sleep(0.01)
        raise AssertionError("the cancel was never observed")

    started = await run_launcher.launch(
        request, kind="definitions", params={}, work=attentive, stage="lookup"
    )
    await asyncio.wait_for(reached.wait(), timeout=2)
    handle = run_service.registry.handle(started["run_id"])
    assert await run_service.registry.cancel(started["run_id"], grace=5)

    await asyncio.wait_for(handle.task, timeout=2)
    assert not handle.task.cancelled(), "a run that stopped on its own was forced anyway"
    assert isinstance(RunCancelled("x"), Exception)

    probe = db.connect(database_url)
    try:
        row = probe.execute("SELECT status FROM runs WHERE id=?", (started["run_id"],)).fetchone()
        assert row["status"] == "cancelled"
    finally:
        probe.close()


async def test_the_same_lookup_twice_is_one_run(launchable, monkeypatch) -> None:
    """Hovering the same card twice, or the definition queue and the hover card
    asking about one surface, must not pay for the work twice -- and must not
    let the loser's `mark_undefined` land on top of the winner's result. The
    single global slot made this impossible by refusing the second; the lane
    split has to answer it directly."""
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    release = asyncio.Event()

    async def blocking(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        await release.wait()
        return run_launcher.LaunchOutcome()

    params = {"target_kind": "acronym", "surface": "NGC2"}
    first = await run_launcher.launch(
        request, kind="lookup", params=params, work=blocking, stage="lookup"
    )
    second = await run_launcher.launch(
        request, kind="lookup", params=dict(params), work=blocking, stage="lookup"
    )

    assert second["run_id"] == first["run_id"]
    assert second.get("coalesced") is True

    release.set()
    await _drain(first["run_id"])

    probe = db.connect(database_url)
    try:
        assert probe.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 1
    finally:
        probe.close()


async def test_lookups_for_different_targets_are_separate_runs(launchable, monkeypatch) -> None:
    """The coalescing above must key on the TARGET, not merely on the kind --
    otherwise the second acronym anyone looks up silently returns the first
    one's run and never gets defined."""
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    release = asyncio.Event()

    async def blocking(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        await release.wait()
        return run_launcher.LaunchOutcome()

    a = await run_launcher.launch(
        request, kind="lookup", params={"surface": "NGC2"}, work=blocking, stage="lookup"
    )
    b = await run_launcher.launch(
        request, kind="lookup", params={"surface": "C4ISR"}, work=blocking, stage="lookup"
    )
    assert a["run_id"] != b["run_id"]

    release.set()
    await _drain(a["run_id"])
    await _drain(b["run_id"])


def test_the_history_listing_carries_what_each_run_was_about(conn: db.Conn) -> None:
    """`params` is in the LIST projection, not only the detail one.

    It was detail-only while every run was a full pipeline and the answer was
    always "the whole corpus". With light runs in the same table, a history
    without it is N identical rows reading "Definition" -- which is what
    the Runs page rendered until this was noticed on screen.
    """
    conn.execute(
        "INSERT INTO runs (id, kind, status, params, counters, warnings, started_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("run:l", "lookup", "succeeded", '{"surface": "NGC2"}', "{}", "[]", NOW),
    )
    conn.commit()

    items, _total, _hidden = run_service.list_runs(conn)
    row = next(r for r in items if r["id"] == "run:l")
    assert row["params"] == {"surface": "NGC2"}
    assert run_schema.decorate(row)["label"] == "Definition"


def test_the_acronym_sweep_is_a_light_kind_with_one_stage() -> None:
    """`lookup_all` is the longest-running kind in the light lane, and still
    light: the lane is about what a run WRITES, not how long it takes. It
    writes `acronym_definitions` and nothing else, so it cannot collide with a
    pipeline the way a second `documents`/`clusters` writer would.
    """
    sweep = run_types.type_for("lookup_all")
    assert sweep.lane == run_types.LIGHT
    assert sweep.stages == ("lookup",)
    assert sweep.detail == "single", "a one-stage run renders a result, not a spine"
    assert "lookup" not in orchestrator.STAGES


def test_every_run_types_summary_names_real_counters() -> None:
    """Contract C6 freezes the counter names. A summary naming one that does not
    exist renders a column the run can never populate -- and `RunCounters` is
    the only place that can say so."""
    from venona.pipeline.types import RunCounters

    counters = RunCounters()
    for run_type in run_types.RUN_TYPES.values():
        for name in run_type.summary:
            assert hasattr(counters, name), f"{run_type.kind}: {name}"


def test_adding_a_kind_needs_no_change_outside_the_table() -> None:
    """What makes C11's "requires touching" list honest.

    Every consumer derives from `RUN_TYPES`: the lane guards ask
    `kinds_in_lane`, the payload is generated, the row decoration is computed,
    and the frontend loads `/api/run-types` at mount. If a kind ever has to be
    named in a second place, this fails and the list applies again.
    """
    payload = run_types.as_payload()
    assert {t["kind"] for t in payload["types"]} == set(run_types.RUN_TYPES)
    for kind in run_types.RUN_TYPES:
        assert kind in run_types.kinds_in_lane(run_types.lane_for(kind))
        decorated = run_schema.decorate({"id": "run:x", "kind": kind})
        assert decorated["label"] and decorated["lane"] and decorated["detail"]


# ── an accepted cancel is not a stopped run ──────────────────────────


async def test_health_says_a_cancelled_run_is_still_running(api_client) -> None:
    """⭐ 202 reads as "stopped" and means "asked to stop".

    Cancellation is cooperative: the flag is set and the run notices at its next
    check. Observed 2026-08-25: still the active run ten minutes after an
    accepted cancel, spending throughout, and it ended only when the process was
    brought down. With `active_run` alone there is nothing on the wire that
    distinguishes "it ignored me" from "it is on its way out", so a reader who
    pressed cancel has no way to tell whether it worked.
    """
    await run_service.registry.submit("run:cancelme", "on_demand", {})
    try:
        health = (await api_client.get("/api/health")).json()
        assert health["active_run"] == "run:cancelme"
        assert health["active_run_cancelling"] is False

        assert await run_service.registry.cancel("run:cancelme")

        health = (await api_client.get("/api/health")).json()
        # STILL ACTIVE, and that is the honest answer -- the run has not stopped.
        assert health["active_run"] == "run:cancelme"
        assert health["active_run_cancelling"] is True
        # And on the run's own row, which is where the Runs page reads it: the
        # research lane is not the only lane a person can cancel from.
        assert [r["cancelling"] for r in health["runs"]["active"]] == [True]
    finally:
        await run_service.registry.release("run:cancelme")


def test_fetch_checks_for_cancellation_before_each_source() -> None:
    """The longest unattended stretch in the pipeline is feed collection — a
    dozen sources, each with retries and a per-host delay — and a check that
    runs only after the whole gather leaves a cancellation invisible until the
    slowest one comes back. That gap IS the minutes between an accepted cancel
    and a run that stops.

    Asserted against the source because the alternative is a timing test over a
    real network stage."""
    import inspect

    from venona.pipeline import fetch

    source = inspect.getsource(fetch.run_fetch)
    collect = source[source.index("async def collect_counting") :]
    before_await = collect[: collect.index("await _collect_one")]
    assert "ctx.check_cancel()" in before_await, (
        "collection can only notice a cancellation after every feed has answered"
    )


async def test_a_wrapping_stage_owns_every_event_inside_it(launchable, monkeypatch) -> None:
    """Two stages cannot both be running, and one of them was never started.

    A `research` run declares `("research", "analyze")`, so the live spine draws
    two segments -- but its whole job runs inside the single `research` stage
    that `POST /api/runs/research` wraps it in. The job calls `analyze`, which
    emits `stage_progress` naming `"analyze"` because under `orchestrator` that
    is true. Through the launcher it named a stage that had no `stage_started`,
    the client promotes a stage to `running` on any progress event, and both
    segments pulsed for the length of the run while the header announced
    research over a count that was advancing analyze.

    Observed on production 2026-09-13: 1178 events on one run, a single
    `stage_started` (research), no `stage_finished`, and nine `stage_progress`
    every one of them tagged `analyze`.
    """
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        # Exactly what `pipeline/analyze.py` does, from inside another stage.
        await target.emit("stage_progress", "analyze", {"current": 14, "total": 70})
        return run_launcher.LaunchOutcome(data={})

    started = await run_launcher.launch(
        request, kind="lookup", params={}, work=work, stage="research"
    )
    await _drain(started["run_id"])

    probe = db.connect(database_url)
    try:
        rows = probe.execute(
            "SELECT type, stage FROM run_events WHERE run_id=? ORDER BY seq",
            (started["run_id"],),
        ).fetchall()
    finally:
        probe.close()

    named = {r["stage"] for r in rows if r["stage"]}
    assert named == {"research"}, f"only the wrapping stage may be named, got {named}"
    # And the progress still ARRIVES -- re-homed, not dropped. A stage-less
    # event is discarded by the client, so silencing it would trade two pulsing
    # segments for a long stage with no live signal at all.
    progress = [r for r in rows if r["type"] == "stage_progress"]
    assert len(progress) == 1 and progress[0]["stage"] == "research"


async def test_a_launch_with_no_wrapping_stage_lets_work_name_its_own(
    launchable, monkeypatch
) -> None:
    """The setup wizard's case, and why the rule above is conditional.

    `domain_setup` launches with no `stage` and emits a `stage_started` per step
    of its own. Forcing a stage name there would collapse its whole sequence
    onto one segment.
    """
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        await target.emit("stage_started", "profile", {})
        await target.emit("stage_started", "sources", {})
        return run_launcher.LaunchOutcome(data={})

    started = await run_launcher.launch(request, kind="lookup", params={}, work=work)
    await _drain(started["run_id"])

    probe = db.connect(database_url)
    try:
        rows = probe.execute(
            "SELECT stage FROM run_events WHERE run_id=? AND type='stage_started' ORDER BY seq",
            (started["run_id"],),
        ).fetchall()
    finally:
        probe.close()

    assert [r["stage"] for r in rows] == ["profile", "sources"]


async def test_a_running_stage_has_a_step_record_before_it_finishes(
    launchable, monkeypatch
) -> None:
    """The Steps table is read while a run is LIVE, which is when it was empty.

    `orchestrator`'s `stage()` writes a `running` row on the way in and settles
    it on the way out. The launcher wrote only the second half, so a run through
    it had no `run_stages` row at all until it stopped -- and the run page,
    reading that table, told a reader "No step records. This run predates step
    timing, or ended before its first step finished" about a research run that
    was two hours into its work in front of them.

    `record_stage` upserts on `(run_id, stage)`, so the finishing write settles
    this row rather than adding a second.
    """
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    seen: list[tuple[str, str | None]] = []

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        probe = db.connect(database_url)
        try:
            rows = probe.execute(
                "SELECT stage, status FROM run_stages WHERE run_id=?", (target.run_id,)
            ).fetchall()
            seen.extend((r["stage"], r["status"]) for r in rows)
        finally:
            probe.close()
        return run_launcher.LaunchOutcome(data={})

    started = await run_launcher.launch(
        request, kind="lookup", params={}, work=work, stage="research"
    )
    await _drain(started["run_id"])

    assert seen == [("research", "running")], f"mid-run step records were {seen}"

    probe = db.connect(database_url)
    try:
        rows = probe.execute(
            "SELECT stage, status FROM run_stages WHERE run_id=?", (started["run_id"],)
        ).fetchall()
    finally:
        probe.close()
    # One row, settled -- not two.
    assert [(r["stage"], r["status"]) for r in rows] == [("research", "ok")]


async def test_the_ledger_attributes_a_call_to_the_stage_the_run_has_open(
    launchable, monkeypatch
) -> None:
    """`llm_calls.stage` is a fact about the RUN, not about the task name.

    `STAGE_FOR_TASK` maps `analyze` -> `analyze`, which is right under the
    pipeline and wrong for a corroboration sweep: that analyses what it brings
    back inside its own `research` stage. So every one of those calls ledgered a
    stage the run never opened, while `run_stages` recorded the one it did --
    two honest answers to one question, and the metrics page rendered the
    phantom as a stage with no status and no start time.

    The convention survives as the FALLBACK, for a call made outside any stage.
    """
    from venona.llm.executor import LlmResult, Usage
    from venona.llm.usage import STAGE_FOR_TASK, record_call

    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )
    assert STAGE_FOR_TASK["analyze"] == "analyze", "the premise: the task says analyze"

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        record_call(
            target.conn,
            call_id="call-1",
            run_id=target.run_id,
            task="analyze",
            result=LlmResult(data=None, usage=Usage()),
            model="m",
            effort="low",
            tools=(),
            created_at=NOW,
            stage=target.ctx.stage,
        )
        return run_launcher.LaunchOutcome(data={})

    started = await run_launcher.launch(
        request, kind="lookup", params={}, work=work, stage="research"
    )
    await _drain(started["run_id"])

    probe = db.connect(database_url)
    try:
        row = probe.execute(
            "SELECT stage FROM llm_calls WHERE run_id=?", (started["run_id"],)
        ).fetchone()
    finally:
        probe.close()
    assert row["stage"] == "research", "the open stage, not the task's convention"


async def test_a_call_outside_any_stage_still_falls_back_to_the_task(
    launchable, monkeypatch
) -> None:
    """The fallback, which is what keeps an unattributed call attributable."""
    from venona.llm.executor import LlmResult, Usage
    from venona.llm.usage import record_call

    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        assert target.ctx.stage is None, "no wrapping stage was opened"
        record_call(
            target.conn,
            call_id="call-1",
            run_id=target.run_id,
            task="analyze",
            result=LlmResult(data=None, usage=Usage()),
            model="m",
            effort="low",
            tools=(),
            created_at=NOW,
            stage=target.ctx.stage,
        )
        return run_launcher.LaunchOutcome(data={})

    started = await run_launcher.launch(request, kind="lookup", params={}, work=work)
    await _drain(started["run_id"])

    probe = db.connect(database_url)
    try:
        row = probe.execute(
            "SELECT stage FROM llm_calls WHERE run_id=?", (started["run_id"],)
        ).fetchone()
    finally:
        probe.close()
    assert row["stage"] == "analyze"


async def test_work_that_dies_before_its_first_stage_still_settles(launchable, monkeypatch) -> None:
    """The other half of `test_a_setup_failure_reaches_the_one_writer`.

    A pipeline run's setup reads user-editable YAML and can raise before any
    stage opens. `execute_run` used to answer that itself, which is why there
    were two terminal writers; it raises now, and this is the one that catches.
    A row left `running` is an SSE stream that never closes and a browser that
    reconnects to it for ever.
    """
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        raise ValueError("profile/relevance.yaml: bad indent")

    started = await run_launcher.launch(request, kind="on_demand", params={}, work=work)
    await _drain(started["run_id"])

    probe = db.connect(database_url)
    try:
        row = probe.execute(
            "SELECT status, error, stage, finished_at FROM runs WHERE id=?", (started["run_id"],)
        ).fetchone()
        types = [
            r["type"]
            for r in probe.execute(
                "SELECT type FROM run_events WHERE run_id=?", (started["run_id"],)
            )
        ]
    finally:
        probe.close()

    assert row["status"] == "failed"
    assert "bad indent" in (row["error"] or "")
    assert row["finished_at"] and row["stage"] is None
    assert "done" in types, "without this the EventSource never closes"


async def test_a_cooperative_cancel_settles_the_run_and_its_open_stage(
    launchable, monkeypatch
) -> None:
    """The ordinary cancel: work that checks the flag between its own items.

    `run_launcher` is the only terminal writer, so a cancel has exactly one
    place to land -- and the stage left open by the raise is settled
    `interrupted` by `finish_run`, which is what stops a cancelled run showing
    a stage running under a finished header.
    """
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    reached = asyncio.Event()

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        async with target.scope.stage("analyze"):
            reached.set()
            for _ in range(200):
                target.ctx.check_cancel()
                await asyncio.sleep(0.02)
        return run_launcher.LaunchOutcome(data={})

    started = await run_launcher.launch(request, kind="lookup", params={}, work=work)
    await asyncio.wait_for(reached.wait(), timeout=3)
    # A grace long enough that this can only be the cooperative path.
    assert await run_service.registry.cancel(started["run_id"], grace=30)
    handle = run_service.registry.handle(started["run_id"])
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(handle.task, timeout=5)

    _assert_settled_cancel(database_url, started["run_id"], stage="analyze")


async def test_a_forced_cancel_settles_work_that_never_checks(launchable, monkeypatch) -> None:
    """⭐ The case the removed backstop existed for.

    A run that settled its own row wrote it AFTER its own try/finally, so a task
    cancelled inside reached neither that line nor the launcher's -- which is
    why there was a separate `abandon_run` for a forced cancel. With one writer
    the `finally` settles every run there is, and `finish_run` is called
    synchronously before any `await` precisely because under cancellation every
    await raises again.
    """
    request, database_url = launchable
    monkeypatch.setattr(
        type(request.app.state.settings), "database_url", property(lambda s: database_url)
    )

    reached = asyncio.Event()

    async def stubborn(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        await target.scope.open_stage("brief")
        reached.set()
        # Never checks the flag and never returns: only the force reaches this.
        await asyncio.sleep(120)
        return run_launcher.LaunchOutcome(data={})

    started = await run_launcher.launch(request, kind="lookup", params={}, work=stubborn)
    await asyncio.wait_for(reached.wait(), timeout=3)
    assert await run_service.registry.cancel(started["run_id"], grace=0.1)
    handle = run_service.registry.handle(started["run_id"])
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(handle.task, timeout=5)
    await asyncio.sleep(0.2)

    _assert_settled_cancel(database_url, started["run_id"], stage="brief")
    assert not run_service.registry.lane_full(run_types.LIGHT), "a forced cancel leaked its slot"


def _assert_settled_cancel(database_url: str, run_id: str, *, stage: str) -> None:
    """Every guarantee a cancelled run owes, whichever way it was stopped."""
    probe = db.connect(database_url)
    try:
        run = probe.execute(
            "SELECT status, stage, finished_at, error FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        steps = probe.execute(
            "SELECT stage, status FROM run_stages WHERE run_id=?", (run_id,)
        ).fetchall()
        events = probe.execute("SELECT type FROM run_events WHERE run_id=?", (run_id,)).fetchall()
        types = [r["type"] for r in events]
    finally:
        probe.close()

    assert run["status"] == "cancelled", "a run somebody stopped is never a failure"
    assert run["error"] is None, "nor does it owe an error"
    assert run["finished_at"], "an unsettled row is a lane nothing can reuse"
    assert run["stage"] is None, "and it is not still inside a stage"
    assert [(r["stage"], r["status"]) for r in steps] == [(stage, "interrupted")], (
        "the open stage is settled `interrupted` -- stopped, not failed"
    )
    assert "done" in types, "without this the browser reconnects to the stream for ever"
