from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from venona.api.deps import get_db
from venona.api.schemas.graph import (
    BriefScope,
    BriefSubgraphRequest,
    BriefSubgraphResponse,
    EdgeProvenanceResponse,
    GraphCoverage,
    GraphFilters,
    GraphOverviewResponse,
    GraphPathResponse,
    GraphPayloadResponse,
    GraphSearchResponse,
)
from venona.db import core as db
from venona.db.repositories import curation as repo
from venona.graph import queries
from venona.services.brief_service import brief_entities
from venona.services.graph_service import describe_path, edge_provenance

router = APIRouter(tags=["graph"])


class GraphOverview(BaseModel):
    limit: int = Field(default=queries.OVERVIEW_EDGE_LIMIT, ge=1, le=queries.OVERVIEW_EDGE_LIMIT)
    filters: GraphFilters = Field(default_factory=GraphFilters)


class GraphQuery(BaseModel):
    seed_entity_ids: list[str] = Field(min_length=1, max_length=50)
    limit: int = Field(default=queries.NEIGHBOR_LIMIT, ge=1, le=queries.NEIGHBOR_LIMIT)
    filters: GraphFilters = Field(default_factory=GraphFilters)


@router.post("/graph/query", response_model=GraphPayloadResponse)
async def graph_query(payload: GraphQuery, request: Request) -> dict[str, Any]:
    """One hop out from the seeds.

    Capped and ordered server-side: `govt:defense-department` has enough edges to hang the
    canvas, and filtering here means we never fetch what we would immediately
    hide.
    """
    graph = request.app.state.graph
    result = await queries.neighborhood(
        graph.driver,
        payload.seed_entity_ids,
        limit=payload.limit,
        types=payload.filters.types,
        predicates=payload.filters.predicates,
        min_source_count=payload.filters.min_source_count,
        min_confidence=payload.filters.min_confidence,
        include_unconfirmed=payload.filters.include_unconfirmed,
    )
    return {
        "nodes": result.nodes,
        "edges": result.edges,
        "truncated": result.truncated,
        "total": result.total,
    }


@router.post("/graph/overview", response_model=GraphOverviewResponse)
async def graph_overview(
    payload: GraphOverview,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """The whole graph, filtered. What `/graph/query` structurally cannot answer.

    `graph_query` requires a seed and walks exactly one hop, so the shape of the
    graph -- its clusters, its bridges, its isolated nodes -- was unreachable
    from the UI no matter how many times you expanded. The whole projection is
    smaller than one hop out of a hub like `govt:defense-department`, so the seed requirement was
    buying nothing.

    Both result sets are bounded (`OVERVIEW_EDGE_LIMIT`, `OVERVIEW_NODE_LIMIT`)
    and either cap sets `truncated`. The limits are sized from the projection's
    measured SHAPE rather than against a corpus snapshot, which goes stale. See
    `queries.py` and `docs/GRAPH-PERF.md`.
    """
    graph = request.app.state.graph
    result = await queries.overview(
        graph.driver,
        limit=payload.limit,
        types=payload.filters.types,
        predicates=payload.filters.predicates,
        min_source_count=payload.filters.min_source_count,
        min_confidence=payload.filters.min_confidence,
        hide_isolated=payload.filters.hide_isolated,
        include_unconfirmed=payload.filters.include_unconfirmed,
    )
    return {
        "nodes": result.nodes,
        "edges": result.edges,
        "truncated": result.truncated,
        "total": result.total,
        "coverage": _coverage(conn, payload.filters.include_unconfirmed),
        "isolated_hidden": result.isolated_hidden,
    }


@router.post("/graph/brief", response_model=BriefSubgraphResponse)
async def graph_brief(
    payload: BriefSubgraphRequest,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> BriefSubgraphResponse:
    """The subgraph induced on the entities one brief names.

    The reading surface's answer to "how does this thing relate to the rest of
    what I just read". Twenty or thirty nodes, which is a graph a person can
    look at -- as opposed to the whole projection, which is a graph a person can
    only navigate.

    Takes a brief id rather than an id list because the brief -> entities
    mapping lives in Postgres and is the only thing that survives a merge; see
    `BriefSubgraphRequest`.
    """
    entities = brief_entities(conn, payload.brief_id)
    if entities is None:
        raise HTTPException(404, detail=f"no brief {payload.brief_id}")

    result = await queries.subgraph(
        request.app.state.graph.driver,
        entities.resolved,
        limit=payload.limit,
        types=payload.filters.types,
        predicates=payload.filters.predicates,
        min_source_count=payload.filters.min_source_count,
        min_confidence=payload.filters.min_confidence,
    )

    present = {node["id"] for node in result.nodes}
    return BriefSubgraphResponse(
        nodes=result.nodes,
        edges=result.edges,
        truncated=result.truncated,
        total=result.total,
        scope=BriefScope(
            brief_id=payload.brief_id,
            mentioned=len(entities.named),
            resolved=len(entities.resolved),
            present=len(present),
            # Named, survived every merge, and still not in the projection --
            # which means the corroboration gate is holding it. Listing the ids
            # rather than only counting them is what makes it actionable from
            # the curation queue.
            missing=[eid for eid in entities.resolved if eid not in present],
            id_cap_reached=len(entities.resolved) > queries.SUBGRAPH_ID_CAP,
        ),
    )


def _coverage(conn: db.Conn, include_unconfirmed: bool = False) -> GraphCoverage:
    """What the graph is NOT showing, and why.

    COUNTED AGAINST THE STATUSES THE CANVAS WAS DRAWN WITH, which is the whole
    reason the flag reaches this far down. A line that always counted against
    `active` alone would report thousands hidden beside a canvas that had just
    drawn them -- the same contradiction the merge-resolution note below records,
    arriving from the other direction.

    A relationship with one endpoint nobody has confirmed cannot appear unless
    the reader asks for it -- measured 2026-08-06 on the live corpus, that was
    371 of 468 known triples. A viewer shown 97 edges with no explanation
    reasonably concludes the extraction is weak. It is not: the assertions exist
    in Postgres and the corroboration gate is holding them back, which is a
    completely different problem with a completely different fix.

    Counted from Postgres because Postgres is the system of record (invariant 1).
    Neo4j cannot answer this question -- the missing entities were never
    projected.

    Merges are resolved first, and a self-loop created by one is dropped, so
    this counts the SAME triples the projector does. The raw form reported 91
    shown against a canvas holding 99 -- a coverage line contradicting the graph
    directly above it.
    """
    # Resolve merges first. The projector collapses a merged endpoint onto its
    # survivor, so counting raw `subject_id_raw` here reported 91 "shown"
    # against a graph holding 99 -- the coverage line contradicting the canvas
    # directly above it.
    from venona.graph.projector import resolve_mappings

    mappings = resolve_mappings(conn)
    status = {r["id"]: r["status"] for r in repo.entity_statuses(conn)}

    triples = {
        (mappings.get(r["s"], r["s"]), r["p"], mappings.get(r["o"], r["o"]))
        for r in repo.distinct_relations(conn)
    }
    triples = {t for t in triples if t[0] != t[2]}
    known = len(triples)
    # `dormant` is absent from both arms deliberately: it is not drawn at either
    # setting, so it belongs on the hidden side of the count at both (ADR 0054).
    drawn = set(queries.WITH_UNCONFIRMED if include_unconfirmed else queries.CONFIRMED_ONLY)
    projected = sum(1 for t in triples if status.get(t[0]) in drawn and status.get(t[2]) in drawn)

    provisional = repo.provisional_count(conn)
    return GraphCoverage(
        relationships_known=known,
        relationships_shown=projected,
        relationships_hidden=known - projected,
        entities_uncorroborated=provisional,
        includes_unconfirmed=include_unconfirmed,
    )


@router.get("/graph/path", response_model=GraphPathResponse)
async def graph_path(
    request: Request,
    source: str = Query(alias="from"),
    target: str = Query(alias="to"),
    max_depth: int = Query(4, ge=1, le=queries.MAX_PATH_DEPTH),
    predicates: list[str] | None = Query(None),
    min_source_count: int = Query(1, ge=1),
    include_unconfirmed: bool = Query(False),
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """Shortest paths between two entities — the motivating query.

    Returns ALL paths of the minimum length (`allShortestPaths`), not the k
    shortest of increasing length. There is deliberately no `k` parameter: it
    would be unimplementable without a weighted traversal.

    THE FILTER RAIL REACHES THIS ONE TOO. The canvas hides what the rail
    excludes, so a route walked over edges it excludes is drawn as a broken
    chain or as nothing at all -- the trace announcing a connection the reader
    cannot see. Omitted rather than empty means "the reader set nothing", which
    is the traversal's own default over the ontology's path predicates.
    """
    if source == target:
        raise HTTPException(400, detail="source and target are the same name")

    result = await queries.shortest_paths(
        request.app.state.graph.driver,
        source,
        target,
        max_depth=max_depth,
        predicates=predicates or None,
        min_source_count=min_source_count,
        include_unconfirmed=include_unconfirmed,
    )

    result["chains"] = [
        describe_path(conn, path["node_ids"], result["edges"]) for path in result["paths"]
    ]
    return result


@router.get(
    "/edges/{edge_id:path}/provenance",
    response_model=EdgeProvenanceResponse,
    # `edge_provenance` falls back to `{id, name}` for an entity Postgres no
    # longer holds, so `type` is absent rather than null on that path.
    response_model_exclude_unset=True,
)
def provenance(edge_id: str, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """Every article that asserted this edge, with the verbatim sentence.

    This is why the graph is trustworthy: an edge you cannot audit is a rumour.
    """
    result = edge_provenance(conn, edge_id)
    if result is None:
        raise HTTPException(404, detail="no sources recorded for this relationship")
    return result


@router.get("/graph/search", response_model=GraphSearchResponse)
async def graph_search(
    request: Request,
    # Bounded, because the fallback arm is a `CONTAINS` scan with no index
    # behind it: the cost of a search is the length of what is typed times the
    # size of the graph, and nothing on the client would ever send more than a
    # search box holds.
    q: str = Query(min_length=1, max_length=128),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    rows = await queries.search_entities(request.app.state.graph.driver, q, limit=limit)
    return {"entities": rows}
