from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from venona.api.deps import Window, get_db, page_window
from venona.api.schemas.definitions import (
    DefineStarted,
    EntitySearchResults,
)
from venona.api.schemas.entity import (
    Card,
    CardBatchRequest,
    CardBatchResponse,
    EntityConnections,
    EntityRecord,
    EntitySources,
    Neighborhood,
)
from venona.api.schemas.run import RunPreview, RunStarted
from venona.db import core as db
from venona.db.repositories import curation as repo
from venona.db.repositories import pipeline_define as repo_define
from venona.pipeline.confidence import ENTITY_CONFIDENCE
from venona.services import entity_service, run_launcher

router = APIRouter(tags=["entities"])


@router.post("/entities/cards:batch", response_model=CardBatchResponse)
def cards_batch(payload: CardBatchRequest, conn: db.Conn = Depends(get_db)) -> CardBatchResponse:
    """One round trip for every reference in a brief.

    The reader calls this once on mount; every subsequent hover is a client-side
    Map lookup.
    """
    cards, missing = entity_service.cards_batch(conn, payload.ids)
    return CardBatchResponse(cards=cards, missing=missing)


@router.get("/entities/{entity_id}/card", response_model=Card)
def get_card(entity_id: str, conn: db.Conn = Depends(get_db)) -> Card:
    card = (
        entity_service.glossary_card(conn, entity_id)
        if entity_id.startswith("gloss:")
        else entity_service.entity_card(conn, entity_id)
    )
    if card is None:
        raise HTTPException(404, detail=f"no name {entity_id}")
    return card


@router.get("/entities/{entity_id}/neighbors", response_model=Neighborhood)
def neighbors(
    entity_id: str,
    limit: int = Query(25, ge=1, le=50),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    conn: db.Conn = Depends(get_db),
) -> Neighborhood:
    return entity_service.neighborhood(conn, entity_id, limit=limit, min_confidence=min_confidence)


@router.get("/entities/{entity_id}/connections", response_model=EntityConnections)
def connections(
    entity_id: str,
    predicate: str | None = Query(None, description="narrow to one kind of connection"),
    window: Window = Depends(page_window(25, 50)),
    conn: db.Conn = Depends(get_db),
) -> EntityConnections:
    """Everything this name is connected to, one page at a time.

    A CEILING on the offset rather than an unbounded window, and it is not
    arbitrary. The traversal behind a page is the whole neighbourhood, so page
    twenty costs what page one costs; past a couple of hundred rows nobody is
    reading, they are walking. `predicate` is the answer to wanting more.
    """
    if window.offset > entity_service.CONNECTION_OFFSET_CEILING:
        raise HTTPException(
            409,
            detail=(
                "That is further than this list goes. Narrow it to one kind of connection instead."
            ),
        )
    return entity_service.connections(
        conn, entity_id, limit=window.limit, offset=window.offset, predicate=predicate
    )


@router.get("/entities/{entity_id}/sources", response_model=EntitySources)
def sources(
    entity_id: str,
    window: Window = Depends(page_window(20, 100)),
    conn: db.Conn = Depends(get_db),
) -> EntitySources:
    """Every article this name appeared in, newest first."""
    return entity_service.sources(conn, entity_id, limit=window.limit, offset=window.offset)


@router.get("/entities/{entity_id}/record", response_model=EntityRecord)
def record(entity_id: str, conn: db.Conn = Depends(get_db)) -> EntityRecord:
    """Where this name came from. Admin, by not being a reader route (C12).

    A reader is told what Venona knows. Whoever runs the domain also needs to
    see why it knows it, which is the only question that can be answered when
    two names turn out to have been one thing.
    """
    result = entity_service.record(conn, entity_id)
    if result is None:
        raise HTTPException(404, detail=f"no name {entity_id}")
    return result


@router.get(
    "/search",
    response_model=EntitySearchResults,
    # `exclude_unset`, and it is load-bearing rather than tidiness.
    #
    # `entity_service.search` POPS `status` when provisionals were not asked
    # for -- its docstring calls that "unchanged shape for every existing
    # caller". A response model with `status: str | None = None` puts it back as
    # an explicit null, which is a new key on a response that had none. Excluding
    # unset fields makes the model describe both shapes without inventing the
    # field for the callers the service deliberately hides it from.
    response_model_exclude_unset=True,
)
def search(
    q: str = Query(min_length=1),
    limit: int = Query(10, ge=1, le=50),
    include_provisional: bool = Query(False),
    conn: db.Conn = Depends(get_db),
) -> dict:
    return {
        "entities": entity_service.search(
            conn, q, limit=limit, include_provisional=include_provisional
        )
    }


@router.post(
    "/entities/definitions/refresh",
    status_code=202,
    response_model=RunStarted,
    # `coalesced` is set only when an identical light-lane run was already
    # in flight; without this it is `false` on every other launch.
    response_model_exclude_unset=True,
)
async def refresh_definitions(
    # CEILING RAISED FROM 50, which was below the size of the queue it exists to
    # work through -- 605 names on this instance were undefined and reachable by
    # hovering, so "define all" could not be ASKED for, whatever the caller was
    # willing to spend. The default is untouched and still comes from
    # `definitions_refresh_cap`: what changes is that a caller who has been
    # quoted a price can now name a number that finishes the job.
    request: Request,
    limit: int | None = Query(None, ge=1, le=5000),
) -> dict[str, Any]:
    """Ground everything the latest brief still leaves undefined -- tier 3, in bulk.

    The light-lane counterpart to `define`. That stage runs inside the pipeline
    and does tiers 1 and 2 only: quote a sentence we already hold, else write
    one from sentences we hold. Tier 3 costs a search and page fetches per
    entity, so it has always been on-demand and one-at-a-time, through the hover
    card. This is the same work over the queue that card leaves behind, without
    a person clicking each row.

    Reuses `define.targets`, which already answers exactly the right question --
    entities the latest brief references, undefined, ranked by relevance -- and
    already falls back to the latest brief when the calling run wrote none.
    """
    from venona.pipeline.define import (
        ground_in_corpus_quote,
        lookup_web,
        mark_entity_undefined,
        store_entity_definition,
    )
    from venona.pipeline.define import targets as define_targets
    from venona.pipeline.types import resolve_cap

    settings = request.app.state.settings
    cap = resolve_cap(limit, settings.pipeline.definitions_refresh_cap)

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        rows = define_targets(
            target.conn, target.run_id, min_salience=settings.pipeline.define_min_salience
        )
        if cap is not None:
            rows = rows[:cap]

        grounded = 0
        rejected = 0
        for index, row in enumerate(rows):
            # Between items, so a cancel from the Runs page lands within one
            # lookup rather than after the whole queue. `target.check_cancel`,
            # not `ctx.check_cancel` -- the latter only fires if something has
            # already bridged the registry's flag into the context, which is
            # exactly what the launcher's version does.
            target.check_cancel()
            await target.emit(
                "stage_progress",
                "lookup",
                # `current`, not `done`. The UI reads `current`/`total` and
                # nothing else (`LiveRunPanel.vue`, `RunsView.vue`), so a
                # correctly-emitted per-item counter under the wrong key rendered
                # `0/N` for the whole lookup -- the one shape of bug that looks
                # like the work is stuck rather than like a typo.
                {"entity_id": row["id"], "name": row["name"], "current": index, "total": len(rows)},
            )
            # THE CORPUS BEFORE THE WEB, the same order the hover card's own
            # button uses. Measured on `run:06aa9646` (2026-09-15), which had
            # this the other way round: of ten names it paid tier 3 for, three
            # were quotable for free from sentences already held, and one of
            # those -- `Bombardier Global 6500 aircraft`, whose article says it
            # `is the chosen platform for next-generation aerial deep sensing`
            # -- the web could not answer at all, so it was reported unexplained
            # while the answer sat in the corpus. Tier 1 also OUTRANKS anything
            # tier 3 returns, so doing it second would store the worse of two
            # definitions we already had.
            if ground_in_corpus_quote(
                target.conn, entity_id=row["id"], name=row["name"], now=target.ctx.clock.now()
            ):
                await target.ctx.counter("entities_defined")
                grounded += 1
                continue

            result = await lookup_web(target.ctx, row["id"], row["name"], trace=[])
            if result is None:
                mark_entity_undefined(
                    target.conn,
                    entity_id=row["id"],
                    now=target.ctx.clock.now(),
                    run_id=target.run_id,
                    attempted=True,
                )
                rejected += 1
                continue
            definition, quote, url = result
            store_entity_definition(
                target.conn,
                entity_id=row["id"],
                definition=definition,
                method="web_grounded",
                confidence=ENTITY_CONFIDENCE["web_grounded"],
                quote=quote,
                url=url,
                now=target.ctx.clock.now(),
            )
            # `await`. `RunContext.counter` is a coroutine, so the bare call
            # incremented nothing and warned into a log nobody reads -- the one
            # counter `definitions` declares in its summary read 0 on every run
            # that worked.
            await target.ctx.counter("entities_defined")
            grounded += 1

        data = {"considered": len(rows), "grounded": grounded, "rejected": rejected}
        # A run that tried ten names and landed none must not read like one
        # that landed ten. Neither of them is a degraded run, so neither of
        # them touches `status`, which comes from `ctx.degraded` like every
        # other unit of work. This says what was found.
        if not rows:
            verdict = "nothing_to_do"
        elif grounded == 0:
            verdict = "nothing_found"
        elif grounded < len(rows):
            verdict = "some"
        else:
            verdict = "delivered"
        return run_launcher.LaunchOutcome(outcome=verdict, data=data)

    return await run_launcher.launch(
        request,
        kind="definitions",
        params={"limit": cap},
        work=work,
        stage="lookup",
    )


#: What one entity of a tier-3 sweep costs and takes when there is nothing
#: measured to say otherwise. Taken from `run:06aa9646` (2026-09-15): ten names,
#: $2.33 and 16.9 minutes, so $0.233 and 101 seconds each. A constant, and the
#: preview reports `cost_sample: 0` beside it so no surface presents it as an
#: average of anything.
FALLBACK_SWEEP_USD = 0.233
FALLBACK_SWEEP_SECONDS = 101.0

#: How many past sweeps the average is taken over.
SWEEP_COST_SAMPLE = 10


def _sweep_harness(request: Request) -> str:
    """The service that will run the sweep. See `run_launcher.lane_harness`."""
    from venona.services import run_launcher, run_types

    return run_launcher.lane_harness(request, run_types.LIGHT)


def _sweep_stats(conn: db.Conn, *, harness: str) -> tuple[float, float, int]:
    """What one entity of a sweep has actually cost, and taken, lately.

    PER ENTITY, computed by dividing each past sweep by what it considered --
    a run of ten and a run of two hundred are the same work repeated, so a mean
    of run totals would quote whatever size happened to run last.

    Returns `(usd, seconds, sample_size)`. A run whose counts cannot be read, or
    that considered nothing, contributes to neither average and to the sample
    size: an unreadable row is not evidence, and counting it as one would dilute
    the figure towards a fallback while claiming to be measured.
    """
    import json

    rows = repo_define.definitions_run_costs(conn, limit=SWEEP_COST_SAMPLE, harness=harness)
    per_usd: list[float] = []
    per_seconds: list[float] = []
    for row in rows:
        try:
            considered = int((json.loads(row["counts"] or "{}") or {}).get("considered") or 0)
        except (ValueError, TypeError):
            continue
        if considered <= 0:
            continue
        per_usd.append(float(row["cost_usd"]) / considered)
        span = _elapsed_seconds(row["started_at"], row["finished_at"])
        if span is not None and span > 0:
            per_seconds.append(span / considered)

    if not per_usd:
        return FALLBACK_SWEEP_USD, FALLBACK_SWEEP_SECONDS, 0
    seconds = sum(per_seconds) / len(per_seconds) if per_seconds else FALLBACK_SWEEP_SECONDS
    return sum(per_usd) / len(per_usd), seconds, len(per_usd)


def _elapsed_seconds(started: str | None, finished: str | None) -> float | None:
    """Wall clock between two run timestamps, or None if either is unusable.

    A run still in flight has no `finished_at`, and neither that nor a format
    this cannot read is worth an exception on a read path whose job is to
    estimate.
    """
    from datetime import datetime

    if not started or not finished:
        return None
    try:
        begin = datetime.fromisoformat(started.replace("Z", "+00:00"))
        end = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (end - begin).total_seconds()


@router.get("/entities/definitions/preview", response_model=RunPreview)
def preview_definitions_sweep(
    request: Request,
    limit: int | None = Query(None, ge=1, le=5000),
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """What a tier-3 sweep would attempt, and what it would cost.

    Calls `define.targets` with the run's own arguments rather than counting
    undefined rows, which is the rule `lookup_all_preview` was written under
    after a preview quoted nearly double and was believed.

    The price is measured off THIS instance's own `definitions` runs, per
    entity rather than per run, and it is explicitly a CEILING: tier 1 now runs
    before the web, so an unknown share of `will_attempt` is answered for
    nothing. On the sweep that prompted this, three names in ten were.
    """
    from venona.pipeline.define import targets as define_targets
    from venona.pipeline.types import resolve_cap

    settings = request.app.state.settings
    cap = resolve_cap(limit, settings.pipeline.definitions_refresh_cap)

    eligible = define_targets(conn, "", min_salience=settings.pipeline.define_min_salience)
    will_attempt = min(len(eligible), cap) if cap is not None else len(eligible)
    per_entity, per_entity_seconds, sample_size = _sweep_stats(
        conn, harness=_sweep_harness(request)
    )

    return {
        "eligible": len(eligible),
        "will_attempt": will_attempt,
        "cap": cap,
        "unit": "name",
        "unit_plural": "names",
        "per_item_usd": round(per_entity, 4),
        "estimate_usd": round(will_attempt * per_entity, 2),
        # Sequential, one entity at a time so a cancel lands inside one lookup.
        # Which makes the total the sum, and on six hundred names that is many
        # hours; quoting only money would tell half the truth.
        "per_item_seconds": round(per_entity_seconds, 1),
        "estimate_seconds": round(will_attempt * per_entity_seconds),
        "cost_sample": sample_size,
        "sample": [row["name"] for row in eligible[:8]],
    }


@router.post(
    "/entities/definitions/reread",
    status_code=202,
    response_model=RunStarted,
    response_model_exclude_unset=True,
)
async def reread_definitions(
    request: Request,
    limit: int | None = Query(None, ge=1, le=2000),
    use_model: bool = Query(True),
    #: How prominent a mention has to be. The stage default keeps the sweep to
    #: names a reader is likely to meet; 0 means every name in the corpus, which
    #: is what the free pass is asked for -- it costs nothing, so the argument
    #: for a floor is only about time.
    min_salience: float | None = Query(None, ge=0, le=1),
) -> dict[str, Any]:
    """Read stored articles again for the names they explain and nothing reached.

    The sibling of `refresh_definitions`, and the difference is where the
    answer is looked for. That one asks the WEB about names a brief left
    unexplained; this asks the articles Venona has already fetched, which is
    free wherever a sentence parses and one call per article where it does not.
    Neither is a substitute for the other -- an article that never describes
    the thing is what tier 3 is for.

    `use_model=false` runs the free half alone, over the whole corpus, for
    nothing. Worth doing first, and it is what the menu offers: it says how far
    the sentence shapes reach before any of this is charged for.

    RESUMABLE. Every name a pass covers gets its answer stamped onto the
    assertion, so a second call starts where the first stopped rather than
    paying for it again. Cancelling from the Runs page loses nothing.
    """
    from venona.pipeline.define import run_backfill

    settings = request.app.state.settings

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        report = await run_backfill(
            target.ctx,
            min_salience=(
                min_salience if min_salience is not None else settings.pipeline.define_min_salience
            ),
            document_limit=limit,
            use_model=use_model,
            budget=target.ctx.budget,
            emit=target.emit,
        )
        for warning in report.warnings:
            target.ctx.warn(warning)

        # `status` is left to derive from `ctx.degraded` like every other unit
        # of work; this says only what was found. A pass that read fifty
        # articles and took nothing from them is not a damaged run.
        # KEYED ON NAMES, not on articles. A free-only pass never reaches the
        # document loop, so `documents_seen` stays 0 and a run that had just
        # explained 25 names reported "everything already explained".
        # `entities_seen` is what every path sets, and it is the question the
        # outcome is actually answering.
        if not report.entities_seen:
            verdict = "nothing_to_do"
        elif not report.defined:
            verdict = "nothing_found"
        elif report.defined < report.entities_seen:
            verdict = "some"
        else:
            verdict = "delivered"
        return run_launcher.LaunchOutcome(outcome=verdict, data=report.as_dict())

    return await run_launcher.launch(
        request,
        kind="redefine",
        params={"limit": limit, "model": use_model},
        work=work,
        stage="define",
    )


@router.post(
    "/entities/{entity_id}/define",
    status_code=202,
    response_model=DefineStarted,
    # `coalesced` is set only when an identical light-lane run was already
    # in flight; without this it is `false` on every other launch.
    response_model_exclude_unset=True,
)
async def define(entity_id: str, request: Request) -> dict[str, Any]:
    """Ground one entity on demand -- tier 3, for a single id.

    A near-copy of `routers/glossary.py::lookup`, and deliberately so: the two
    do the same thing to two kinds of reference, and the invariants they have to
    hold are identical. Both are now thin `work` closures over
    `run_launcher.launch`, which is where that "deliberately so" finally stops
    being two maintained copies of a lifecycle.

    **202, not 200** -- a lookup is a search call plus up to three page fetches
    plus a read call, well past any sane request timeout, and progress goes
    through the same run event log so the client gets `/api/runs/{id}/stream`
    for free.

    **Does not refuse while a run is active.** It runs in the `light` lane
    (contract C11), so it starts alongside a research run instead of being
    refused for the tens of minutes one takes -- which is exactly the window in
    which someone is reading a brief and hits an undefined name. Two writers
    with two budgets is not a hazard here: this writes `entity_definitions`,
    which no pipeline stage writes concurrently, and it carries its own `Budget`
    like every other run.
    """
    from venona.pipeline.define import (
        ground_in_corpus_quote,
        lookup_web,
        mark_entity_undefined,
        store_entity_definition,
    )

    settings = request.app.state.settings

    probe = db.connect(settings.database_url)
    try:
        row = repo.entity_name(probe, entity_id)
    finally:
        probe.close()
    if row is None:
        raise HTTPException(404, detail="no such name")
    name = row["name"]

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        # THE CORPUS BEFORE THE WEB. This button went straight to tier 3, so a
        # reader pressing it on a name our own articles describe paid a search
        # and three page fetches to be told the web had nothing -- and from
        # production the fetches to defense.gov and dtic.mil are refused
        # outright, so "nothing" was frequently the machine's answer rather
        # than the world's. Tier 1 is free and instant, and it outranks
        # anything tier 3 could return.
        if ground_in_corpus_quote(
            target.conn, entity_id=entity_id, name=name, now=target.ctx.clock.now()
        ):
            await target.ctx.counter("entities_defined")
            return run_launcher.LaunchOutcome(
                outcome="delivered",
                data={"entity_id": entity_id, "grounded": True, "url": None},
            )

        trace: list[dict[str, Any]] = []
        result = await lookup_web(target.ctx, entity_id, name, trace=trace)

        if result is None:
            # Not a failure and not a degradation: the checks rejected every
            # candidate, which is the system working. The run did its whole job
            # and the answer is no, which is exactly what `nothing_found` says
            # and what `succeeded_partial` could only imply by lying about the
            # machinery.
            mark_entity_undefined(
                target.conn,
                entity_id=entity_id,
                now=target.ctx.clock.now(),
                run_id=target.run_id,
                attempted=True,
            )
            return run_launcher.LaunchOutcome(
                outcome="nothing_found",
                data={"entity_id": entity_id, "grounded": False, "rejected": trace},
            )

        definition, quote, url = result
        store_entity_definition(
            target.conn,
            entity_id=entity_id,
            definition=definition,
            method="web_grounded",
            confidence=ENTITY_CONFIDENCE["web_grounded"],
            quote=quote,
            url=url,
            now=target.ctx.clock.now(),
        )
        return run_launcher.LaunchOutcome(
            outcome="delivered",
            data={"entity_id": entity_id, "grounded": True, "url": url},
        )

    started = await run_launcher.launch(
        request,
        kind="lookup",
        # `target_kind` so a run row says what it looked up without the reader
        # having to infer it from which id-shaped key happens to be present.
        # Legacy rows carry only `entity_id` / `surface`; the UI falls back to
        # exactly that.
        params={"target_kind": "entity", "entity_id": entity_id, "name": name},
        work=work,
        stage="lookup",
    )
    return {**started, "entity_id": entity_id}
