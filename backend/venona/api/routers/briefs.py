from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from venona.api.deps import Window, get_db, page_window
from venona.api.schemas.brief import Brief, BriefList, BriefSearchResults, RewriteRequest
from venona.api.schemas.relevance import ForYou
from venona.api.schemas.run import RunStarted
from venona.db import core as db
from venona.db.repositories import curation as repo
from venona.pipeline.types import utc_now_iso
from venona.services import brief_service, run_launcher

router = APIRouter(prefix="/briefs", tags=["briefs"])


def _ceiling(request: Request) -> str | None:
    """How far back the archive opens for this caller, or None for no limit.

    From the principal, which `AuthMiddleware` re-reads from the `users` row on
    every request -- so a subscription that ended an hour ago narrows the
    archive now rather than when a fortnight-old cookie expires.

    None for an entitled reader, an admin, and every principal with no row (the
    break-glass admin, the root token, auth off). The paywall must never be the
    thing standing between an operator and a broken instance.
    """
    principal = getattr(request.state, "principal", None)
    return getattr(principal, "archive_ceiling", None) if principal else None


@router.get("", response_model=BriefList)
def list_briefs(
    request: Request,
    # 500, not 100: a day can hold several briefs, so a hundred rows is weeks of
    # history rather than the year the archive was sized for.
    window: Window = Depends(page_window(default=20, cap=500)),
    conn: db.Conn = Depends(get_db),
) -> BriefList:
    items, total = brief_service.list_briefs(
        conn, limit=window.limit, offset=window.offset, ceiling=_ceiling(request)
    )
    return BriefList(items=items, total=total)


# Declared before /{brief_id} for the same reason as /latest: a literal path
# must win against the parameterised one, or "search" is looked up as a brief id
# and answers 404.
@router.get("/search", response_model=BriefSearchResults)
def search(
    request: Request,
    q: str = Query("", max_length=200, description="text to find in titles, headlines and bodies"),
    entity: str | None = Query(
        None,
        max_length=200,
        description="an entity id from a block's spans, e.g. prog:next-generation-...",
    ),
    limit: int = Query(20, ge=1, le=200),
    conn: db.Conn = Depends(get_db),
) -> BriefSearchResults:
    """Which briefs mention this, and where.

    `q` and `entity` are independent and ANDed when both are given. Neither is
    required: with both empty this answers an empty result rather than the whole
    archive, because "show me everything" is what `GET /briefs` is for and
    returning it from a search box makes an empty query look like a bug.
    """
    return brief_service.search_briefs(
        conn, q=q, entity=entity, limit=limit, ceiling=_ceiling(request)
    )


# Declared before /{brief_id} so "for-you" is never captured as an id.
@router.get("/for-you", response_model=ForYou, response_model_exclude_none=True)
def for_you(request: Request, conn: db.Conn = Depends(get_db)) -> ForYou:
    """The reader's own list, and their document when today has one.

    ZERO model calls: the list is the shared per-cluster analysis re-rolled
    through this reader's emphasis and follows at read time. The document is
    whatever `POST /for-you/generate` last wrote for today, with `stale`
    saying whether the reader's inputs have moved since.

    It also reports whether a generation is in flight and whether one is DUE.
    Reporting is all it does: `auto_due` is a fact about the reader's day, and
    the client turns it into the POST that spends. Starting a run from here
    would put a model call behind a GET, which is the property this docstring
    opens by promising.
    """
    from venona.api.routers.learning import _actor
    from venona.services import personal_brief, run_service

    user_id = _actor(request, conn)
    payload = personal_brief.for_you(
        conn,
        user_id=user_id,
        settings=request.app.state.settings,
        now_iso=utc_now_iso(),
        registry=run_service.registry,
    )
    return ForYou(**payload)


@router.post(
    "/for-you/generate",
    status_code=202,
    response_model=RunStarted,
    response_model_exclude_unset=True,
)
async def generate_for_you(request: Request, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """Write (or refresh) the reader's own brief: ONE light-lane model call.

    The one deliberate reader-reachable spend. Bounded three ways: the
    launcher coalesces a double-click, the run itself no-ops when the
    reader's document already covers the newest shared brief with the same
    inputs, and the per-reader daily cap refuses past
    `personal_briefs_per_day`.
    """
    from venona.api.routers.learning import _actor
    from venona.db.repositories import relevance as relevance_repo
    from venona.services import personal_brief

    user_id = _actor(request, conn)
    settings = request.app.state.settings

    # A hard ceiling on spend, deliberately kept a UTC calendar day while
    # everything else about the reader's brief follows the shared edition. The
    # two are different jobs: a day bounds what one reader can cost, and an
    # operator who runs three extra shared briefs in an afternoon must not
    # multiply that ceiling by three.
    date = utc_now_iso()[:10]
    used = relevance_repo.count_personal_brief_runs(conn, user_id=user_id, since=date)
    if used >= settings.llm.personal_briefs_per_day:
        raise HTTPException(
            429,
            detail=(
                "your brief has been rewritten enough times today; the allowance resets tomorrow"
            ),
        )
    return await personal_brief.start_generation(request, settings=settings, user_id=user_id)


# Declared before /{brief_id} so "latest" is never captured as an id.
@router.get("/latest", response_model=Brief, response_model_exclude_none=True)
def get_latest(request: Request, conn: db.Conn = Depends(get_db)) -> Brief:
    """The newest brief this reader may open.

    Deliberately NOT refused for a lapsed reader. `/` renders this, so a 403
    would bounce them to the paywall and they would never see the archive they
    paid for; instead the ceiling narrows it to their last covered edition.
    """
    ceiling = _ceiling(request)
    brief_id = brief_service.latest_brief_id(conn, ceiling=ceiling)
    if brief_id is None:
        raise HTTPException(404, detail="no briefs yet; start a run from the Runs page")
    brief = brief_service.get_brief(conn, brief_id, ceiling=ceiling)
    if brief is None:
        raise HTTPException(404, detail=f"brief {brief_id} disappeared while loading")
    return brief


@router.get("/{brief_id}", response_model=Brief, response_model_exclude_none=True)
def get_brief(brief_id: str, request: Request, conn: db.Conn = Depends(get_db)) -> Brief:
    brief = brief_service.get_brief(conn, brief_id, ceiling=_ceiling(request))
    if brief is None:
        raise HTTPException(404, detail=f"no brief {brief_id}")
    return brief


# Re-annotation lives on the CLI only (`venona reannotate`), not here.
#
# It re-resolves spans against the CURRENT alias index, which is the payoff of
# storing offsets rather than markup: improving the alias table retro-fixes
# every historical brief for free. But it is a maintenance operation run after
# a merge or an alias edit -- both of which are already CLI-only -- and no
# frontend ever called the endpoint. `pipeline/brief.py::reannotate` is the
# implementation; `cli.py::_reannotate` is the entry point.
#
# `RescoreResponse` was declared here too, unused: /rescore lives in meta.py.


@router.post(
    "/rewrite",
    status_code=202,
    response_model=RunStarted,
    # `coalesced` is set only when an identical light-lane run was already
    # in flight; without this it is `false` on every other launch.
    response_model_exclude_unset=True,
)
async def rewrite(
    request: Request,
    payload: RewriteRequest | None = None,
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """Re-run the brief stage alone, over what is already in the database.

    Cheap to add and the reason `--only` exists: every stage reads its inputs
    from Postgres by STATE, not by run id (contract C7), so this re-briefs the
    current window without re-fetching or re-analysing anything. The same
    property is what makes `venona run --only brief` work from a terminal, and
    this is that command with a button on it.

    **It has to name the brief it is rewriting, and that is the one place this
    endpoint departs from keying on state.** Retrieval excludes every cluster
    any brief has featured, so by the time this button is pressable the brief on
    screen has claimed all of them: the run retrieved nothing, ended
    `succeeded_partial` with "nothing new since the last brief", and never
    reached the model. `rewrite_of` releases that ONE brief's claim -- see
    `build_retrieval`. It adds a brief rather than replacing one; the brief
    being rewritten stays in the archive, which is what ids-unique-per-
    generation (C3) already assumes.

    **Research lane, so it 409s behind a pipeline run** rather than queueing.
    The brief is the single most expensive call in the system -- opus at high
    effort -- and it writes the artefact a full run is itself trying to write.
    Two of them racing would produce two briefs for one window and leave which
    one a reader sees up to whichever finished last.
    """
    from venona.api.routers.runs import _refuse_if_research_is_busy
    from venona.llm.prompts import load_profile
    from venona.pipeline.orchestrator import execute_run

    settings = request.app.state.settings
    _refuse_if_research_is_busy(settings)

    # Absent means the latest, so a caller with no view state -- the CLI, a
    # curl -- gets the same behaviour as the button. Resolved HERE rather than
    # in the stage: `run_brief` must be handed an id or nothing, or "the latest
    # brief" would be re-resolved inside a run that is itself writing one.
    target_brief = payload.brief_id if payload else None
    if target_brief is None:
        latest = repo.latest_brief_id(conn)
        target_brief = latest["id"] if latest else None

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        outcome = await execute_run(
            scope=target.scope,
            run_id=target.run_id,
            settings=settings,
            conn=target.conn,
            graph=request.app.state.graph,
            kind="rewrite",
            only=("brief",),
            rewrite_of=target_brief,
        )
        return run_launcher.LaunchOutcome(
            status=outcome.status,
            outcome=outcome.outcome,
            brief_id=outcome.brief_id,
            error=outcome.error,
        )

    try:
        return await run_launcher.launch(
            request,
            kind="rewrite",
            # `rewrite_of` is in `params` so the Runs page can say WHICH brief a
            # rewrite was of. It is also what makes two rewrites of different
            # briefs distinct to the launcher's coalescing.
            params={"only": ["brief"], "rewrite_of": target_brief},
            work=work,
            profile_hash=load_profile(settings).short_hash,
            migrate=True,
            context={"graph": request.app.state.graph, "profile": load_profile(settings)},
        )
    except run_launcher.LaneBusy as exc:
        raise HTTPException(409, detail=str(exc)) from None
