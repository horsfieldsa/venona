from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, computed_field, model_validator

from venona.api.deps import Window, get_db, page_window
from venona.api.schemas.metrics import RunMetrics
from venona.api.schemas.run import (
    RunCancelling,
    RunDetail,
    RunEventList,
    RunHidden,
    RunHideAll,
    RunList,
    RunPreview,
    RunStarted,
    RunTypeList,
    ScheduledRunTriggered,
    decorate,
)
from venona.api.security import Principal
from venona.db import core as db
from venona.llm.budget import Budget
from venona.pipeline.orchestrator import STAGES as PIPELINE_STAGES
from venona.pipeline.orchestrator import execute_run
from venona.pipeline.types import with_overrides
from venona.services import metrics_service, run_launcher, run_service, run_types
from venona.settings import Settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


def _research_kinds_pattern() -> str:
    """The kinds this endpoint may start, as a pydantic pattern.

    Derived from `run_types` so adding a research kind is one table entry. The
    light-lane kinds are deliberately absent: a lookup needs a target, so it has
    its own endpoint that knows what to ask for.
    """
    kinds = sorted(run_types.kinds_in_lane(run_types.RESEARCH))
    return "^(" + "|".join(kinds) + ")$"


def _refuse_if_research_is_busy(settings: Settings) -> None:
    """409 if the research lane is occupied, in this process or another one."""
    in_process = run_service.registry.active_run_id(run_types.RESEARCH)
    if in_process:
        raise HTTPException(409, detail=f"run {in_process} is already active")

    probe = db.connect(settings.database_url)
    try:
        conflict = run_service.lane_conflict(probe, lane=run_types.RESEARCH)
    finally:
        probe.close()
    if conflict:
        raise HTTPException(409, detail=f"run {conflict['id']} is already active")


class TriggerRun(BaseModel):
    kind: str = Field(default="on_demand", pattern=_research_kinds_pattern())
    limit_per_source: int | None = Field(default=None, ge=1, le=100)
    analyze_limit: int | None = Field(default=None, ge=1, le=200)
    skip_brief: bool = False
    # The three a deep read widens. Days rather than hours because that is the
    # unit the question is asked in; the conversion happens once, here.
    lookback_days: int | None = Field(default=None, ge=1, le=365)
    discovery_max_age_days: int | None = Field(default=None, ge=1, le=365)
    max_calls: int | None = Field(default=None, ge=1, le=5000)
    #: Whether to go looking for older material. Off by default, and that is the
    #: whole point: discovery was 52% of this instance's whole spend (2026-09-11).
    go_looking: bool = False

    @model_validator(mode="after")
    def _widening_requires_no_brief(self) -> TriggerRun:
        """A widened window is legal only for a run that writes no brief.

        The same rule `retrieval_window` enforces, stated here so a person gets
        a 422 naming the field rather than a run that starts and then fails.
        `retrieval_window` stays the backstop; this is the message.
        """
        writes_brief = "brief" in run_types.type_for(self.kind).stages and not self.skip_brief
        if writes_brief and self.lookback_days is not None:
            raise ValueError(
                "lookback_days is only for a run that writes no brief; pass skip_brief"
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def window_text(self) -> str:
        """What this run was about, for the history list.

        A `computed_field`, so it reaches `params` through `model_dump` and the
        table's `subject` can point at it. A column of rows all reading "Deep
        read" with nothing beside them is not a history.
        """
        span = f"the last {self.lookback_days} days" if self.lookback_days else "everything waiting"
        return f"{span}, going looking" if self.go_looking else span

    @property
    def stages_only(self) -> tuple[str, ...] | None:
        """Which stages to run, or None for every stage this pipeline has.

        DERIVED FROM THE KIND'S OWN DECLARATION, never from a rule about fetch.
        Written the other way round -- drop `fetch` from any kind that declares
        it -- this took the fetch stage off `on_demand` and `daily` too, so
        `Run now` analysed what was already stored and never went and got
        anything. It ran, reported `succeeded`, and fetched nothing:
        `run:06aa4b85` (2026-09-11) has three stage rows and no `fetch` among
        them. The scheduler was untouched only because it calls `execute_run`
        directly.
        """
        declared = tuple(run_types.type_for(self.kind).stages)
        # A kind may declare stages this orchestrator has never heard of --
        # `research` names one of its own -- so intersect rather than trust.
        pipeline = tuple(s for s in PIPELINE_STAGES if s in declared)
        if not pipeline or pipeline == PIPELINE_STAGES:
            return None
        if self.go_looking:
            return pipeline
        # Reading what is already stored is the deep read's default.
        return tuple(s for s in pipeline if s != "fetch") or None


@router.post(
    "",
    status_code=202,
    response_model=RunStarted,
    # `coalesced` is set only when an identical light-lane run was already
    # in flight; without this it is `false` on every other launch.
    response_model_exclude_unset=True,
)
async def trigger_run(payload: TriggerRun, request: Request) -> dict[str, Any]:
    """Start a full pipeline run and return immediately.

    The pipeline runs as a background task, so this returns in milliseconds. A
    multi-minute generation inside the request handler would freeze every other
    endpoint, including /health.

    **409 rather than queueing, and only against the research lane.** Light runs
    queue (contract C11) because a lookup someone asked for is still wanted 30
    seconds later. A second forty-minute pipeline almost never is -- and
    `scheduler.run_daily` has always taken the same view, skipping rather than
    stacking, on the grounds that if yesterday's run is still going at 06:00
    then starting another makes the problem worse.
    """
    settings = request.app.state.settings
    from venona.llm.prompts import load_profile

    _refuse_if_research_is_busy(settings)

    # BOTH OF THE TWO BLOCKS BELOW HANG ON THIS. They follow a brief, not a run:
    # a deep read writes none, so fanning out the readership's own briefs would
    # spend $0.45 a head (measured 2026-09-11) on an edition that does not exist,
    # and announcing it would tell every subscriber a brief had landed. Read from
    # the table rather than from `skip_brief` alone, so a kind with no brief stage
    # at all is covered without remembering to.
    writes_brief = "brief" in run_types.type_for(payload.kind).stages and not payload.skip_brief

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        outcome = await execute_run(
            scope=target.scope,
            run_id=target.run_id,
            settings=settings,
            conn=target.conn,
            graph=request.app.state.graph,
            kind=payload.kind,
            limit_per_source=payload.limit_per_source,
            analyze_limit=payload.analyze_limit,
            skip_brief=payload.skip_brief,
            only=payload.stages_only,
            lookback_hours=payload.lookback_days * 24 if payload.lookback_days else None,
            discovery_max_age_hours=(
                payload.discovery_max_age_days * 24 if payload.discovery_max_age_days else None
            ),
            max_calls=payload.max_calls,
        )
        if not writes_brief:
            return run_launcher.LaunchOutcome(
                status=outcome.status,
                outcome=outcome.outcome,
                brief_id=outcome.brief_id,
                error=outcome.error,
            )

        # A brief written outside the schedule is still new reading, and the
        # readers' own briefs derive from it. `personal_brief_fanout` decides
        # whether that pulls the readership along; best-effort, because a
        # failed warm pass degrades to lazy generation on first visit and must
        # not touch this run's own outcome.
        try:
            from venona.pipeline.types import format_millis
            from venona.services import personal_brief

            await personal_brief.warm_recent_readers(
                request.app,
                settings=settings,
                now_iso=format_millis(datetime.now(UTC)),
                scheduled=False,
            )
        except Exception:  # noqa: BLE001 - the run's own report must not depend on this
            log.exception("personal brief warm pass failed")
        # A brief written by hand is still the edition that landed, so the
        # readers who asked to be told are told. Deduped on the brief id, so a
        # rewrite of the same edition says nothing twice.
        from venona import scheduler

        await scheduler.announce_brief(request.app)
        return run_launcher.LaunchOutcome(
            status=outcome.status,
            outcome=outcome.outcome,
            brief_id=outcome.brief_id,
            error=outcome.error,
        )

    try:
        return await run_launcher.launch(
            request,
            kind=payload.kind,
            params=payload.model_dump(),
            work=work,
            profile_hash=load_profile(settings).short_hash,
            migrate=True,
            # What the pipeline needs beyond the default context.
            context={"graph": request.app.state.graph, "profile": load_profile(settings)},
        )
    except run_launcher.LaneBusy as exc:
        # The pre-flight above lost a race with a concurrent submit. Same 409,
        # decided at the point where the answer cannot change under it.
        raise HTTPException(409, detail=str(exc)) from None


class SecondSourceRun(BaseModel):
    """How far a corroboration sweep is allowed to reach.

    The defaults are a DAILY'S defaults and this is not a daily.
    `research_max_sweeps` is 3 and a sweep covers `research_entities_per_sweep`
    names, so an unconfigured run chases thirty of them -- which is the right
    size beside a morning's reading and is nothing against the 2,835 names
    holding relationships out of the graph. Naming a number here lifts the sweep
    cap to match it, because a limit the sweeps then truncate is a number that
    lies to the person who typed it.
    """

    names: int | None = Field(default=None, ge=1, le=2000)
    max_calls: int | None = Field(default=None, ge=1, le=5000)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def window_text(self) -> str:
        return f"the {self.names} worst-blocked names" if self.names else "the worst-blocked names"


#: What one search costs when this machine has never run one. Deliberately a
#: MEASURED figure rather than a round number, because a fallback that is
#: optimistic is worse than no quote at all: a search here asks forty URL slots
#: across ten names, and measured $2.50 on the live ledger, 2026-09-12.
#: Wall clock one chased name takes, for the duration half of the quote.
#: A constant: sweeps are batched, so there is no per-name span to measure.
FALLBACK_SEARCH_SECONDS = 20.0
FALLBACK_SEARCH_USD = 2.50
SEARCH_COST_SAMPLE = 60


@router.get("/second-source/preview", response_model=RunPreview)
def second_source_preview(
    names: int = Query(0, ge=0, le=2000),
    request: Request = None,  # type: ignore[assignment]
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """What the sweep would cost and what it could reveal.

    Both halves from the same source the run uses: the names come from
    `stuck_entities` with the argument the run will pass, so a preview cannot
    quote one population while the run works another -- the failure
    `RunPreview`'s docstring records.
    """
    from venona.db.repositories import pipeline_research as research_repo
    from venona.pipeline.research import stuck_entities

    settings = request.app.state.settings
    per_sweep = settings.pipeline.research_entities_per_sweep
    waiting = research_repo.blocked_name_count(conn)
    wanted = names or per_sweep * settings.pipeline.research_max_sweeps
    will_chase = min(wanted, waiting)

    rows = research_repo.sweep_costs(
        conn,
        limit=SEARCH_COST_SAMPLE,
        harness=run_launcher.lane_harness(request, run_types.RESEARCH),
    )
    per_search = sum(r["cost_usd"] for r in rows) / len(rows) if rows else FALLBACK_SEARCH_USD
    searches = max(1, -(-will_chase // per_sweep)) if will_chase else 0
    targets = stuck_entities(conn, limit=will_chase) if will_chase else []

    # PRICED PER NAME, though the model is billed per SEARCH over
    # `research_entities_per_sweep` of them. The shared shape prices the unit a
    # reader counts; `searches` travels in `extra` for the sentence that says
    # how the work is actually batched, beside `would_appear`, which is the
    # number this run is actually judged on.
    per_name = (searches * per_search / will_chase) if will_chase else per_search
    return {
        "eligible": waiting,
        "will_attempt": will_chase,
        "cap": wanted,
        "unit": "name",
        "unit_plural": "names",
        "per_item_usd": round(per_name, 3),
        "estimate_usd": round(searches * per_search, 2),
        "per_item_seconds": FALLBACK_SEARCH_SECONDS,
        "estimate_seconds": round(will_chase * FALLBACK_SEARCH_SECONDS),
        "cost_sample": len(rows),
        "sample": [t.name for t in targets[:8]],
        "extra": {
            "searches": searches,
            "per_search_usd": round(per_search, 3),
            "would_appear": sum(t.ready for t in targets),
        },
    }


@router.post("/second-source", status_code=202, response_model=RunStarted)
async def trigger_second_source(payload: SecondSourceRun, request: Request) -> dict[str, Any]:
    """Go looking for another publisher on relationships only one source has.

    A RESEARCH-LANE run with its own work closure rather than a kind
    `execute_run` can take: its first stage is `research`, which the pipeline
    has never heard of and `--only` must never accept. `run_types` says as much
    -- the two stage lists answer different questions.

    Corroboration is the one thing reading more of the same press cannot
    reliably fix. A relationship reported once stays hidden until somebody else
    reports it, and the daily only finds that by accident; this goes and asks.
    """
    settings = request.app.state.settings
    from venona.llm.prompts import load_profile

    _refuse_if_research_is_busy(settings)

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        from venona.graph.projector import project
        from venona.pipeline.analyze import run_analyze
        from venona.pipeline.research import coverage_outcome, run_research

        ctx = target.ctx
        budget = Budget(settings=with_overrides(settings, max_calls=payload.max_calls).llm)
        limit = payload.names or settings.pipeline.research_entities_per_sweep
        # The sweep cap follows the number of names rather than standing in
        # front of it. Batches are `research_entities_per_sweep` wide, so this
        # is how many it takes to reach every name that was asked for.
        per_sweep = settings.pipeline.research_entities_per_sweep
        sweeps = max(1, -(-limit // per_sweep))

        report = await run_research(ctx, limit=limit, max_sweeps=sweeps, budget=budget)
        analyzed = None
        if report.documents_new:
            # Unbounded by date: an article found today about a relationship
            # reported months ago is exactly what was gone looking for, and the
            # brief's window would refuse it. This run writes no brief.
            analyzed = await run_analyze(ctx, budget=budget, writes_brief=False, bounded=False)
        await project(target.conn, request.app.state.graph.driver)

        promoted = analyzed.promoted if analyzed else 0
        return run_launcher.LaunchOutcome(
            outcome=coverage_outcome(promoted, degraded=ctx.degraded),
            data={
                "targeted": len(report.targeted),
                "blocked": report.blocked_total,
                "ready": report.ready_total,
                "documents_new": report.documents_new,
                "promoted": promoted,
                "sweeps": report.sweeps,
            },
        )

    try:
        return await run_launcher.launch(
            request,
            kind="research",
            params=payload.model_dump(),
            work=work,
            profile_hash=load_profile(settings).short_hash,
            stage="research",
            migrate=True,
        )
    except run_launcher.LaneBusy as exc:
        raise HTTPException(409, detail=str(exc)) from None


@router.get("", response_model=RunList)
def list_runs(
    window: Window = Depends(page_window(default=25, cap=100)),
    include_hidden: bool = Query(False),
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """The runs list, and the one place a run abandoned by a dead process gets
    noticed.

    Reaping from a GET is deliberate. The other caller is the API lifespan, so
    a run killed with the API still up -- a `venona run` whose terminal was
    closed, a scheduler tick in a worker that died -- keeps a spinner turning
    until someone restarts the server, and this page is where a person goes to
    ask why. The sweep is lease-based and idempotent, and on a healthy instance
    it reads the handful of unfinished rows and writes nothing.
    """
    from venona.pipeline.types import SystemClock

    run_service.reap_interrupted(conn, now=SystemClock().now())
    items, total, hidden = run_service.list_runs(
        conn, limit=window.limit, offset=window.offset, include_hidden=include_hidden
    )
    return {
        "items": [decorate(item) for item in items],
        "total": total,
        "hidden": hidden,
        # The research-lane run only. Light runs are in `items` with a
        # `running`/`queued` status of their own; hoisting one of them here
        # would make the Runs page follow a 20-second lookup instead of the
        # pipeline it was watching.
        "active": run_service.registry.active_run_id(run_types.RESEARCH),
    }


@router.get("/types", response_model=RunTypeList)
def run_type_list() -> dict[str, Any]:
    """Contract C11: what kinds of run exist and how each one renders.

    Declared here rather than restated in the UI so adding a run type is one
    entry in `services/run_types.py`. The alternative -- a `switch (kind)` in a
    Vue component -- is the shape that made a one-call lookup draw a six-segment
    pipeline spine with five segments that never lit.

    Registered ABOVE `/{run_id}`: FastAPI matches in declaration order, and a
    path parameter that greedy would swallow `/runs/types` and 404 on "no run
    types".
    """
    return run_types.as_payload()


@router.post(
    "/schedule/trigger",
    status_code=202,
    response_model=ScheduledRunTriggered,
    response_model_exclude_unset=True,
)
async def trigger_scheduled(request: Request) -> dict[str, Any]:
    """Fire the scheduled daily, exactly as the 06:00 timer would.

    For an external cron -- a hosted cron job POSTing here with
    `Authorization: Bearer $VENONA_AUTH_API_TOKEN` -- on a deployment where the
    process is not guaranteed to be awake at fire time, so the in-process
    scheduler is turned off (`schedule.enabled: false`) and this endpoint IS
    the schedule. That is why `schedule.enabled` is deliberately not checked.

    `run_daily` owns everything that makes the scheduled path scheduled: the
    cross-process lane check, launch through `run_launcher`, and the notify
    reporting whose heartbeat is the dead-man's switch. A busy lane returns
    202 `skipped` -- see `ScheduledRunTriggered` for why not 409 -- which also
    makes a cron retry of an already-started fire harmless.
    """
    from venona import scheduler

    result = await scheduler.run_daily(request.app, request=request)
    if result.outcome == "error":
        raise HTTPException(500, detail=result.reason)
    body: dict[str, Any] = {"outcome": result.outcome}
    if result.run_id is not None:
        body["run_id"] = result.run_id
    if result.reason is not None:
        body["reason"] = result.reason
    return body


# ── hiding ───────────────────────────────────────────────────────────
#
# Hiding, never deleting (ADR 0237): every provenance table carries a `run_id`
# with no foreign key behind it, and a deletion gate strict enough to protect
# them would refuse almost every row worth removing.
#
# These are `def`, not `async def`, so FastAPI runs them in the threadpool.
# psycopg is synchronous, so an `async def` body would block the event loop for
# the whole round trip -- and with it `/health` and every open SSE stream.


@router.post("/{run_id}/hide", response_model=RunHidden)
def hide_run(run_id: str, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    from venona.pipeline.types import SystemClock

    try:
        return run_service.hide_run(conn, run_id, now=SystemClock().now())
    except LookupError:
        raise HTTPException(404, detail=f"no run {run_id}") from None
    except run_service.RunIsLive:
        raise HTTPException(409, detail=f"run {run_id} is still in progress") from None


@router.post("/{run_id}/unhide", response_model=RunHidden)
def unhide_run(run_id: str, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """The reverse of `hide_run`, and it 404s on the same condition.

    No `RunIsLive` branch: a live run can be unhidden. See `run_service`.
    """
    try:
        return run_service.unhide_run(conn, run_id)
    except LookupError:
        raise HTTPException(404, detail=f"no run {run_id}") from None


@router.post("/hide", response_model=RunHideAll)
def hide_all(conn: db.Conn = Depends(get_db)) -> dict[str, int]:
    """Clear the visible history. A run still in flight is skipped, not refused."""
    from venona.pipeline.types import SystemClock

    return run_service.hide_all(conn, now=SystemClock().now())


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    run = run_service.get_run(conn, run_id)
    if run is None:
        raise HTTPException(404, detail=f"no run {run_id}")
    # The connection is what lets a `reader` fact name a person rather than
    # drop; every other fact resolves without it.
    return decorate(run, conn=conn)


@router.get("/{run_id}/metrics", response_model=RunMetrics)
def run_metrics(run_id: str, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """Where the time went, and whether the work was worth it.

    A separate route from `GET /runs/{id}` rather than more fields on it: the
    run detail is polled by the runs list and rendered on every card, and this
    aggregates four tables including the whole `llm_calls` ledger for the run.
    Making every caller pay for that to render a status chip would be the wrong
    trade.
    """
    run = run_service.get_run(conn, run_id)
    if run is None:
        raise HTTPException(404, detail=f"no run {run_id}")
    return metrics_service.build(conn, run)


@router.get("/{run_id}/events", response_model=RunEventList)
def get_events(
    run_id: str,
    after_seq: int = Query(0, ge=0),
    limit: int = Query(2000, ge=1, le=2000),
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """Plain replay, for clients that would rather poll than stream.

    `has_more` exists because `last_seq` alone cannot tell a poller whether it
    has reached the end of the log or merely the end of a page, and the two
    demand opposite behaviour: wait, or come straight back.
    """
    events = run_service.events_since(conn, run_id, after_seq=after_seq, limit=limit)
    return {
        "events": events,
        "last_seq": events[-1]["seq"] if events else after_seq,
        "has_more": len(events) == limit,
    }


@router.get("/{run_id}/stream")
async def stream(
    run_id: str,
    request: Request,
    after_seq: int = Query(0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE. Replays from the log, then attaches to the live queue.

    `Last-Event-ID` is sent automatically by the browser on reconnect, so a
    dropped connection resumes exactly where it stopped -- the durability of the
    log is what makes that work, not the transport.

    **The one route here that must NOT carry a `response_model`,** and the
    absence is deliberate rather than missed. This returns a `StreamingResponse`;
    a response model would make FastAPI try to validate and serialise the
    generator as a single value, which ends the stream this endpoint exists to
    hold open. `RunEvent` in `schemas/run.py` is the shape of the frames, and
    `/events` serves the same rows as a modelled list for pollers.

    THE ONE ROUTE WHOSE AUTHORIZATION HAS TO OUTLIVE ITS OWN REQUEST.
    `AuthMiddleware` ran once, before this function; the generator below then
    holds for up to forty minutes carrying `model_message` frames. So the
    viewer's id rides into the generator and is re-resolved on a timer there --
    see `run_service.REVOCATION_RECHECK_SECONDS`. The stream simply ENDS when
    the answer changes: synthesising a `done` would tell the client the run
    finished, which is a lie about the run rather than a fact about the reader,
    and a browser `EventSource` does not reconnect through the 403 that its
    retry now earns.
    """
    settings = request.app.state.settings
    principal: Principal | None = getattr(request.state, "principal", None)
    viewer_id = principal.user_id if principal else None
    resume_from = after_seq
    if last_event_id and last_event_id.isdigit():
        resume_from = max(resume_from, int(last_event_id))

    conn = db.connect(settings.database_url)

    async def generator():
        try:
            async for chunk in run_service.stream_events(
                conn, run_id, after_seq=resume_from, viewer_id=viewer_id
            ):
                yield chunk
        finally:
            conn.close()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Without this the Vite dev proxy (and any future reverse proxy)
            # buffers the stream and progress arrives in one lump at the end,
            # which reads as "the pipeline hung".
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/cancel", status_code=202, response_model=RunCancelling)
async def cancel_run(run_id: str) -> dict[str, Any]:
    """Ask the run to stop, and stop it if it does not.

    `async def` because it arms a timer: a sync endpoint is handed to a
    threadpool, where there is no running loop to arm one on. A run that stops
    on its own inside `CANCEL_GRACE_SECONDS` unwinds cleanly and is never
    forced; one that is inside a model call has its task cancelled, which kills
    the subprocess. A second call forces immediately. ADR 0231.
    """
    if not await run_service.registry.cancel(run_id):
        raise HTTPException(404, detail=f"run {run_id} is not active")
    return {"run_id": run_id, "cancelling": True}
