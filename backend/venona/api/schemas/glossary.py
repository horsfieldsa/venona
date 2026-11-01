"""Wire models for `routers/glossary.py` — the acronym definitions queue.

Invariant 3 is the thing to keep in view here: a definition exists only if it is
traceable to a sentence in a document. So `GlossaryGrounding` is not decoration,
it is the evidence, and an entry whose `expansion` is set while its grounding is
empty is a bug rather than a sparse row.

Most fields are optional and that is the data, not caution. An `undefined` row —
a surface seen often enough to be worth defining and never successfully defined
— is the queue's most common shape, and it carries a `surface` and counters and
almost nothing else.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from venona.api.schemas.run import RunStarted
from venona.api.schemas.weburl import SafeWebUrl


class GlossaryGrounding(BaseModel):
    """Where a definition came from. Empty on an undefined row.

    A `glossary` card with an expansion and no quote violates invariant 3 (see
    contract C8), which is why nothing here is allowed to be synthesised: the
    quote is verbatim, and the tier-3 checks are all about it containing the
    expansion.
    """

    quote: str | None = None
    #: The grounding url, falling back to the source document's own url.
    url: SafeWebUrl = None
    article_id: str | None = None
    article_title: str | None = None
    publisher: str | None = None
    #: Where the defining sentence sits in the source document. Written for
    #: every tier-1 grounding since it shipped and read back by nothing until
    #: it was served — which is how it was found to be silently discarded on
    #: corroboration. `Any` because it is stored as opaque JSON.
    span: Any | None = None


class GlossaryEntry(BaseModel):
    """One row of `acronym_definitions` — one READING of one surface.

    A surface with two entries is ambiguous, and the card lists both rather than
    picking. Picking would be a silent, unrecoverable error.
    """

    id: str
    surface: str
    expansion: str | None = None
    #: Set when the surface turned out to name an entity rather than expand to
    #: a phrase. The row is retired at the same time.
    entity_id: str | None = None
    #: `in_article` | `web_grounded` | `manual`. Distinguishes a QUOTATION from
    #: a synthesis, and the card must not blur them (contract C8).
    method: str | None = None
    confidence: float | None = None
    #: How many independent sources produced this expansion. Rendered beside the
    #: confidence, because "0.55" alone invites the question this answers.
    corroboration_count: int = 1
    #: `active` | `ambiguous` | `undefined` | `rejected`.
    status: str
    sighting_count: int = 0
    article_count: int = 0
    #: NULL means nobody has ruled on this row — honestly different from
    #: "reviewed and left alone", and what makes a rejection durable.
    decided_at: str | None = None
    #: WHO ruled on it, as an address. NULL alongside a set `decided_at` means
    #: the CLI or the pipeline decided, which is the same honest NULL every
    #: actor column in this app carries (C1) — and it is what every row decided
    #: before this field was written carries, because backfilling a name onto a
    #: verdict nobody signed would be inventing provenance.
    decided_by_email: str | None = None
    #: NULL means no web lookup has ever run for this surface. Set means one ran
    #: and grounded nothing, which is what stops the card offering a first-time
    #: lookup for a question already answered.
    last_attempt_at: str | None = None
    last_attempt_run_id: str | None = None
    attempt_count: int = 0
    grounding: GlossaryGrounding = Field(default_factory=GlossaryGrounding)


class GlossaryList(BaseModel):
    """The review queue. Grounded entries first, then frequency — sorting by
    frequency alone buried the definitions worth reviewing under the undefined
    backlog."""

    items: list[GlossaryEntry] = Field(default_factory=list)
    #: Row count per status over the WHOLE table, for the queue's tabs — not
    #: the page, and not the filtered set.
    counts: dict[str, int] = Field(default_factory=dict)
    #: How many rows match the current filter, whole set. What tells the client
    #: whether scrolling has more to fetch.
    total: int = 0


class GlossarySurface(BaseModel):
    """Every accepted reading of one surface."""

    surface: str
    #: More than one LIVE reading. Counting rejected ones too meant a surface a
    #: curator had just settled still reported itself ambiguous, forever.
    ambiguous: bool
    entries: list[GlossaryEntry] = Field(default_factory=list)


class CorpusContradiction(BaseModel):
    """A tier-3 definition the corpus disagrees with."""

    surface: str
    expansion: str | None = None
    documents: int = 0
    #: What the surface is ALWAYS followed by instead — the evidence that it is
    #: a name rather than an abbreviation.
    always_followed_by: str | None = None
    share: float | None = None


class CorpusAudit(BaseModel):
    """The automatic half of the curation loop. It reports; it changes nothing.

    Absence of the expansion is deliberately NOT treated as evidence — most
    definitions have no occurrence of their expansion and most of those are
    right. See `services/corpus_check.py`.
    """

    checked: int
    contradicted: list[CorpusContradiction] = Field(default_factory=list)
    corroborated: int = 0
    unsupported: int = 0


class CorroborateResult(BaseModel):
    """Re-weighing every tier-3 definition against the corpus as it stands.

    Free and deterministic: no model calls, one pass over `documents`.
    Confidence only ever RISES here.
    """

    checked: int
    definitions_raised: int
    sources_added: int
    #: True when neither the corpus nor the definitions have moved since the
    #: last pass, so there was nothing a second reading could find. Reported
    #: rather than hidden: "checked 0" with no explanation reads as a failure.
    skipped: bool = False


class AttachedEntity(BaseModel):
    id: str
    name: str
    type: str | None = None


class AttachEntityResult(BaseModel):
    """This surface names an ENTITY, not an acronym.

    The first user-facing alias write in the codebase: it writes the alias,
    makes the entity indexable, and retires the glossary row so re-grounding
    cannot reinstate it.
    """

    surface: str
    entity: AttachedEntity
    #: A provisional or dormant entity is promoted to `active`, because a
    #: provisional entity's aliases never enter the automaton — which is
    #: precisely why the surface never resolved.
    promoted: bool
    definitions_retired: int


class NotAnAcronymResult(BaseModel):
    """Both halves are needed and neither is sufficient: the stoplist entry stops
    `detect()` counting the token again, and the rejection settles what is
    already on screen."""

    surface: str
    stoplisted: bool
    definitions_rejected: int


class KeepBothResult(BaseModel):
    """These readings are all real. The surface stays ambiguous ON PURPOSE —
    that is what lets one acronym render differently in two briefs instead of
    picking one and being wrong half the time."""

    surface: str
    kept: list[str] = Field(default_factory=list)
    entries: list[GlossaryEntry] = Field(default_factory=list)


class SameThingResult(BaseModel):
    """These readings are ONE meaning spelled differently.

    Distinct from `choose`: the losers are not wrong, they are duplicates, so
    their SOURCES are folded into the survivor and raise its confidence
    honestly. Their quote and url are deliberately not copied — a sentence
    spelling "Fiscal Years" is not evidence for the string "Fiscal Year".
    """

    surface: str
    kept: str | None = None
    collapsed: list[str] = Field(default_factory=list)
    #: Distinct sources the survivor carries after the collapse.
    sources: int = 0
    entries: list[GlossaryEntry] = Field(default_factory=list)


class ChooseResult(BaseModel):
    """One meaning wins, the rest are rejected — atomically.

    Rejecting rather than deleting is deliberate: a rejected row still carries
    the sentence that grounded it, so "why is this not the obvious reading" has
    an answer six months on, and a re-lookup does not pay to rediscover it.
    """

    surface: str
    chosen: str
    rejected: list[str] = Field(default_factory=list)
    entries: list[GlossaryEntry] = Field(default_factory=list)


class ReopenResult(BaseModel):
    """Takes back a verdict. Deliberately refuses a surface attached to an
    entity — that wrote an alias and promoted an entity, which is a different
    operation to undo."""

    surface: str
    reopened: int
    entries: list[GlossaryEntry] = Field(default_factory=list)


class LookupStarted(RunStarted):
    """`run_launcher.launch`'s envelope with the surface echoed back.

    202: a lookup is a search call plus up to three page fetches plus a read,
    which is well past any sane request timeout. Progress goes through the run
    event log, so the client gets `/api/runs/{id}/stream` for free.

    INHERITS the envelope rather than restating it — see `DefineStarted`.
    """

    surface: str
