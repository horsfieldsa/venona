"""Wire models for the curation queue.

The merge mechanism has existed since S8 -- `services/curate.py` has
`merge_entities`, `dismiss_conflict`, `unmerge`, all reversible and all tested --
but it was reachable only from the CLI. These are the shapes that put it behind
the API so a curator can work the queue with the evidence on screen.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# `anchor` is a PROPOSAL rather than a resolver finding: `pipeline/anchors.py`
# files one for an entity that connects to nothing. It shares this queue on
# purpose: the Postgres baseline carries one `alias_conflicts` table and no
# second queue, because two queues for one decision ("is this pair related,
# and how") is one too many.
ConflictKind = Literal[
    "duplicate",
    "collision",
    "containment",
    "resolver_error",
    "anchor",
    "cross_type",
    "type_conflict",
]
Resolution = Literal["merged", "kept_apart", "related"]


class Conflict(BaseModel):
    """One pair the resolver declined to merge on its own.

    `kind` is the organising signal, not decoration: a `duplicate` wants a
    merge, a `containment` is usually a real parent/subsidiary distinction that
    should stay apart, and a `resolver_error` is our bug rather than a curation
    decision at all.
    """

    id: str
    kind: ConflictKind | None = None
    surface: str
    entity_type: str
    existing_entity_id: str
    existing_name: str | None = None
    proposed_entity_id: str
    proposed_name: str | None = None
    article_id: str | None = None
    created_at: str
    resolved: bool = False
    resolution: Resolution | None = None
    resolved_at: str | None = None
    #: WHO decided, as an email, or None. Migration 0002 added the column and
    #: nothing surfaced it, which made the audit trail write-only: a verdict nobody
    #: can attribute is worth about as much as one nobody recorded.
    #:
    #: None is honest and common. It means the CLI decided, or the pipeline did, or
    #: the row predates anyone being asked to sign. Never backfilled -- putting a
    #: name on a verdict nobody signed would invent provenance, which invariant 3
    #: forbids everywhere else.
    #:
    #: An EMAIL rather than the `users.id` the column stores. The id is internal
    #: and means nothing on screen; a curator wants to know who, and the reader of
    #: a shared corpus is exactly the person for whom "somebody decided this"
    #: is not an answer.
    resolved_by: str | None = None
    # What the pipeline proposed, for the curator to confirm or override. Never
    # applied automatically -- structural inference was measured 2026-08-06 at ~50% wrong,
    # so this reads the names instead and still asks.
    suggestion: Literal["merge", "part_of", "distinct"] | None = None
    suggestion_note: str | None = None
    suggestion_child: str | None = None
    #: The predicate `part_of` actually meant, translated through the ontology.
    #: NULL when several are legal for this pair of types -- the UI then shows
    #: the choices with nothing preselected rather than guessing PART_OF, which
    #: the API refuses anyway.
    suggestion_predicate: str | None = None


class ConflictList(BaseModel):
    items: list[Conflict]
    counts: dict[str, int] = Field(default_factory=dict, description="open conflicts per kind")
    total_open: int = 0


class RelateRequest(BaseModel):
    """Keep two entities apart AND record how they relate.

    The third verdict. `containment` is by far the largest queue and it is named
    for the shape it detects -- one name contains the other -- which in practice
    almost always means a parent/child structure: `Air Force Test Pilot School`
    is PART_OF `Air Force`, `GDIT` is PART_OF `General Dynamics`. Merging them
    would be wrong and keeping them apart discards the very fact the conflict
    surfaced.

    Direction is the caller's to state, because it is the thing most easily got
    backwards: the CHILD is the subject. `check_relation` rejects it inverted.
    """

    subject_id: str = Field(description="the child / the acting side")
    object_id: str = Field(description="the parent / the acted-upon side")
    #: REQUIRED. It defaulted to PART_OF, which made the server guess the one
    #: thing only the curator knows -- and PART_OF is precisely the predicate
    #: that is wrong whenever the parent is a Country or a Person. A caller with
    #: no predicate should get a 422 it can read, not a plausible edge.
    predicate: str = Field(min_length=1, description="an ontology predicate name")


class PredicateOption(BaseModel):
    name: str
    description: str | None = None
    symmetric: bool = False


class CorrectTypeRequest(BaseModel):
    """The kind the thing actually is. The server picks or mints the entity."""

    entity_type: str = Field(description="one of this instance's entity types")


class CorrectTypeResponse(BaseModel):
    survivor_id: str
    minted: bool = Field(description="true when no entity of that kind existed and one was created")
    settled_conflict_ids: list[str] = Field(default_factory=list)


class MergeRequest(BaseModel):
    """`merge_id` and `now` are deliberately absent -- the server generates both.

    `services/curate.py` takes them as arguments because it sits under the
    determinism lint, not because a caller should choose them. Letting a client
    supply a merge id would let two merges collide on the `entity_mappings`
    primary key.
    """

    from_id: str = Field(description="the loser; becomes status='merged'")
    to_id: str = Field(description="the survivor")
    reason: str = "curated"


class Merge(BaseModel):
    from_id: str
    to_id: str
    merge_id: str
    active: bool
    reason: str | None = None
    created_at: str
    from_name: str | None = None
    to_name: str | None = None
    #: Who merged them, as an email, or None. See `Conflict.resolved_by`.
    merged_by: str | None = None
    #: Every conflict this merge answered, not only the row the curator clicked.
    #:
    #: One real-world pair is routinely filed as several rows, because the
    #: resolver files one per SURFACE: `prog:orca-xluuv` and its uncrewed
    #: spelling arrived twice, under the acronym-generated `OELUUV` and
    #: `OELU2V`. The server has always settled all of them; the client had no
    #: way to know which, so it dropped one and left the rest on screen, where
    #: clicking one returned "already the same entity" -- a decision the curator
    #: had just made, handed back as an error.
    #:
    #: Empty when the merge came from somewhere with no queue behind it, which
    #: is why it defaults rather than being required.
    settled_conflict_ids: list[str] = Field(default_factory=list)


class MergeList(BaseModel):
    """One page of the merge history, newest first."""

    items: list[Merge] = Field(default_factory=list)
    #: Every merge matching `active_only`, whole set — what tells the client
    #: whether scrolling has more to fetch.
    total: int = 0


class ConflictOptions(BaseModel):
    """Which predicates are well-typed for THIS pair, in each direction.

    Computed from the ontology rather than hardcoded, so the options a curator
    sees and the rules `check_relation` enforces cannot disagree.
    """

    forward: list[PredicateOption] = Field(default_factory=list)
    reverse: list[PredicateOption] = Field(default_factory=list)
    subject_type: str | None = None
    object_type: str | None = None


class CurationStatus(BaseModel):
    """What is outstanding, and whether the graph has caught up.

    `pending_rebuild` exists because a merge lands in Postgres immediately but
    reaches Neo4j only on the next projection. Without surfacing it, the graph
    silently disagrees with the curation view for as long as the curator keeps
    working -- which is exactly when they are most likely to look at it.
    """

    open_conflicts: int = 0
    by_kind: dict[str, int] = Field(default_factory=dict)
    undefined_definitions: int = 0
    ambiguous_definitions: int = 0
    # SURFACES awaiting a decision -- the same unit `GET /curation/definitions`
    # queues. The two counts above are rows, and a row count cannot agree with a
    # queue whose unit is a surface: the tab read "543" beside a queue saying
    # "742 surfaces to decide", which is two numbers for one thing.
    pending_definitions: int = 0
    pending_rebuild: int = Field(default=0, description="changes a rebuild would apply right now")
    blocked_relations: int = Field(
        default=0,
        description=(
            "curated relationships that cannot project because an endpoint is "
            "uncorroborated. Fixed by corroboration, not by a rebuild."
        ),
    )
    run_active: bool = False
