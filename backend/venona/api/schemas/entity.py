"""Contract C8: the card payload.

ONE shape, discriminated by `kind`, feeding the hover card, the brief rail and
the graph node panel. One store, one cache, one TTL, one component. All three
kinds exist from day one even though grounded acronyms don't land until S8 --
adding a third kind later would touch the component, the card, the store and the
token set.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from venona.api.schemas.weburl import SafeWebUrl

CardKind = Literal["entity", "glossary", "undefined"]


class EntityRef(BaseModel):
    id: str
    name: str
    type: str | None = None
    relevance: float = 0.0


class Signal(BaseModel):
    article_id: str
    title: str
    url: SafeWebUrl = None
    publisher: str | None = None
    published_at: str | None = None
    score: int | None = None


class MiniGraphNeighbor(BaseModel):
    id: str
    name: str
    type: str | None = None
    predicate: str
    dir: Literal["in", "out"]
    weight: float = 1.0


class MiniGraph(BaseModel):
    """Precomputed on the server: deterministic angles, no force simulation, no
    graph library on the reading surface. Renders as inline SVG in ~1ms."""

    center: str
    neighbors: list[MiniGraphNeighbor] = Field(default_factory=list)
    more: int = 0


class Grounding(BaseModel):
    """Invariant 3 made visible. A glossary card without this cannot be `active`."""

    quote: str | None = None
    url: SafeWebUrl = None
    article_id: str | None = None
    article_title: str | None = None
    published_at: str | None = None


class Card(BaseModel):
    id: str
    kind: CardKind

    # entity
    type: str | None = None
    name: str | None = None
    expansion: str | None = None
    aliases: list[str] = Field(default_factory=list)
    status: str | None = None
    summary: str | None = None
    signals: list[Signal] = Field(default_factory=list)
    mini_graph: MiniGraph | None = None
    neighbor_count: int = 0

    # glossary / undefined
    surface: str | None = None
    definition: str | None = None
    method: str | None = None
    confidence: float | None = None
    grounding: Grounding | None = None
    # Tier 2 entity definitions are written FROM several evidence sentences
    # rather than quoted from one, so they carry the articles they rest on and
    # no quote. The card renders "drawn from N articles" off this; a synthesis
    # presented as a quotation is the failure invariant 3 exists to prevent.
    definition_sources: list[Signal] = Field(default_factory=list)
    alternatives: list[GlossaryAlternative] = Field(
        default_factory=list,
        description="populated when a surface has >=2 accepted expansions",
    )
    sighting_count: int = 0
    # Undefined cards only. NULL means no web lookup has ever run for this
    # surface; set means one ran and grounded nothing. The card reads it so
    # reopening does not offer a first-time lookup for a question already
    # asked, and links the run id to the trace of what was tried.
    last_attempt_at: str | None = None
    last_attempt_run_id: str | None = None


class GlossaryAlternative(BaseModel):
    expansion: str
    confidence: float
    method: str
    grounding: Grounding | None = None


class CardBatchRequest(BaseModel):
    ids: list[str] = Field(max_length=100)


class CardBatchResponse(BaseModel):
    cards: dict[str, Card]
    missing: list[str] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    name: str
    type: str | None = None
    relevance: float = 0.0
    status: str | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    predicate: str
    source_count: int = 1
    confidence: float = 0.5
    first_seen: str | None = None
    last_seen: str | None = None


class Connection(BaseModel):
    """One row of the entity page's connections list.

    NOT a `GraphEdge`. An edge names two ids and leaves the caller to look both
    up; a connection is read from ONE name's point of view, so it carries the
    other end already named and the direction already resolved. `edge_id` is
    what `GET /edges/{edge_id}/provenance` takes, and it comes from here rather
    than being assembled by the client -- assembled, it is backwards for every
    incoming connection.
    """

    edge_id: str
    predicate: str
    direction: Literal["in", "out"]
    other_id: str
    other_name: str
    other_type: str | None = None
    #: Distinct PUBLISHERS, not articles. One outlet running a story twice is
    #: not two sources, and `edge_provenance` counts the same thing the same way.
    publisher_count: int = 1
    confidence: float = 0.5
    first_seen: str | None = None
    last_seen: str | None = None


class EntityConnections(BaseModel):
    items: list[Connection] = Field(default_factory=list)
    #: The whole set matching the current filter, not the page.
    total: int = 0


class EntitySources(BaseModel):
    items: list[Signal] = Field(default_factory=list)
    total: int = 0


class MergedName(BaseModel):
    """A name that turned out to be this one. Admin only."""

    id: str
    name: str
    type: str | None = None
    reason: str | None = None
    merged_at: str | None = None
    #: The address of whoever ruled on it. NULL is honest and common: the CLI
    #: and the pipeline both merge without a person.
    merged_by: str | None = None


class AliasOrigin(BaseModel):
    """One surface this name is matched on, and where it came from. Admin only."""

    surface: str
    strictness: int = 0
    is_acronym: bool = False
    added_by: str | None = None
    article_id: str | None = None
    article_title: str | None = None
    article_url: SafeWebUrl = None


class EntityRecord(BaseModel):
    """Provenance for one name. Both lists are bounded slices.

    The totals are what keep a cut list honest -- without them a hub name with
    six hundred surfaces would present the first two hundred as all of them, on
    the one screen whose job is to say where a name came from.
    """

    id: str
    merged_from: list[MergedName] = Field(default_factory=list)
    aliases: list[AliasOrigin] = Field(default_factory=list)
    merged_from_total: int = 0
    aliases_total: int = 0


class Neighborhood(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    returned: int = 0
    total: int = 0


Card.model_rebuild()
