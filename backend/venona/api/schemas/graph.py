"""Wire models for the graph router.

Every endpoint in `routers/graph.py` is modelled here, and the shape-drift risk
is worth recording: `response_model` **filters**. A field the handler returns and
the model omits does not raise, it silently disappears from the wire, and the
symptom lands in a component that reads it. So check a model field-for-field
against a real response from the live corpus rather than reading it off the
handler.

Generating `web/src/api/schema.d.ts` from these models is what retires the
hand-written mirror in `web/src/types/graph.ts`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from venona.api.schemas.weburl import SafeWebUrl
from venona.graph import queries


class GraphFilters(BaseModel):
    types: list[str] | None = None
    predicates: list[str] | None = None
    min_source_count: int = 1
    min_confidence: float = 0.0
    #: Drop entities with no surviving edge. Honoured by `/graph/overview` ONLY,
    #: and that is not an omission: `/graph/query` returns the seeds plus edge
    #: endpoints, and `/graph/brief` returns a named id set, so in both of those
    #: an "isolated" node is either impossible or is precisely the finding the
    #: view exists to surface. Default False -- the canvas hides them by default
    #: but showing them is one refetch away, and a server-side default would make
    #: that toggle unable to answer.
    hide_isolated: bool = False
    #: Draw names one source has given us, hollow rather than withheld
    #: (ADR 0247). On `GraphFilters` rather than on each request so an expansion
    #: cannot disagree with the overview it expands: a seed drawn hollow whose
    #: one-hop query still filtered it out returns nothing, which reads as an
    #: entity with no neighbours instead of a view set to hide them.
    include_unconfirmed: bool = False


class GraphNode(BaseModel):
    id: str
    name: str
    type: str | None = None
    org_type: str | None = None
    relevance: float = 0.0
    summary: str | None = None
    mention_count: int = 0
    #: `active` or `provisional`. Defaulted rather than required because the
    #: brief subgraph and the path finder stay confirmed-only, so their nodes
    #: carry no status and every one of them is confirmed by construction.
    status: str = "active"


class GraphEdge(BaseModel):
    """`id` is `{source}|{predicate}|{target}` -- deterministic and URL-safe, so
    the frontend can address an edge without a round trip."""

    id: str
    source: str
    target: str
    predicate: str
    #: Distinct PUBLISHERS, not articles.
    source_count: int = 1
    #: Declared structure rather than something read: it names no article, so
    #: `source_count` is a floor artefact and not a publisher count. The canvas
    #: must not dash it and the corroboration filter must not drop it.
    declared: bool = False
    confidence: float = 0.5
    kind: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None


class BriefSubgraphRequest(BaseModel):
    """The BRIEF ID, not an id list.

    The brief -> entities mapping lives in Postgres (`brief_blocks.spans`) and is
    the only thing that survives a merge: a brief written before one names the
    loser, which the projector collapsed onto the survivor. Ids sent from the
    client would silently drop exactly those entities, and the client cannot see
    `entity_mappings`.
    """

    brief_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=queries.SUBGRAPH_EDGE_LIMIT, ge=1, le=queries.SUBGRAPH_EDGE_LIMIT)
    filters: GraphFilters = Field(default_factory=GraphFilters)


class BriefScope(BaseModel):
    """What the brief named, and how much of it the projection can show.

    NOT the corpus-wide `coverage` block the overview returns. "Showing 97 of
    468 known relationships" printed under a twelve-edge canvas describes a
    different population from the one on screen, which is a worse statement than
    silence. This describes THIS brief.
    """

    brief_id: str
    #: Distinct entity refs the brief's spans name.
    mentioned: int
    #: After collapsing merge chains; never more than `mentioned`.
    resolved: int
    #: Of those, active nodes in the projection.
    present: int
    #: Named but not projected: still uncorroborated, or merged away.
    missing: list[str] = Field(default_factory=list)
    id_cap_reached: bool = False


class GraphPayloadResponse(BaseModel):
    """`queries.GraphPayload` on the wire. Shared by `/graph/query` and, with
    coverage added, `/graph/overview`."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    #: `total` exceeded the request's `limit`; the canvas says so rather than
    #: quietly drawing a subset.
    truncated: bool
    total: int


class BriefSubgraphResponse(GraphPayloadResponse):
    scope: BriefScope


class GraphCoverage(BaseModel):
    """What the projection is NOT showing, counted from Postgres.

    Neo4j cannot answer this — the hidden entities were never projected — so
    invariant 1 is doing real work here rather than being observed for its own
    sake. See `routers/graph.py::_coverage`.
    """

    relationships_known: int
    relationships_shown: int
    relationships_hidden: int
    entities_uncorroborated: int
    #: What `relationships_shown` was counted against, so the line and the
    #: canvas cannot drift: with the reader's switch on, a relationship resting
    #: on an unconfirmed name is SHOWN and must stop being counted as hidden.
    includes_unconfirmed: bool = False


class GraphOverviewResponse(GraphPayloadResponse):
    coverage: GraphCoverage
    #: Active entities the `hide_isolated` filter withheld. NOT on
    #: `GraphPayloadResponse`, because `/graph/query` cannot withhold one --
    #: `response_model` filters, so declaring it only here is what keeps that
    #: endpoint's shape honest rather than carrying a field that is always zero.
    #:
    #: The canvas needs it because the filter is SERVER-side: the readout says
    #: "183 unconnected hidden · show", and the client cannot count entities it
    #: was never sent. A view that quietly shows 115 of 298 entities is the thing
    #: this readout exists to prevent.
    isolated_hidden: int = 0


class GraphPath(BaseModel):
    """One shortest path, as ids into the response's own `nodes` and `edges`.

    Ids rather than embedded objects: `allShortestPaths` returns paths that
    overlap heavily, and inlining would send the same hub node once per path.
    """

    node_ids: list[str]
    edge_ids: list[str]


class GraphPathResponse(BaseModel):
    paths: list[GraphPath]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    found: bool
    #: The depth actually searched, clamped to `queries.MAX_PATH_DEPTH` — not
    #: necessarily the depth asked for.
    max_depth: int
    #: One human-readable rendering per path, in `paths` order. Built in
    #: `graph_service.describe_path`, which resolves names from Postgres.
    chains: list[str] = Field(default_factory=list)


class ProvenanceEndpoint(BaseModel):
    """`type` is absent when the edge names an entity Postgres has since dropped —
    `edge_provenance` falls back to `{id, name}`, so this cannot be required."""

    id: str
    name: str
    type: str | None = None


class ProvenanceAssertion(BaseModel):
    """One article's claim that this edge exists, with the sentence it rests on.

    `quote`, `confidence` and `method` are NOT NULL in `assertions`; `model` is
    null for the deterministic methods, and the document columns are null when
    the article has been pruned.
    """

    assertion_id: str
    article_id: str
    quote: str
    confidence: float
    method: str
    model: str | None = None
    title: str | None = None
    url: SafeWebUrl = None
    publisher: str | None = None
    published_at: str | None = None


class ProvenanceBrief(BaseModel):
    id: str
    title: str | None = None
    date: str | None = None
    generated_at: str | None = None


class EdgeProvenanceResponse(BaseModel):
    """Why an edge is believable. An edge you cannot audit is a rumour."""

    edge_id: str
    predicate: str
    source: ProvenanceEndpoint
    target: ProvenanceEndpoint
    #: Distinct PUBLISHERS, matching `GraphEdge.source_count`.
    source_count: int
    assertion_count: int
    #: Declared structure rather than something read from an article. Never
    #: `single_source`, because it makes no claim about publishers at all.
    declared: bool = False
    #: Drawn dashed on the canvas, so panel and canvas agree on strength.
    single_source: bool
    assertions: list[ProvenanceAssertion] = Field(default_factory=list)
    briefs: list[ProvenanceBrief] = Field(default_factory=list)


class EntitySearchHit(BaseModel):
    """`degree` and `score` ride along because a name alone does not tell you
    which `Lattice` you meant."""

    id: str
    name: str
    type: str | None = None
    summary: str | None = None
    mention_count: int = 0
    degree: int = 0
    score: float = 0.0


class GraphSearchResponse(BaseModel):
    entities: list[EntitySearchHit] = Field(default_factory=list)
