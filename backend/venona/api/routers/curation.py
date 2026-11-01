"""The curation queue: alias conflicts, merges, and the graph catching up.

A transport. Every decision `services/curate.py` owns is taken there rather
than re-implemented here: `merge_entities` is append-only, cycle-guarded and
reversible, and a second implementation behind HTTP would be the one that
drifts.

Merging is the only destructive action in the application. Two properties make
it safe to expose: it is a row in `entity_mappings` rather than an edit
(invariant 1 holds, `venona graph rebuild` still reconstructs everything), and
`unmerge` reverses it while reopening the conflict it closed. The UI's undo is
that call, not a client-side illusion.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from venona.api.authz import actor_email, actor_id
from venona.api.deps import Window, get_db, page_window
from venona.api.schemas.curation import (
    Conflict,
    ConflictList,
    ConflictOptions,
    CorrectTypeRequest,
    CorrectTypeResponse,
    CurationStatus,
    Merge,
    MergeList,
    MergeRequest,
    PredicateOption,
    RelateRequest,
)
from venona.api.schemas.definitions import (
    DefinitionQueue,
    RebuildResult,
    RetractionResult,
    UnmergeResult,
)
from venona.api.schemas.run import RunStarted
from venona.db import core as db
from venona.db.repositories import curation as repo
from venona.pipeline.types import SystemClock, SystemIdGen, utc_now_iso
from venona.services import run_launcher, run_service, run_types
from venona.services.curate import (
    MergeError,
    correct_entity_type,
    count_merges,
    dismiss_conflict,
    list_conflicts,
    list_merges,
    merge_entities,
    open_conflict_counts,
    resolve_entity_id,
    resolved_pair,
    unmerge,
)

router = APIRouter(prefix="/curation", tags=["curation"])


def _conflict(row: dict[str, Any]) -> Conflict:
    return Conflict(
        id=row["id"],
        kind=row.get("kind"),
        surface=row["surface"],
        entity_type=row["entity_type"],
        existing_entity_id=row["existing_entity_id"],
        existing_name=row.get("existing_name"),
        proposed_entity_id=row["proposed_entity_id"],
        proposed_name=row.get("proposed_name"),
        article_id=row.get("article_id"),
        created_at=row["created_at"],
        resolved=bool(row.get("resolved")),
        resolution=row.get("resolution"),
        resolved_at=row.get("resolved_at"),
        resolved_by=row.get("resolved_by_email"),
        suggestion=row.get("suggestion"),
        suggestion_note=row.get("suggestion_note"),
        suggestion_child=row.get("suggestion_child"),
        suggestion_predicate=row.get("suggestion_predicate"),
    )


@router.get("/conflicts", response_model=ConflictList)
def get_conflicts(
    kind: str | None = Query(None),
    include_resolved: bool = Query(False),
    window: Window = Depends(page_window(default=500, cap=500)),
    conn: db.Conn = Depends(get_db),
) -> ConflictList:
    """The merge queue, bounded.

    The default IS the cap: the queue is worked down card-at-a-time rather
    than browsed, so the client takes the largest bounded bite and reloads
    when it runs the buffer dry — no offset arithmetic to keep honest against
    the moot-row drop in `list_conflicts`, which can return a short page while
    open rows remain. `total_open` counts every row the queue would show,
    across every kind; it is a badge, never a has-more signal.
    """
    rows = list_conflicts(
        conn,
        kind=kind,
        include_resolved=include_resolved,
        limit=window.limit,
        offset=window.offset,
    )
    counts = open_conflict_counts(conn)
    return ConflictList(
        items=[_conflict(r) for r in rows],
        counts=counts,
        total_open=sum(counts.values()),
    )


@router.post("/conflicts/{conflict_id:path}/keep-apart", response_model=Conflict)
def keep_apart(conflict_id: str, request: Request, conn: db.Conn = Depends(get_db)) -> Conflict:
    """Record that a pair is genuinely two different things.

    Not a no-op and not a skip. Roughly 40% of the containment queue is correct
    distinctions -- `Thales` / `Thales Australia` -- and with no way to say so
    they sat there forever, indistinguishable from rows nobody had looked at.
    """
    try:
        dismiss_conflict(conn, conflict_id, now=utc_now_iso(), actor=actor_id(request))
    except MergeError as exc:
        raise HTTPException(409, detail=str(exc)) from exc

    row = repo.conflict(conn, conflict_id)
    return _conflict(dict(row))


@router.post("/conflicts/{conflict_id:path}/reopen", response_model=Conflict)
def reopen(conflict_id: str, conn: db.Conn = Depends(get_db)) -> Conflict:
    """Undo a keep-apart.

    The merge side of undo already existed as `unmerge`, which reopens the
    conflict it closed. Keep-apart had no counterpart, so a misfired `K` was
    unrecoverable from the UI -- and on a keyboard queue a misfired key is a
    matter of time, not chance.

    Reverses a `kept_apart` or a `related` verdict. A `merged` closure must be
    undone by unmerging, or the conflict would reopen while the entities stayed
    merged.
    """
    row = repo.conflict_resolution(conn, conflict_id)
    if row is None:
        raise HTTPException(404, detail=f"no conflict {conflict_id!r}")
    if row["resolution"] not in ("kept_apart", "related"):
        raise HTTPException(
            409,
            detail=(
                "only a kept-apart or related decision reopens here; "
                "a merged pair must be unmerged so the names follow"
            ),
        )

    if row["resolution"] == "related":
        # Retract rather than delete: `assertions` is append-only (C1), and
        # `retracted_at` is the column for a claim made and then withdrawn.
        repo.retract_run_assertions(conn, run_id=f"curation:{conflict_id}", now=utc_now_iso())

    repo.reopen_conflict(conn, conflict_id)
    conn.commit()

    updated = repo.conflict(conn, conflict_id)
    return _conflict(dict(updated))


@router.get("/conflicts/{conflict_id:path}/options", response_model=ConflictOptions)
def relate_options(conflict_id: str, conn: db.Conn = Depends(get_db)) -> ConflictOptions:
    """Predicates that are well-typed for this pair, both ways round.

    Generated from the ontology so the choices a curator is offered and the
    rules `check_relation` enforces cannot drift apart -- the same reason the
    extraction guide is generated rather than written.
    """
    from venona.ontology import get_ontology

    row = repo.conflict_endpoints(conn, conflict_id)
    if row is None:
        raise HTTPException(404, detail=f"no conflict {conflict_id!r}")

    # RESOLVED, like every other reader of a conflict row -- `resolved_pair` is
    # where trusting the stored columns instead is argued out. Reading them here
    # typed the `org:canada` tombstone as an Organization while the card showed
    # `Canada`, a Country: PART_OF offered, BASED_IN never, and `relate` -- which
    # does resolve -- answered 422 naming a type that had never been on screen.
    existing_id, proposed_id = resolved_pair(conn, row)
    types = repo.entity_types(conn, existing_id, proposed_id)
    a_type = types.get(existing_id)
    b_type = types.get(proposed_id)

    ontology = get_ontology()

    def allowed(subject_type: str | None, object_type: str | None) -> list[PredicateOption]:
        if not subject_type or not object_type:
            return []
        return [
            PredicateOption(name=name, description=spec.description, symmetric=spec.symmetric)
            for name, spec in ontology.predicates.items()
            if ontology.check_relation(name, subject_type, object_type) is None
        ]

    return ConflictOptions(
        forward=allowed(a_type, b_type),
        reverse=allowed(b_type, a_type),
        subject_type=a_type,
        object_type=b_type,
    )


@router.post("/conflicts/{conflict_id:path}/relate", response_model=Conflict)
def relate(conflict_id: str, payload: RelateRequest, conn: db.Conn = Depends(get_db)) -> Conflict:
    """The third verdict: they are different things, AND here is how they relate.

    `General Dynamics` / `General Dynamics Information Technology` is the shape:
    merging is wrong, and keeping them apart throws away the one fact the
    conflict actually surfaced. GDIT is PART_OF General Dynamics.

    The assertion is written the same way every other one is -- an `assertions`
    row with a predicate the ontology accepts -- but marked `method='manual'`
    and carrying a quote that names the decision rather than a sentence. That is
    the seed loader's precedent (`services/seed.py`), and it keeps a curated
    claim distinguishable from an extracted one forever after.

    `article_id` is the conflict's own article: the document where the surface
    was seen, which is what caused the conflict in the first place.
    """
    from venona.ontology import get_ontology

    row = repo.conflict_row(conn, conflict_id)
    if row is None:
        raise HTTPException(404, detail=f"no conflict {conflict_id!r}")
    if row["resolved"]:
        raise HTTPException(409, detail="that conflict is already settled")

    # The pair as the queue showed it. `list_conflicts` resolves merge chains,
    # so the client posts survivors while the row still stores the ids it was
    # filed with -- comparing against those would 400 on exactly the conflicts
    # that outlived a merge.
    pair = set(resolved_pair(conn, row))
    if {payload.subject_id, payload.object_id} != pair:
        raise HTTPException(400, detail="subject and object must be this conflict's two names")

    types = repo.entity_types(conn, payload.subject_id, payload.object_id)
    problem = get_ontology().check_relation(
        payload.predicate,
        types.get(payload.subject_id, ""),
        types.get(payload.object_id, ""),
    )
    if problem:
        # The ontology names the correct form, so the curator reads its message.
        raise HTTPException(422, detail=problem)

    now = utc_now_iso()
    names = {
        r["id"]: r["name"] for r in repo.entity_names(conn, payload.subject_id, payload.object_id)
    }
    repo.insert_curated_relation(
        conn,
        assertion_id=SystemIdGen().uuid7(),
        conflict_id=conflict_id,
        article_id=row["article_id"] or "doc:curated",
        predicate=payload.predicate,
        subject_id=payload.subject_id,
        object_id=payload.object_id,
        evidence_quote=(
            f"Curated: {names.get(payload.subject_id, payload.subject_id)} "
            f"{payload.predicate} {names.get(payload.object_id, payload.object_id)}. "
            f"Recorded from alias conflict {conflict_id}."
        ),
        now=now,
    )
    repo.mark_related(conn, conflict_id=conflict_id, now=now)
    conn.commit()

    updated = repo.conflict(conn, conflict_id)
    return _conflict(dict(updated))


@router.post("/conflicts/{conflict_id}/correct-type", response_model=CorrectTypeResponse)
def correct_type(
    conflict_id: str,
    payload: CorrectTypeRequest,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> CorrectTypeResponse:
    """Say what kind a thing is, and fold both readings of it into one entry.

    The fourth verdict, and the one the other three could not express: `keep
    apart` leaves two entries for one thing, `relate` invents a relationship
    that is not there, and `merge` refuses to cross a type boundary because the
    rule that let it would also merge two unrelated things that share a name.
    Naming the type is what makes the crossing safe -- a person has said which
    side of the boundary the thing sits on.

    Two merge ids because up to two entities fold in, and both are generated
    here for the reason `create_merge` generates one.
    """
    ids = SystemIdGen()
    try:
        result = correct_entity_type(
            conn,
            conflict_id,
            entity_type=payload.entity_type,
            merge_ids=(f"merge:{ids.uuid7()}", f"merge:{ids.uuid7()}"),
            now=SystemClock().now(),
            actor=actor_id(request),
        )
    except MergeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CorrectTypeResponse(**result)


@router.post("/merges", response_model=Merge, status_code=201)
def create_merge(payload: MergeRequest, request: Request, conn: db.Conn = Depends(get_db)) -> Merge:
    """Merge two entities. Reversible, by `POST /merges/{merge_id}/unmerge`.

    `MergeError` becomes a 409 rather than a 500 because every case it covers is
    a legitimate refusal the curator should read: merging into something already
    merged away, closing a cycle, or a self-merge.
    """
    merge_id = f"merge:{SystemIdGen().uuid7()}"
    now = SystemClock().now()

    # The ids the merge will ACTUALLY use, resolved BEFORE it runs.
    # `merge_entities` follows merge chains, so a request naming a tombstone
    # lands on its survivor and reporting the posted ids would make a retarget
    # invisible. Resolving afterwards is worse than useless: the merge has just
    # made `from_id` a tombstone itself, so it would resolve to the survivor
    # and the response would claim the entity merged into itself.
    from_id = resolve_entity_id(conn, payload.from_id)
    to_id = resolve_entity_id(conn, payload.to_id)

    try:
        settled = merge_entities(
            conn,
            from_id=payload.from_id,
            to_id=payload.to_id,
            merge_id=merge_id,
            reason=payload.reason,
            now=now,
            actor=actor_id(request),
        )
    except MergeError as exc:
        raise HTTPException(409, detail=str(exc)) from exc

    names = {r["id"]: r["name"] for r in repo.entity_names(conn, from_id, to_id)}
    return Merge(
        from_id=from_id,
        to_id=to_id,
        merge_id=merge_id,
        active=True,
        reason=payload.reason,
        created_at=now,
        from_name=names.get(from_id),
        to_name=names.get(to_id),
        # From the principal rather than re-read: this response is the one caller
        # that already knows, and `response_model` would silently drop the field to
        # None if it were left off -- which would read as "nobody signed this".
        merged_by=actor_email(request),
        settled_conflict_ids=settled,
    )


@router.post("/merges/{merge_id:path}/unmerge", status_code=200, response_model=UnmergeResult)
def undo_merge(
    merge_id: str, request: Request, from_id: str = Query(...), conn: db.Conn = Depends(get_db)
) -> dict[str, Any]:
    """Undo. Sets the mapping inactive and REOPENS the conflict it closed.

    The mapping row itself is kept as a record -- nothing is deleted, so the
    history of what was tried survives the reversal.
    """
    try:
        unmerge(
            conn, from_id=from_id, merge_id=merge_id, now=utc_now_iso(), actor=actor_id(request)
        )
    except MergeError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return {"merge_id": merge_id, "from_id": from_id, "active": False}


@router.post(
    "/assertions/{assertion_id:path}/retract", status_code=200, response_model=RetractionResult
)
def retract(assertion_id: str, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """Withdraw one claim whose evidence does not carry it.

    THE GAP THIS CLOSES. Every other verdict in this router is about identity:
    are these two names one thing. Nothing here could act on the other kind of
    error, where the names are right and the SENTENCE behind the edge says
    something else, so a relationship a person had read and found unsupported
    had nowhere to go. `graph_health.py effectiveness` now puts those in front
    of a reader, which is what made the absence worth fixing.

    NOT REVERSIBLE HERE, deliberately. `unmerge` exists because a merge is a
    keystroke in a fast queue and a misfire is a matter of time; a retraction
    is the end of reading a quote and deciding it does not say what was claimed,
    which is not a key anyone presses by accident. If that turns out to be
    wrong, the row is still there and un-retracting it is one nullable column.

    THE PROJECTION DOES NOT FOLLOW. Neo4j is rebuilt from `assertions`
    (invariant 1) and nothing rebuilds it on a write, so the edge stays on the
    canvas until the next projection. That is true of every retraction here,
    including the one `reopen` already performs.
    """
    row = repo.assertion(conn, assertion_id)
    if row is None:
        raise HTTPException(404, detail=f"no assertion {assertion_id!r}")
    if row["retracted_at"]:
        return {
            "assertion_id": assertion_id,
            "retracted_at": row["retracted_at"],
            "already_retracted": True,
        }
    now = utc_now_iso()
    repo.retract_assertion(conn, assertion_id=assertion_id, now=now)
    conn.commit()
    return {"assertion_id": assertion_id, "retracted_at": now, "already_retracted": False}


@router.get("/merges", response_model=MergeList)
def get_merges(
    active_only: bool = Query(True),
    window: Window = Depends(page_window(default=50, cap=200)),
    conn: db.Conn = Depends(get_db),
) -> MergeList:
    """Merge history, windowed — it grows monotonically forever.

    An envelope, where this returned a bare array: the paging convention needs
    a `total` beside the page, and a bare list has nowhere to put one.
    """
    items = [
        Merge(
            from_id=r["from_id"],
            to_id=r["to_id"],
            merge_id=r["merge_id"],
            active=bool(r["active"]),
            reason=r["reason"],
            created_at=r["created_at"],
            from_name=r["from_name"],
            to_name=r["to_name"],
            merged_by=r["merged_by_email"],
        )
        for r in list_merges(
            conn, active_only=active_only, limit=window.limit, offset=window.offset
        )
    ]
    return MergeList(items=items, total=count_merges(conn, active_only=active_only))


async def _projection_state(graph, conn: db.Conn) -> tuple[int, int]:
    """`(pending, blocked)` — what a rebuild would apply, and what it cannot.

    Reported after "as I merge things I don't see the rebuild count increment as
    much as I merge", which was two separate things.

    Only `active` entities are projected, so the measure is direct: a merged
    entity that STILL HAS A NODE is exactly a merge the projection has not
    caught up with. Curated relationships are counted apart because most of
    them cannot be projected at all -- measured 2026-08-06 on the live
    database, ten `relate` verdicts existed, none in the graph, all ten with an
    uncorroborated endpoint. ADR 0240 carries why folding the two is worse
    than reporting them separately.

    Returns `(0, 0)` when Neo4j is unreachable. A graph we cannot query is not
    evidence of pending work.
    """
    merged = repo.merged_entity_ids(conn)
    curated = repo.curated_relations(conn)
    projectable = [c for c in curated if c["s_status"] == "active" and c["o_status"] == "active"]
    blocked = len(curated) - len(projectable)

    if not merged and not projectable:
        return 0, blocked

    try:
        async with graph.driver.session() as session:
            pending = 0
            if merged:
                result = await session.run(
                    "MATCH (n:Entity) WHERE n.id IN $ids RETURN count(n) AS n", ids=merged
                )
                record = await result.single()
                pending += record["n"] if record else 0
            if projectable:
                # One round trip for the whole set. A query per edge made this
                # endpoint cost more the more curating had been done, on a poll
                # that ticks every few seconds while a light run is live.
                #
                # Grouped by the POSITION in the list, not by the edge itself:
                # `curated_relations` does not deduplicate, so two assertions of
                # the same triple are two rows here, and grouping on the map
                # would silently fold them into one.
                result = await session.run(
                    "UNWIND range(0, size($edges) - 1) AS i "
                    "WITH i, $edges[i] AS edge "
                    "OPTIONAL MATCH (a:Entity {id: edge.a})-[e]->(b:Entity {id: edge.b}) "
                    "WHERE type(e) = edge.p "
                    "WITH i, count(e) AS n "
                    "RETURN sum(CASE WHEN n = 0 THEN 1 ELSE 0 END) AS absent",
                    edges=[{"a": e["s"], "b": e["o"], "p": e["p"]} for e in projectable],
                )
                record = await result.single()
                pending += record["absent"] if record else 0
            return pending, blocked
    except Exception:  # noqa: BLE001 - a down graph is not a curation error
        return 0, blocked


@router.get(
    "/definitions",
    response_model=DefinitionQueue,
    # `QueueSuggestion.entry_id` is set only by the `choose` verdict. Without
    # this it serialises as an explicit null on every other suggestion --
    # a key that was never on the wire, on the queue's own payload.
    response_model_exclude_unset=True,
)
def definitions_queue(
    window: Window = Depends(page_window(default=50, cap=200)),
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """The definitions review queue, one SURFACE per decision.

    Lives here rather than under `/glossary` because it is a queue, and this is
    where queues are: the merge queue's `GET /conflicts` is the same shape and
    the same job. The VERDICTS stay on `/glossary/{surface}/…` where the
    resource is -- moving them would break the hover card's "look this up" for
    no gain, and a queue that reads one place and writes another is the normal
    arrangement, not a smell.

    See `services/definition_queue.py` for why the unit is a surface and how the
    suggestion is derived.
    """
    from venona.services import definition_queue

    return definition_queue.build(conn, limit=window.limit, offset=window.offset)


@router.get("/status", response_model=CurationStatus)
async def status(request: Request, conn: db.Conn = Depends(get_db)) -> CurationStatus:
    by_kind = open_conflict_counts(conn)
    # `decided_at IS NULL` -- these are counts of PENDING WORK, and a surface a
    # curator reviewed and deliberately kept ambiguous is finished. Without the
    # clause "keep both" would be a verdict that leaves the number it was meant
    # to clear exactly where it was, which is the same as having no verdict.
    defs = repo.undecided_definitions_by_status(conn)
    pending, blocked = await _projection_state(request.app.state.graph, conn)
    return CurationStatus(
        open_conflicts=sum(by_kind.values()),
        by_kind=by_kind,
        undefined_definitions=defs.get("undefined", 0),
        ambiguous_definitions=defs.get("ambiguous", 0),
        pending_definitions=repo.pending_definition_surfaces(conn),
        pending_rebuild=pending,
        blocked_relations=blocked,
        # The RESEARCH lane, not a global "is anything busy" (C11): only a
        # research run writes the projection, so a lookup must not disable the
        # rebuild the curator is waiting on.
        run_active=run_service.registry.active_run_id(run_types.RESEARCH) is not None,
    )


@router.post("/rebuild", status_code=200, response_model=RebuildResult)
async def rebuild(request: Request, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """Re-project the graph so merges become visible.

    Batched on purpose. A merge is durable the moment it is written, and
    re-projecting after every decision would make a keyboard queue crawl and
    409 constantly. One pass after a session of curation costs the same as one
    pass after a single merge.

    **Refuses while a RESEARCH run is active.** The projection is what a run
    writes at its end; two writers would race, and the run would win with stale
    mappings. A light run is not one of those writers -- a lookup writes a
    definition row and never touches `assertions` -- so it does not block this.
    """
    active = run_service.registry.active_run_id(run_types.RESEARCH)
    if active:
        raise HTTPException(
            409,
            detail={
                "error": "run_active",
                "run_id": active,
                "message": "the graph refreshes when the run finishes; wait for it",
            },
        )

    from venona.graph.projector import ProjectionBusy, project

    # `rebuild=False` deliberately. Truncating first left the graph EMPTY for the
    # length of the pass, so anyone with the explorer open watched it disappear
    # and come back, and a projection is minutes on a real corpus. `project`
    # prunes to the same end state without it; `drop_all` stays for `venona graph
    # rebuild`, where a person asked for the truncation and is watching a terminal.
    #
    # `wait=False` because there is a person waiting on this button. Queueing
    # behind a run's own projection would hold the request open for minutes.
    try:
        counts = await project(conn, request.app.state.graph.driver, wait=False)
    except ProjectionBusy:
        raise HTTPException(
            409,
            detail={
                "error": "rebuilding",
                "message": "the graph is refreshing right now; try again in a moment",
            },
        ) from None
    return {"rebuilt": True, **counts}


@router.post("/placements", status_code=202, response_model=RunStarted)
async def propose_placements(request: Request, limit: int = Query(60, ge=1, le=200)) -> Any:
    """Suggest where the entities with nothing attached to them belong.

    The corpus supplies the leaves and almost never the trunk: no article states
    which department a command reports to, so nothing a research run reads will
    ever attach one. This asks, and files each answer for a person to accept.

    It writes no relationship and edits no document. Every suggestion lands in
    the same queue as every other one and needs the same keystroke, which is
    what makes it safe for the prompt behind it to use knowledge the rest of the
    system is forbidden to use.
    """

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        from venona.pipeline.anchors import propose_anchors

        report = await propose_anchors(target.ctx, limit=limit)
        data = {"floating": report.isolated, "proposed": report.proposed}

        # Three different nothings, and only one of them is a run that did no
        # work. Nothing floating is `nothing_to_do`; things floating and no
        # placement for any of them is a real answer, not a degraded run.
        if not report.isolated:
            verdict = "nothing_to_do"
        elif report.proposed == 0:
            verdict = "nothing_found"
        elif report.proposed < report.isolated:
            verdict = "some"
        else:
            verdict = "delivered"
        return run_launcher.LaunchOutcome(outcome=verdict, data=data)

    return await run_launcher.launch(
        request,
        kind="anchors",
        params={"limit": limit},
        work=work,
        stage="anchors",
    )
