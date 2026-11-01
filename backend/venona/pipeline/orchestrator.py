"""The full run: fetch → analyze → brief, with progress and cancellation.

Two behaviours matter more than the stage list:

  * **A partially useful run is `succeeded_partial`, never `failed`.** If the
    budget runs out or two feeds 403, the run still persists what it gathered
    and still writes a brief. Reporting that as a failure teaches you to ignore
    the status field, which is worse than any single bad run.
  * **And a run that simply found nothing is not degraded either.** `status`
    says whether the machinery worked; `outcome` says what it found. A quiet
    day with no new stories is `succeeded` + `nothing_to_do`, not
    `succeeded_partial` -- the same reasoning, one field over. ADR 0183.
  * **Every event is durable before it is published.** The SSE stream is a view
    over the log, so a client that reconnects mid-run misses nothing.
"""

from __future__ import annotations

import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from venona.db import core as db
from venona.db.repositories import pipeline_orchestrator as repo
from venona.llm.budget import Budget
from venona.pipeline.acronyms import run_acronyms
from venona.pipeline.analyze import run_analyze
from venona.pipeline.brief import run_brief
from venona.pipeline.define import run_define
from venona.pipeline.fetch import run_fetch
from venona.pipeline.types import (
    RunCancelled,
    RunMessage,
    with_overrides,
)
from venona.services import run_types
from venona.settings import Settings

# The authoritative stage list. `--only` validates against this.
STAGES: tuple[str, ...] = ("fetch", "analyze", "acronyms", "brief", "define", "curate")


@dataclass
class RunOutcome:
    run_id: str
    status: str
    #: What the run FOUND, as opposed to whether it ran. For a pipeline run the
    #: artifact is the brief, so this is `delivered` when one was written and
    #: `nothing_to_do` when the window held nothing new. A quiet day is a
    #: correct night's work, and this is the field that lets it say so without
    #: claiming a degradation. ADR 0183.
    outcome: str | None = None
    counters: dict[str, Any] = field(default_factory=dict)
    warnings: list[RunMessage] = field(default_factory=list)
    brief_id: str | None = None
    cost_usd: float = 0.0
    error: str | None = None


async def execute_run(
    *,
    run_id: str,
    settings: Settings,
    conn: db.Conn,
    graph,
    kind: str = "on_demand",
    limit_per_source: int | None = None,
    analyze_limit: int | None = None,
    skip_brief: bool = False,
    only: Sequence[str] | None = None,
    record: bool = False,
    rewrite_of: str | None = None,
    llm: Any = None,
    lookback_hours: int | None = None,
    discovery_max_age_hours: int | None = None,
    max_calls: int | None = None,
    adjudicate_limit: int | None = None,
    scope: Any = None,
) -> RunOutcome:
    """Run the pipeline.

    `llm` is the executor the run calls through -- the API and the scheduler
    pass `app.state.llm`, the research lane's long-lived instance. A run that
    built its own had its own circuit breaker, so `/api/health` reported a
    closed circuit on the executor nobody was using while the nightly run's
    calls failed on one nobody could see. Absent (the CLI, the offline suite)
    or when recording, a fresh executor is built as before.

    `only` restricts execution to a subset of STAGES. This works because every
    stage reads its inputs from Postgres by STATE, not by run id -- `analyze`
    looks for `analyzed_at IS NULL`, `brief` looks for unbriefed clusters in the
    window. So `--only brief` re-briefs from what is already there without
    re-fetching, which turns an eight-minute tuning loop into seconds.

    (There is deliberately no `--from RUN_ID`: since stages key on state rather
    than run, filtering by run would need new predicates everywhere for no gain.)

    `rewrite_of` is the ONE exception to "stages key on state, never on an id",
    and it is a brief id rather than a run id for that reason. It exists because
    keying on state alone made `--only brief` unable to re-brief: "unbriefed
    clusters in the window" is empty the moment a brief has claimed them, so the
    stage that `only` was built for was the one it could not run twice. It names
    the brief whose claim to release and nothing else -- see `build_retrieval`.

    `lookback_hours`, `discovery_max_age_hours` and `max_calls` are this run's
    own ceilings, applied as a COPY of the settings rather than by assignment --
    see `with_overrides`. A widened `lookback_hours` is refused by
    `retrieval_window` unless this run skips the brief; ADR 0228 has the
    argument.
    """
    # THE SCOPE IS THE RUN. It carries the context the launcher built -- one set
    # of counters, one budget, one answer to which stage is open -- and the
    # emitter that writes the event log. This function runs stages; it does not
    # start or settle a run.
    ctx, emit = scope.ctx, scope.emit
    wanted = set(only) if only else set(STAGES)
    # THE KIND HAS THE LAST WORD, and it has to. `only=None` means "every stage",
    # which includes the brief -- so a deep read that was told to go looking, and
    # therefore names no stage subset, would have written one. Nothing downstream
    # would have said so: the API's own gate reads the same table this does, so
    # the brief would have been written, never announced, and its claim on the
    # window's clusters would have made the NEXT morning's brief skip them.
    declares_brief = "brief" in run_types.type_for(kind).stages
    writes_brief = declares_brief and "brief" in wanted and not skip_brief
    # Whether analyze has a date bound at all. A run that writes a brief always
    # does, and so does one that was handed an explicit window. A reading run
    # that named none is asking for everything waiting, which is the default the
    # screen offers and the whole point of the kind.
    bounded = writes_brief or lookback_hours is not None
    # A READING RUN MAY WORK THE WHOLE QUEUE. `adjudicate_limit_per_run` caps
    # ROWS, not calls, and it is sized for a daily -- so after a deep read files
    # two hundred conflicts at once, the queue drains sixty a day while a person
    # is asked about the rest. Lifting it is safe because the row cap was never
    # the real bound: the run's budget is, and `adjudicate` already checks it
    # per batch and defers the remainder with a warning a reader can see.
    if not writes_brief and adjudicate_limit is None:
        adjudicate_limit = max_calls or settings.llm.max_calls_per_run
    settings = with_overrides(
        settings,
        lookback_hours=lookback_hours,
        discovery_max_age_hours=discovery_max_age_hours,
        max_calls=max_calls,
        adjudicate_limit=adjudicate_limit,
    )
    # The overridden settings reach the stages through the context they already
    # hold, so a stage reading `ctx.settings` sees the same window the run was
    # asked for rather than the instance default.
    ctx.settings = settings
    budget = ctx.budget or Budget(settings=settings.llm)

    outcome = RunOutcome(run_id=run_id, status="running")
    brief_id: str | None = None
    #: What a run that writes no brief has to show for itself. Initialised here
    #: rather than read off the stage report at the end, because the analyze
    #: stage may not have run at all and an unbound local is not an outcome.
    stories_read = 0
    learned = 0
    #: What a pass over the curation queue read and what it managed to settle.
    #: Initialised here for the reason above: the stage may not have run.
    questions_read = 0
    settled = 0
    still_open = 0
    #: The brief stage ran and found the window empty, as opposed to failing.
    brief_empty = False
    # Stages whose work is already committed to Postgres. If something blows up
    # after any of these, the run is `succeeded_partial` -- see the handler.
    completed: list[str] = []

    async def check_cancel() -> None:
        ctx.check_cancel()

    stage = scope.stage
    completed = scope.completed

    # First-run anchor load. The trunk in `profile/anchors.yaml` reaches the
    # database only through an explicit Apply, which a new instance's operator
    # has not necessarily pressed before the first (possibly scheduled) run
    # fires -- and a run without its anchors resolves every entity cold. An
    # empty anchor set plus a non-empty file means "never loaded", so load it
    # once; the run's own persist stage projects it. Explicit Apply stays the
    # only path that RELOADS: this never touches an anchor set that exists.
    #
    # KEYED ON ANALYZE, not on `only is None`. Analyze is the stage that
    # resolves surfaces into entities, so it is the one that mints duplicates of
    # the whole trunk when the anchors are not there -- and a run naming its
    # stages needs the trunk exactly as much as a run naming none. The `only is
    # None` gate meant a first run that skipped any stage resolved cold, which
    # is the case a deep read is: it names its stages to leave fetch out.
    if "analyze" in wanted:
        try:
            from venona.db.repositories import seed as seed_repo
            from venona.services.seed import ANCHOR_RUN_ID, load_anchors

            if seed_repo.count_assertions_by_run_id(conn, ANCHOR_RUN_ID) == 0:
                counts = load_anchors(conn, settings.profile_dir, quiet=True)
                if counts.get("entities"):
                    await emit(
                        "log",
                        None,
                        {"message": f"anchors auto-loaded on first run: {counts}"},
                    )
        except FileNotFoundError:
            pass  # no anchors file at all; nothing to load
        except Exception as exc:  # noqa: BLE001 - a bad anchor file must not kill the run
            ctx.warn(f"anchors not auto-loaded: {type(exc).__name__}: {exc}")
            await emit("warning", None, {"message": ctx.warnings[-1].text})

    try:
        if "fetch" in wanted:
            async with stage("fetch") as report:
                fetch = await run_fetch(ctx, limit_per_source=limit_per_source, budget=budget)
                report.counts = fetch.as_dict()

        if "analyze" in wanted:
            async with stage("analyze") as report:
                analyze = await run_analyze(
                    ctx,
                    limit=analyze_limit,
                    budget=budget,
                    writes_brief=writes_brief,
                    bounded=bounded,
                )
                # `analyze` is where promotion actually happens -- it calls
                # `promote_provisionals` as documents land, so by the time
                # `curate` re-runs it idempotently there is usually nothing left
                # to do. Only curate's number was ever reported, which is why run
                # `06a80ae3` showed `entities_promoted: 0` while 47 entities
                # crossed the corroboration gate. `ctx.counter` ADDS, so both
                # stages contribute and every `--only` subset still reports what
                # it did.
                await ctx.counter("entities_promoted", analyze.promoted)
                report.counts = analyze.as_dict()
                stories_read = analyze.clusters_analyzed
                learned = analyze.entities_minted + analyze.relations_written

        if "acronyms" in wanted:
            # After analyze: grounding runs over the documents that were actually
            # analysed, and Tier 2 can reuse anything analyze already resolved.
            async with stage("acronyms") as report:
                acronyms = await run_acronyms(ctx, budget=budget)
                report.counts = acronyms.as_dict()

        if writes_brief:
            async with stage("brief") as report:
                brief = await run_brief(ctx, budget=budget, rewrite_of=rewrite_of)
                brief_id = brief.brief_id
                brief_empty = brief.empty_reason is not None
                if brief.error:
                    ctx.warn(f"brief: {brief.error}")
                elif brief.empty_reason:
                    # NOT a degradation: the run read the window and there was
                    # nothing in it to write about. `error` and `empty_reason`
                    # are separate fields precisely so this branch exists --
                    # one field for both facts makes every quiet night report
                    # `degraded`, against what `docs/RUNBOOK.md` documents.
                    ctx.note(brief.empty_reason)
                # A brief stage that wrote no brief is NOT committed work:
                # `--only brief` re-running after it would find the same
                # clusters unclaimed. It is only an `error` row when a brief was
                # OWED -- a stage flagged red with a null `error` is what a
                # quiet night rendered as while `empty_reason` lived in `error`.
                report.status = "ok" if (brief.brief_id or brief.empty_reason) else "error"
                report.error = brief.error
                report.committed = bool(brief_id)
                report.counts = {
                    "clusters_used": brief.clusters_used,
                    "blocks": brief.blocks,
                    "spans": brief.spans,
                    "quiet_day": int(brief.quiet_day),
                }
                report.payload = {
                    "brief_id": brief_id,
                    "blocks": brief.blocks,
                    "spans": brief.spans,
                    "quiet_day": brief.quiet_day,
                    "error": brief.error,
                    "empty_reason": brief.empty_reason,
                }

        # AFTER brief, and that ordering is the whole scope decision: this is
        # the first point at which we know which entities the brief actually
        # references, and those are the only ones a reader can hover. Defining
        # all 1,600 on a schedule would spend on entities nobody can open.
        if "define" in wanted:
            async with stage("define") as report:
                define = await run_define(ctx, budget=budget)
                for warning in define.warnings:
                    ctx.warn(warning)
                report.counts = define.as_dict()

        # Last, and before the projection, so a promotion made here reaches the
        # graph in the same run. Zero model calls: everything is a query.
        if "curate" in wanted:
            from venona.pipeline.curate import run_curate

            async with stage("curate") as report:
                curate = await run_curate(ctx, budget=budget)
                await ctx.counter("entities_promoted", curate.promoted)
                await ctx.counter("curation_open", curate.conflicts_open)
                report.counts = curate.as_dict()
                questions_read = curate.suggested
                # EVERY WAY A ROW LEAVES THE QUEUE, and a relation is one of
                # them. Left out, a run that wrote four edges and settled four
                # conflicts reported `nothing waiting` -- a run under-reporting
                # its own work, which teaches a reader that the chip means
                # nothing.
                settled = (
                    curate.merged
                    + curate.merged_by_model
                    + curate.merged_by_declared_type
                    + curate.kept_apart_by_model
                    + curate.related_by_model
                )
                still_open = curate.conflicts_open

        # ── project ──────────────────────────────────────────────────
        # Isolated on purpose. Neo4j is a REBUILDABLE PROJECTION (invariant 1),
        # so a projection failure costs a `venona graph rebuild`, not the run's
        # work. Letting it unwind into the handler below turned a down Neo4j
        # into a `failed` run that had already written its brief.
        if graph is not None:
            from venona.graph.projector import project

            try:
                counts = await project(conn, graph.driver)
                await emit("stage_finished", "persist", counts)
                await ctx.counter("edges_written", counts.get("edges", 0))
            except RunCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - the graph is rebuildable
                # No command in the message: this renders on a run page, and
                # the reader is not at a shell. The record is intact either
                # way -- Postgres holds it and the view is rebuildable.
                ctx.warn(
                    "The relationship view could not be updated, so it is behind "
                    f"today's reading. Nothing was lost. ({exc})"
                )
                await emit("warning", "persist", {"message": ctx.warnings[-1].text})

        # A partially useful run is NOT a failure -- and a run that did its
        # whole job is NOT partial. `ctx.degraded`, not `ctx.warnings`: routine
        # notices (the alias-conflict backlog, the ontology's relation
        # rejections) are surfaced without downgrading the status. See
        # `RunContext.note`.
        outcome.status = "succeeded_partial" if ctx.degraded else "succeeded"
        # And separately, what it FOUND. The brief is the artifact, so a run
        # that wrote one delivered; one that reached the end of a quiet window
        # with nothing new to say did its whole job and had nothing to do.
        #
        # `--only fetch` reaches here and has no answer to report: nothing was
        # asked for, so nothing is claimed.
        #
        # A run that reads for the GRAPH rather than for a brief does have one,
        # and it is a different question: not "was there anything worth writing
        # about" but "was there anything left to read". Claimed only when
        # analyze actually ran, so `--only fetch` still says nothing.
        #
        # KEYED ON THE KIND'S DECLARED STAGES, not on `writes_brief`. A kind
        # that declares a brief and was told to skip it this once is an ad hoc
        # run of the reading, and its `outcomes` table spells `delivered` as
        # "brief written" -- which would be a lie about a run that wrote none.
        # Only a kind that never writes one may claim this.
        if not declares_brief and "analyze" in wanted:
            if not stories_read:
                outcome.outcome = "nothing_to_do"
            elif learned:
                outcome.outcome = "delivered"
            else:
                outcome.outcome = "nothing_found"
        # A run whose whole job is the queue answers a different question again:
        # not what it read, but how much of what was waiting it took off a
        # person. `some` is the honest middle -- it settled several and left
        # several, which is what a worked queue looks like most days.
        elif not declares_brief and "curate" in wanted:
            if not questions_read and not settled:
                outcome.outcome = "nothing_to_do"
            elif not settled:
                outcome.outcome = "nothing_found"
            elif still_open:
                outcome.outcome = "some"
            else:
                outcome.outcome = "delivered"
        elif writes_brief:
            # `nothing_to_do` says the window held nothing. A brief that was
            # OWED and failed reached no answer at all, which ADR 0183 spells
            # as a null outcome -- reporting it as `nothing_to_do` told the
            # quiet-nights alarm a failure was a quiet night, and told
            # `outage_retry_due` not to bother retrying it.
            if brief_id:
                outcome.outcome = "delivered"
            elif brief_empty:
                outcome.outcome = "nothing_to_do"

    except RunCancelled:
        outcome.status = "cancelled"
        await emit("warning", None, {"message": "cancelled by user"})

    except Exception as exc:  # noqa: BLE001 - a run must always end cleanly
        outcome.error = f"{type(exc).__name__}: {exc}"
        # `stage` is whatever we were in when it blew up -- the most useful
        # single field for diagnosing a failed run.
        failed_stage = repo.select_runs_by_id(conn, run_id).fetchone()
        stage_name = failed_stage["stage"] if failed_stage else None

        # An exception is not automatically a failed run. If earlier stages
        # already committed their work -- 40 documents fetched, 30 clusters
        # analysed, a brief written -- then reporting `failed` hides all of it
        # and teaches you to ignore the status field. That is what
        # `succeeded_partial` is for; only a run that persisted nothing failed.
        outcome.status = "succeeded_partial" if completed else "failed"
        # An exception mid-run means the answer is whatever was on the floor
        # when it hit: a brief already written still delivered, and a run that
        # blew up before reaching one has no answer at all rather than a
        # negative one.
        outcome.outcome = "delivered" if brief_id else None
        ctx.warn(f"{stage_name or 'run'}: {outcome.error}")

        await emit(
            "error",
            stage_name,
            {"message": outcome.error, "traceback": traceback.format_exc()[-2000:]},
        )

    # NO TERMINAL ROW AND NO `done` HERE: `run_launcher` writes both, for every
    # run there is, from the outcome below.
    outcome.counters = ctx.counters.as_dict()
    outcome.warnings = ctx.warnings
    outcome.brief_id = brief_id
    outcome.cost_usd = ctx.counters.cost_usd

    return outcome
