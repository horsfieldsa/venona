"""Wire shapes for the learning surface (epic O, docs/adr/0157).

The answer side of a card is the C8 `Card` from `schemas.entity`, unchanged:
one skeleton for hover, merge review and study, and a tier-2 synthesis
structurally cannot render as a quotation because the shape it arrives in
carries no quote.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from venona.api.schemas.entity import Card, Grounding


class DeckCard(BaseModel):
    """One servable card, format already chosen for this showing."""

    card_key: str
    namespace: str  # def | gloss | rel | q
    format: str  # flashcard | choice | cloze
    #: The face shown before reveal: an entity name, a gloss surface, a
    #: definition (choice), a blanked quote (cloze), or a question prompt.
    front: str
    #: choice format only; the correct entry is present and the order is
    #: deterministic per showing.
    choices: list[str] | None = None
    #: The correct choice / the blanked surface / the stored answer. None for
    #: a plain flashcard, where the reveal IS the card below.
    answer: str | None = None
    #: The C8 answer card, when the fact is an entity or gloss.
    card: Card | None = None
    #: Provenance for facts that carry their own quote (relations, generated
    #: questions).
    grounding: Grounding | None = None
    entity_id: str | None = None
    #: Scheduling context: absent on a new card (no state row exists yet).
    due: str | None = None
    state: str | None = None
    reps: int = 0


class QueueCounts(BaseModel):
    due: int
    new_remaining: int
    reviewed_today: int
    new_today: int
    #: Ungraded servable facts across the whole deck -- what "keep studying"
    #: can truthfully offer beyond the daily pace.
    deck_remaining: int


class LearningQueue(BaseModel):
    due: list[DeckCard]
    new: list[DeckCard]
    counts: QueueCounts
    streak: int
    daily_goal: int


class ReviewRequest(BaseModel):
    """`extra="forbid"` is the privacy property made visible: the subject of a
    review is always the acting user, and a smuggled `user_id` is a 422
    rather than a field someone has to remember to ignore."""

    model_config = ConfigDict(extra="forbid")

    card_key: str
    rating: int = Field(ge=1, le=4)


class ReviewResult(BaseModel):
    card_key: str
    state: str
    due: str
    interval_days: float
    reps: int
    lapses: int


class SeriesDay(BaseModel):
    day: str
    reviews: int
    #: Non-new reviews, the retention denominator; a first meeting is not a
    #: memory test.
    graded: int
    lapsed: int
    retention: float | None = None


class TypeCoverage(BaseModel):
    #: An ontology entity type, or the pseudo-type `gloss`.
    type: str
    total: int
    started: int
    strong: int


class LearningProgress(BaseModel):
    series: list[SeriesDay]
    series_days: int
    coverage: list[TypeCoverage]
    streak: int
    reviewed_today: int
    daily_goal: int
    #: The stability bar (days) behind every `strong` count.
    strong_stability_days: float


class LearningCounts(BaseModel):
    """The brief's nudge: enough to say "N cards are ready", cheaply -- no
    card is derived answering this."""

    due: int
    new_available: int
    reviewed_today: int
    daily_goal: int


class OverviewCard(BaseModel):
    """One held card, with its MEANING beside it -- the terms page is a
    personal glossary, not a scheduler ledger."""

    card_key: str
    term: str
    #: Entity type, `gloss`, `relation`, `question` -- or None for a dormant
    #: card whose source row vanished; the page labels it rather than hiding
    #: real state.
    type: str | None = None
    #: The definition / expansion / relationship-in-words / answer.
    detail: str | None = None
    entity_id: str | None = None
    due: str
    stability: float
    lapses: int


class LearningOverview(BaseModel):
    """The three questions a reader actually asks of their own cards."""

    #: Weak, or forgotten at least once -- worth another look; the schedule
    #: presses on these soonest anyway.
    attention: list[OverviewCard]
    #: What comes back next, of the cards that are doing fine.
    upcoming: list[OverviewCard]
    #: Held strongly, strongest first: the payoff list.
    known: list[OverviewCard]
    total: int


class LearningSettings(BaseModel):
    new_per_day: int = Field(ge=0, le=100)
    daily_goal: int = Field(ge=0, le=500)
    retention_target: float = Field(ge=0.7, le=0.98)


class LearningSettingsUpdate(BaseModel):
    """Partial, like `DisplayNameUpdate`: absent means keep."""

    model_config = ConfigDict(extra="forbid")

    new_per_day: int | None = Field(default=None, ge=0, le=100)
    daily_goal: int | None = Field(default=None, ge=0, le=500)
    retention_target: float | None = Field(default=None, ge=0.7, le=0.98)


# ── the question bank (admin) ────────────────────────────────────────


class QuestionRow(BaseModel):
    id: str
    kind: str  # choice | cloze | qa
    prompt: str
    choices: list[str] | None = None
    answer: str
    entity_id: str | None = None
    grounding_article_id: str
    grounding_quote: str
    status: str
    run_id: str
    created_at: str


class QuestionPage(BaseModel):
    items: list[QuestionRow]
    total: int


class DecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject", "retire"]


class QuestionDecision(BaseModel):
    id: str
    status: str
