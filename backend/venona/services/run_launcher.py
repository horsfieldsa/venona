"""Contract C11: the one way a run is started from the API process.

Mint the id, write the row, claim the lane, spawn the task, settle a terminal
status, close the connection, release the slot -- one sequence, here, for every
kind. A slot leaked strands every run QUEUED behind it, forever, with no error
anywhere, so once the task exists the release happens in exactly one place:
`_runner`'s `finally`, and every new run type gets it by construction. ADR 0233.

The rule that shape enforces, from `glossary.py`'s own scar:

    Everything after `submit()` is INSIDE the try, including setup.
    `load_lexicon` reads a user-editable YAML file, so a stray tab in it
    raises. When that raise happened outside the try, the task died before
    `finally`, the registry was never released, and every later run answered
    409 "already active" until the server was restarted.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from venona.db import core as db
from venona.llm import budget as budget_mod
from venona.services import run_service, run_types
from venona.services.run_scope import RunScope

log = logging.getLogger("venona.runs")


@dataclass
class LaunchOutcome:
    """What a unit of work reports back. Metadata only -- like a stage, the work
    itself persists to Postgres (contract C7)."""

    #: EXECUTION health, and **None means derive it** from `ctx.degraded` --
    #: which is what almost every unit of work wants and what `_runner` does
    #: with an unset field. Deriving by default is how a new run type gets the
    #: rule without having to remember it; `outcome` is where "the machinery was
    #: fine, but the answer was no" goes. ADR 0183 has the count that forced the
    #: split.
    #:
    #: A site still sets this explicitly to say something the context cannot
    #: know: `ingest` turns a fetch failure into `failed`, and `personal_brief`
    #: does the same for a model that returned nothing usable.
    status: str | None = None
    #: What the work FOUND. One of `run_service.OUTCOMES`, or None for work
    #: that has no answer to report -- which is every failure.
    outcome: str | None = None
    # Merged into the `stage_finished` event. The place a lookup says whether
    # it grounded anything, and what it rejected.
    data: dict[str, Any] = field(default_factory=dict)
    brief_id: str | None = None
    #: Why it failed, for `runs.error` -- the column an operator reads.
    #:
    #: With nowhere else to put it, three callers put the reason in `data`,
    #: which reaches the `stage_finished` event and stops there: a reader's
    #: brief exhausted five structured-output retries and the run listed as
    #: `failed` with an empty error, the one state that tells an operator
    #: nothing at all.
    error: str | None = None


@dataclass
class LaunchContext:
    """What a unit of work is handed. `ctx` is the `RunContext` `_runner` built
    for this run, the pipeline's included -- `execute_run` runs stages through
    it rather than building a second one. ADR 0233."""

    run_id: str
    conn: db.Conn
    ctx: Any
    emit: Callable[[str, str | None, dict[str, Any]], Awaitable[None]]
    # Call between items in a loop. Raises `RunCancelled`, which `_runner` turns
    # into a `cancelled` terminal status.
    check_cancel: Callable[[], None]
    #: The run's lifecycle, for work that opens stages of its own. Work launched
    #: WITH a `stage=` is already inside one and does not need this.
    scope: Any = None


Work = Callable[[LaunchContext], Awaitable[LaunchOutcome]]


class Cancelled(Exception):
    """Raised inside `_runner` when a run was cancelled before it began."""


#: Re-exported so `run_launcher.LaneBusy` still names the refusal every router
#: catches. It is defined in `run_service` because `create_run_guarded` is where
#: the claim becomes atomic, and importing this module from there would close a
#: cycle.
LaneBusy = run_service.LaneBusy


def _actor(request: Any) -> str | None:
    """`api/authz.py::actor_id`, imported HERE rather than at module scope.

    `services/` must not take a module-level dependency on `api/`: the routers
    that call `launch` import this module at startup, so a top-level import back
    would close the cycle. Calling into `authz.py` rather than copying its
    defensive getattr chain is what keeps the two from drifting.
    """
    from venona.api.authz import actor_id

    return actor_id(request)


def host(settings: Any, **state: Any) -> Any:
    """A stand-in for the `Request` `launch` reads its world from.

    THE NON-HTTP ENTRY POINT, so C11's "every run starts through `launch`" has
    no standing exception. `launch` needs three things off `request.app.state`,
    and a process without a web server supplies them just as well.
    """
    from types import SimpleNamespace

    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings, **state)))


async def launch(
    request: Any,
    *,
    kind: str,
    params: dict[str, Any],
    work: Work,
    profile_hash: str | None = None,
    stage: str | None = None,
    migrate: bool = False,
    #: Extra fields for the run's context, for work that needs more than the
    #: default four. The pipeline needs `graph` and `profile`; `make record`
    #: needs its executor and http client built in recording mode.
    context: dict[str, Any] | None = None,
    record: bool = False,
) -> dict[str, Any]:
    """Create a run, put it in its lane, and start it in the background.

    Returns as soon as the row exists -- in milliseconds. The run itself may be
    `running` (a slot was free) or `queued` (its lane was full and it will start
    when one frees). Both are 202 to the caller; the difference is visible in
    the run row and on the Runs page, and the client polls or streams either
    way.

    `stage` wraps `work` in `stage_started` / `stage_finished` and a
    `run_stages` row. Pass it for single-purpose runs; leave it None for
    `execute_run`, which emits its own six stages and would otherwise be
    double-counted.

    EVERY RUN TERMINATES HERE, the pipeline included. A second writer needs a
    second set of rules for when this one stands back, and each of those was a
    way for a run to end unsettled. One writer is the whole point.
    """
    from venona.pipeline.types import SystemClock, SystemIdGen

    settings = request.app.state.settings
    clock, ids = SystemClock(), SystemIdGen()
    run_id = f"run:{ids.uuid7()}"
    lane = run_types.lane_for(kind)

    # An identical request already in flight is handed that run rather than a
    # second one. Two lookups for the same acronym cost twice and write the same
    # row, and the loser's `mark_undefined` can land on top of the winner's
    # result. Under a single global slot this was impossible -- the second was
    # refused -- so it is a hazard the lane split introduces and has to answer.
    #
    # Light lane only. Two research runs are already refused outright, and
    # coalescing them would turn "start a run" into a silent no-op.
    if lane == run_types.LIGHT:
        existing = run_service.registry.duplicate_of(kind, params)
        if existing is not None:
            return {
                "run_id": existing.run_id,
                "status": "running" if existing.admitted.is_set() else "queued",
                "lane": existing.lane,
                "coalesced": True,
            }

    run_conn = db.connect(settings.database_url)
    if migrate:
        db.migrate(run_conn)

    # Submit BEFORE the row is written, so the status the row is created with is
    # the truth rather than a guess that a concurrent submit could invalidate.
    handle = await run_service.registry.submit(run_id, kind, params)
    status = "running" if handle.admitted.is_set() else "queued"

    # `submit` is the atomic point, so it is also where a lane that refuses
    # rather than queues has to say so. The endpoint's own pre-flight runs a few
    # statements earlier, and the gap between the two is a real `await` -- small,
    # but the failure it admits is a second forty-minute pipeline starting by
    # itself some minutes after the caller was told nothing had happened.
    #
    # Research only. The light lane queues on purpose; that is the contract.
    if status == "queued" and lane == run_types.RESEARCH:
        await run_service.registry.release(run_id)
        run_conn.close()
        raise LaneBusy(run_service.registry.active_run_id(lane) or "another run")

    try:
        # `create_run_guarded`, not `create_run`: `submit` above is atomic only
        # within this process, and the CLI is a different one. The lane lock it
        # takes is what makes the claim hold across both.
        run_service.create_run_guarded(
            run_conn,
            lane=lane,
            run_id=run_id,
            kind=kind,
            params=params,
            profile_hash=profile_hash,
            now=clock.now(),
            status=status,
            # Read off the request rather than passed in, because every caller
            # already has one and threading it through would be a chance to
            # forget at every call site. `request.state.principal` is set by
            # `AuthMiddleware`; a scheduler-started run has no request and records
            # NULL, which is the honest answer.
            started_by=_actor(request),
        )
    except Exception:
        # The row is what every later step keys off. If it could not be written
        # there is no run, and holding the slot would strand the lane.
        run_conn.close()
        await run_service.registry.release(run_id)
        raise

    handle.task = asyncio.create_task(
        _runner(
            request,
            run_id=run_id,
            kind=kind,
            handle=handle,
            conn=run_conn,
            work=work,
            stage=stage,
            clock=clock,
            context=context,
            record=record,
        )
    )
    return {"run_id": run_id, "status": status, "lane": lane}


async def _runner(
    request: Any,
    *,
    run_id: str,
    kind: str,
    handle: run_service.RunHandle,
    conn: db.Conn,
    work: Work,
    stage: str | None,
    clock: Any,
    context: dict[str, Any] | None = None,
    record: bool = False,
) -> None:
    from venona.llm.executor import PermissionDenied, build_executor
    from venona.pipeline.types import RunCancelled, build_context
    from venona.sources.http import build_http_client

    settings = request.app.state.settings

    async def emit(type_: str, stage_: str | None, data: dict[str, Any]) -> None:
        """THE OPEN STAGE WINS over whatever the emitter named.

        The job calls helpers that name a stage of their own -- `analyze.py`
        emits under `"analyze"`, true under `orchestrator` and never started
        here -- and the client promotes a stage to `running` on any progress
        event (`stores/runs.ts`), so both segments pulsed at once for the length
        of every sweep. With no stage open the caller's name passes through,
        which `domain_setup` needs.
        """
        event = run_service.append_event(
            conn,
            run_id=run_id,
            type_=type_,
            # Once the stage closes nothing is forced: the counter snapshot and
            # `done` belong to the run, not to a stage that has finished.
            stage=(ctx.stage if ctx is not None else None) or stage_,
            data=data,
            now=clock.now(),
        )
        run_service.registry.publish(run_id, event)

    # Initialised to the PESSIMISTIC value and set to "succeeded" only where the
    # work actually finished. Starting at "succeeded" means any escape path that
    # skips the handlers below records a run that never completed as one that
    # did -- and `asyncio.CancelledError` is exactly such a path, because it is a
    # BaseException and none of these `except` clauses name it. That is the
    # uvicorn shutdown path, so on a container it is the DEPLOY path.
    status: str = "failed"
    # No verdict until `work` reports one. Every escape path below -- cancelled,
    # denied, raised -- leaves it None, which is the honest record: a run that
    # did not finish never reached an answer. Named apart from the
    # `LaunchOutcome` that `work` returns, which shadows `outcome` below.
    outcome_verdict: str | None = None
    error: str | None = "interrupted"
    brief_id: str | None = None
    http: Any = None
    ctx: Any = None
    # Nothing between `submit` and this `try` may raise -- see the module
    # docstring. That includes the waiting itself: a run cancelled while queued
    # has to reach `finally` like any other.
    try:
        await handle.admitted.wait()
        if handle.cancelled:
            raise Cancelled
        run_service.mark_running(conn, run_id=run_id)

        # WHICH SERVICE THIS RUN IS ABOUT TO USE, recorded now rather than when
        # the row was created. A queued light-lane run waits here for as long
        # as the lane is full, and the configured service could have changed in
        # between -- so the row is stamped from the executor actually handed
        # over. A run cancelled before it reaches this line keeps NULL, which
        # is the honest answer for work that never chose one.
        executor = executor_for(request, handle.lane)
        # NOT FOR A KIND THAT CALLS NO MODEL. `uses_model=False` means exactly
        # that, and stamping the service anyway makes the run page say a run was
        # "thought by Claude Code" when it made no call at all -- provenance
        # invented rather than recorded, on the one column that exists to say
        # who did the thinking. NULL is the honest answer here for the same
        # reason it is for a run cancelled before it got this far.
        if run_types.type_for(handle.kind).uses_model:
            _stamp_service(conn, run_id, executor, settings)

        # EVERY RUN GETS ITS CONTEXT HERE, the pipeline's included: a second one
        # would be a second set of counters, a second budget and a second answer
        # to which stage is open.
        #
        # `record` builds both in capturing mode. `make record` captures model
        # envelopes and pages in one pass, and omitting it here is how
        # the HTTP half went uncaptured for a while.
        http = build_http_client(settings, record=record)
        if record:
            executor = build_executor(settings, record=True)
        ctx = build_context(
            run_id=run_id,
            db=conn,
            llm=executor,
            settings=settings,
            http=http,
            emit=emit,
            **(context or {}),
        )
        # A CEILING ON EVERY RUN. Five kinds built no `Budget` at all, so
        # `max_calls_per_run` bound nothing for them (ADR 0233). A closure that
        # needs a wider one replaces `ctx.budget`, as the sweeps do.
        ctx.budget = budget_mod.Budget(settings=settings.llm)
        # THE CONTEXT ASKS THE REGISTRY ITSELF, on every check. The work
        # closure hands `ctx` to the same helpers the orchestrator uses, and
        # `analyze.py` checks per cluster; without this a cancel waited for
        # whatever boundary the closure happened to check, and the
        # second-source work checks none, so its cancel was always the 5s
        # force -- mid-call rather than between items.
        ctx._is_cancelled = lambda: bool(handle.cancelled)
        # WHAT A PAUSE LOOKS LIKE FROM OUTSIDE, without which half an hour of
        # waiting renders identically to half an hour of working. ON THE
        # CONTEXT, not the executor: that is one object per LANE, and the light
        # lane holds three runs.
        ctx.wait_abort = lambda: bool(handle.cancelled)

        async def on_wait(retry_in: float | None) -> None:
            # NO STAGE NAMED: this runs inside a model call, which does not know
            # which stage asked for it.
            await emit(
                "stage_progress",
                None,
                {
                    "waiting": retry_in is not None,
                    "retry_in": round(retry_in) if retry_in is not None else None,
                },
            )

        ctx.on_wait = on_wait

        # ONE LIFECYCLE. `stage=` is sugar for "wrap the closure in this one
        # stage", opened and closed exactly as the orchestrator's six are. A
        # launch with no `stage` opens its own, as the setup wizard does.
        scope = RunScope(run_id=run_id, conn=conn, ctx=ctx, emit=emit) if ctx is not None else None

        def check_cancel() -> None:
            """The work closure's own way to ask, for one that holds no context.

            `ctx._is_cancelled` above is what makes a check anywhere INSIDE the
            pipeline work; this is for a `work` that wants to check between its
            own items. Both read the registry's flag, which is what the cancel
            endpoint sets, so they cannot disagree.
            """
            if handle.cancelled and ctx is not None:
                ctx.cancel()
            if ctx is not None:
                ctx.check_cancel()

        target = LaunchContext(
            run_id=run_id, conn=conn, ctx=ctx, emit=emit, check_cancel=check_cancel, scope=scope
        )
        if stage and scope is not None:
            async with scope.stage(stage) as report:
                outcome = await work(target)
                report.counts = outcome.data
        else:
            outcome = await work(target)
        # Resolved here, not at the end of the block: the pessimistic
        # initialisation above must survive right up to the point where `work`
        # has actually returned, which is the only evidence that anything
        # finished.
        #
        # `outcome.status or ...` is the derive-by-default rule -- work that
        # says nothing about execution gets the one answer the context can
        # give, which is whether anything called `ctx.warn`. See
        # `LaunchOutcome`.
        status = outcome.status or (
            "succeeded_partial" if ctx is not None and ctx.degraded else "succeeded"
        )
        outcome_verdict, error = outcome.outcome, outcome.error
        brief_id = outcome.brief_id

        conn.commit()

    except Cancelled:
        status, error = "cancelled", None
    except RunCancelled:
        status, error = "cancelled", None
    except asyncio.CancelledError:
        # A BaseException, so it reaches neither `except Exception` below nor
        # any of the domain handlers above. Two things arrive through this door
        # and they are not the same event: uvicorn shutting down -- which on a
        # container is every deploy -- and a PERSON, whose cancel was not
        # noticed within `CANCEL_GRACE_SECONDS` and whose run was stopped for
        # it. `handle.cancelled` is what tells them apart, and a run stopped on
        # purpose has no error to report.
        #
        # Re-raised, because swallowing a cancellation lies to the loop about
        # whether this task stopped. The `finally` still runs first, which is
        # the whole point.
        status = "cancelled"
        error = None if handle.cancelled else "interrupted by shutdown"
        raise
    except PermissionDenied as exc:
        # Contract C2. A denied tool is never degraded to "found nothing" --
        # that reads as a clean empty result and stays broken forever.
        status, error = "failed", str(exc)
        await emit("error", stage, {"message": error})
    except Exception as exc:  # noqa: BLE001 - a run must always end cleanly
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        await emit("error", stage, {"message": error})
        # `error`, not the `interrupted` that `_settle_open_stages` would write:
        # this work raised and was caught, which is a different fact from a
        # process that died holding the row.
        if scope is not None:
            await scope.settle_failed(error)
    finally:
        # Two nested try/finally, because closing the connection and freeing the
        # slot must happen even if writing the terminal row fails. The slot is
        # the one that matters: a leaked one is silent and permanent.
        #
        # Bound HERE rather than beside the raise below, because the `finally`
        # reads it: a `finish_run` that throws would otherwise reach that read
        # with the name unbound and turn a failed write into a `NameError` on
        # the way out.
        queued = 0
        try:
            # The row FIRST, and synchronously. Under cancellation every
            # `await` in this block raises immediately, so an emit placed
            # before the write would leave the run `running` forever.
            run_service.finish_run(
                conn,
                run_id=run_id,
                status=status,
                counters=ctx.counters.as_dict() if ctx else {},
                warnings=ctx.warnings if ctx else [],
                brief_id=brief_id,
                cost_usd=ctx.counters.cost_usd if ctx else 0.0,
                error=error,
                now=clock.now(),
                outcome=outcome_verdict,
            )
            try:
                await emit(
                    "done",
                    None,
                    # ALL SIX KEYS, every time. Three of them were missing for a
                    # light run, so its final counters and cost never reached
                    # the stream and a run you watched disagreed with the same
                    # run reloaded. Additive under C6.
                    {
                        "status": status,
                        "outcome": outcome_verdict,
                        "brief_id": brief_id,
                        "counters": ctx.counters.as_dict() if ctx is not None else {},
                        # Sentences, not levels: the levels live on the row.
                        "warnings": [w.text for w in ctx.warnings[:20]] if ctx is not None else [],
                        "cost_usd": (ctx.counters.cost_usd if ctx is not None else 0.0),
                    },
                )
            except BaseException:  # noqa: BLE001
                # Best effort. A cancelled task raises on the first await,
                # so this is the expected outcome during shutdown -- and
                # `_tail_log` already synthesises a terminal event for a
                # client watching a run whose owner is gone, which is
                # exactly this case.
                pass
            if hasattr(http, "aclose"):
                try:
                    await http.aclose()
                except BaseException:  # noqa: BLE001 - same reasoning
                    pass
            # THE OUTWARD REPORT, and this is the only point EVERY terminal
            # run passes through -- which is the whole reason it is here rather
            # than beside each call to `launch`. A report every caller has to
            # remember is a report most callers do not make.
            #
            # `notify.raise_run_notice` decides whether this kind has anything
            # to say; this block only owes it an honest status.
            try:
                from venona import notify

                # The status in hand, not the row read back. Reading it back
                # was how one report covered a run that had settled itself; with
                # one writer there is nothing to read back FROM.
                queued = notify.raise_run_notice(
                    conn, run_id=run_id, kind=kind, status=status, error=error
                )
            except BaseException:  # noqa: BLE001 - a notifier must never fail a run
                log.warning("could not raise the notice for run %s", run_id, exc_info=True)
        finally:
            conn.close()
            await run_service.registry.release(run_id)
            # SENT AFTER THE SLOT IS FREED. This POSTs to whatever the operator
            # pointed Venona at, and holding the research slot open for a
            # network round trip would delay the next run for the sake of a
            # message about the last one. Skipped entirely when nothing was
            # queued, which is every run on an instance with no destinations.
            if queued:
                try:
                    from venona import scheduler

                    await scheduler.deliver_now(request.app)
                except BaseException:  # noqa: BLE001 - delivery never fails a run
                    log.warning("could not deliver the notice for run %s", run_id, exc_info=True)


def _stamp_service(conn: Any, run_id: str, executor: Any, settings: Any) -> None:
    """Record the service on the run, tolerating an executor that has none.

    Reached by `getattr` because the replay executor and every test stub
    satisfy `LlmExecutor` without being a subprocess -- and a run under replay
    genuinely did not use a service, so NULL is what it should say.
    """
    from venona.db.repositories import harness as harness_repo

    harness = getattr(executor, "harness", None)
    if harness is None:
        return
    with contextlib.suppress(Exception):
        harness_repo.stamp_run(
            conn,
            run_id,
            harness=harness.key,
            billing_mode=settings.llm.billing_mode,
        )


def executor_for(request: Any, lane: str) -> Any:
    """The model executor a lane's runs call through.

    Two instances of the same class, differing in how many `claude` subprocesses
    each will hold open at once and in whether it waits out a provider outage.
    They share the ledger, the budget accounting and offline replay, so contract
    C2 is untouched and there is still exactly one place the binary is invoked.

    The separation exists because admitting a lookup instantly and then parking
    it behind `analyze` on a two-slot semaphore is indistinguishable, from the
    hover card, from not admitting it at all. Reserved headroom is the point:
    the light lane's calls are seconds long and a person is watching a spinner
    for every one of them.
    """
    if lane == run_types.LIGHT:
        return getattr(request.app.state, "llm_light", None) or request.app.state.llm
    return request.app.state.llm


def lane_harness(request: Any, lane: str) -> str:
    """The service that will actually run the work, not the one configured.

    THE EXECUTOR, NOT THE SETTINGS DOCUMENT. `llm.harness` is editable, and a
    save while a run is in flight defers the rebuild and reports
    `restart_required` -- so until the process restarts, the runs being stamped
    carry whichever service the executor still holds while the document names
    another. Pricing from the document in that window reads a history with
    nothing in it, and a modal falls back to a constant without saying why.

    Here rather than in a router because two of them quote a measured price
    (`routers/runs.py`, `routers/glossary.py`) off the same argument.
    """
    with contextlib.suppress(Exception):
        return str(executor_for(request, lane).harness.key)
    return str(request.app.state.settings.llm.harness)
