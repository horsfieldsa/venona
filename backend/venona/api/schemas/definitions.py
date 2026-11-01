"""Wire models for the definitions queue and the remaining curation actions.

The queue is a different projection of `acronym_definitions` from
`schemas/glossary.py`'s: that one is a flat list of ROWS, this one groups them
by surface and ranks the surfaces by how much they are hurting — contradicted
first, then unusable-until-settled, then a single reading wanting a nod, then
the undefined backlog. Both exist because the queue asks "what should I work on"
and the glossary asks "what do we hold".
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from venona.api.schemas.run import RunStarted
from venona.api.schemas.weburl import SafeWebUrl


class QueueGrounding(BaseModel):
    """Thinner than `glossary.GlossaryGrounding` on purpose: the queue shows a
    quote and a link, and does not need the span or the publisher."""

    quote: str | None = None
    url: SafeWebUrl = None
    article_id: str | None = None


class QueueReading(BaseModel):
    """One candidate meaning, sorted strongest-evidence-first so "fold the
    others into this one" and the digit keys both mean something without the
    curator sorting by eye."""

    id: str
    expansion: str
    method: str | None = None
    confidence: float | None = None
    corroboration_count: int = 1
    status: str
    grounding: QueueGrounding = Field(default_factory=QueueGrounding)


class QueueContradiction(BaseModel):
    """The corpus disagrees with this reading — the case a curator would never
    press "look again" on, because the definition looks settled."""

    expansion: str | None = None
    always_followed_by: str | None = None
    documents: int = 0


class QueueSuggestion(BaseModel):
    """What this surface probably needs, and WHY.

    An object rather than a verdict string, and the `why` is the reason: this
    proposes a destructive-ish action to a curator who has a hundred of these to
    get through, and "same_thing" on its own does not let anyone check the
    reasoning before agreeing with it.
    """

    #: `attach_entity` | `same_thing` | `choose` | `keep_both` | `accept` |
    #: `lookup` — each maps to an endpoint on the glossary router.
    verdict: str
    why: str
    #: `choose` only: which reading the queue would keep.
    entry_id: str | None = None


class QueueItem(BaseModel):
    """One SURFACE and everything known about it."""

    surface: str
    normalized: str
    sightings: int = 0
    articles: int = 0
    #: A web lookup has been tried, whether or not it grounded anything.
    attempted: bool = False
    attempt_count: int = 0
    #: A surface is decided when ANY of its rows carries a verdict — every
    #: verdict stamps all of them, so a half-stamped surface would mean a
    #: decision was interrupted rather than that one reading is pending.
    decided: bool = False
    readings: list[QueueReading] = Field(default_factory=list)
    contradiction: QueueContradiction | None = None
    #: What the queue proposes. Only computed for pending surfaces, so it is
    #: absent on a decided one rather than being a stale recommendation.
    suggestion: QueueSuggestion | None = None


class DefinitionQueue(BaseModel):
    items: list[QueueItem] = Field(default_factory=list)
    total_pending: int = 0
    decided: int = 0


class UnmergeResult(BaseModel):
    """A merge is an `entity_mappings` row, never an `UPDATE` to an assertion
    (invariant 1) — which is exactly what makes this reversible and lossless."""

    merge_id: str
    from_id: str
    active: bool


class RetractionResult(BaseModel):
    """What a withdrawal leaves behind.

    `retracted_at` rather than a deletion, because C1 makes assertions
    append-only: an edge whose evidence turned out not to support it has to stay
    readable, or the panel for a withdrawn claim shows nothing instead of
    showing why it went.
    """

    assertion_id: str
    retracted_at: str
    already_retracted: bool = False


class RebuildResult(BaseModel):
    """`venona graph rebuild` over HTTP: drop the projection and rebuild it from
    the `assertions` table.

    Invariant 1 is what makes this safe — Neo4j holds nothing original, so the
    destructive half costs nothing that cannot be reconstructed.
    """

    rebuilt: bool
    entities: int = 0
    edges: int = 0
    articles: int = 0
    mentions: int = 0
    #: Assertions the predicate whitelist or the corroboration gate refused.
    rejected: int = 0
    pruned: int = 0


class EntityHit(BaseModel):
    """Alias-aware prefix search, backing the path finder and the "this surface
    is an entity" picker."""

    id: str
    name: str
    type: str | None = None
    relevance: float = 0.0
    #: Present ONLY when `include_provisional` is on, so a caller that is about
    #: to promote something can show what it is promoting rather than doing it
    #: silently. Absent for every existing caller, whose shape is unchanged.
    status: str | None = None


class EntitySearchResults(BaseModel):
    entities: list[EntityHit] = Field(default_factory=list)


class DefineStarted(RunStarted):
    """`run_launcher.launch`'s envelope with the entity echoed back.

    INHERITS rather than restating the four envelope fields. Spelled out, a
    field added to `launch` would reach `POST /runs` and be silently filtered
    off this one — which is the exact defect the whole response-model pass was
    about.
    """

    entity_id: str
