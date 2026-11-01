"""Wire models for personal relevance: emphasis, follows, the personal list.

The reader-safe rubric projection lives here too: axis ids and descriptions
are what the model was asked (publishable, per `rubric_fingerprint`'s split);
the prose relevance profile is operator strategy and stays admin.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from venona.api.schemas.brief import Brief
from venona.api.schemas.weburl import SafeWebUrl


class RubricAxis(BaseModel):
    """The reader's view of one axis. The rubric's `description` is the
    MODEL's scoring contract ("Score 5 for...") and is deliberately absent
    here: quoted at a reader it reads as someone else's config file, which is
    exactly how the first welcome screen failed its first user test."""

    id: str
    #: Plain name for the tailor screens ("Money and contracts"). Falls back
    #: to the id's words client-side when the rubric omits it.
    label: str | None = None
    #: One reader-answerable sentence: what raising this brings more of.
    plain: str | None = None
    default_weight: float


class RubricMeta(BaseModel):
    """What a reader may tune: the axes, and the words they may say about
    each. Served like the ontology -- a pure projection of loaded config, so
    the tailor screen is built from the instance's rubric rather than a
    hardcoded frontend list."""

    axes: list[RubricAxis] = Field(default_factory=list)
    emphasis_levels: list[str] = Field(default_factory=list)


class RelevanceSettings(BaseModel):
    #: {axis_id: "more" | "normal" | "less"}. Empty when the reader has never
    #: tailored (instance defaults apply).
    emphasis: dict[str, str] = Field(default_factory=dict)
    #: Whether a stored row exists at all -- the first-visit invitation keys
    #: off this, not off the emphasis being non-default.
    tailored: bool = False
    #: The stored emphasis references axes absent from the current rubric, so
    #: instance defaults are in effect; the settings page says so.
    drifted: bool = False


class RelevanceSettingsUpdate(BaseModel):
    """Full replacement, not a merge: the tailor screen submits the whole
    emphasis map, so an absent axis means "normal", not "keep"."""

    model_config = ConfigDict(extra="forbid")

    emphasis: dict[str, str] | None = None
    #: True deletes the row -- an absent row IS the instance defaults, so
    #: reset is deletion rather than a second spelling of "default".
    reset: bool = False


class FollowEntry(BaseModel):
    entity_id: str
    #: The id after merge resolution -- what the name belongs to today.
    resolved_id: str
    name: str | None = None
    type: str | None = None
    status: str | None = None
    created_at: str


class FollowList(BaseModel):
    """What one reader follows. `items` is bounded; `total` is not.

    Returned by the mutations as well as the read, so the client can replace its
    list from any of the three without a second request.
    """

    items: list[FollowEntry] = Field(default_factory=list)
    total: int = 0


class FollowSuggestion(BaseModel):
    id: str
    name: str
    type: str
    mention_count: int = 0
    degree: int = 0


class FollowSuggestions(BaseModel):
    items: list[FollowSuggestion] = Field(default_factory=list)


class ForYouItem(BaseModel):
    """One window cluster, scored for THIS reader. Derived at read time from
    the shared analysis -- zero model calls on this path."""

    cluster_id: str
    headline: str
    url: SafeWebUrl = None
    publisher: str | None = None
    published_at: str | None = None
    overall: int = 0
    tier: str = "background"
    #: The stored why-sentence from the shared analysis; the templated
    #: evidence, not per-reader prose.
    why: str | None = None
    so_what: str | None = None
    #: Names this cluster mentions that the reader follows.
    follows: list[str] = Field(default_factory=list)


class ForYou(BaseModel):
    #: The date of the request, not of the document. Its only reader is the
    #: empty state, which has no document to take a date from.
    date: str
    tailored: bool = False
    drifted: bool = False
    #: Followed-mention items first, then the rest -- both halves ordered by
    #: the reader's own overall.
    items: list[ForYouItem] = Field(default_factory=list)
    #: The reader's current personal brief, whatever day it was written on.
    brief: Brief | None = None
    #: The document exists but was generated from different inputs: the reader
    #: edited emphasis or follows since. The UI offers regeneration instead of
    #: presenting it as current.
    stale: bool = False
    #: The document exists but predates the newest shared brief, so there is
    #: reading in it the reader's own has never seen. Separate from `stale`,
    #: which is about the reader's inputs moving rather than the reading.
    outdated: bool = False
    #: Generations left today under the per-reader cap.
    generations_left: int = 0
    #: A run writing this reader's brief is in flight right now. Drives the
    #: waiting state, and survives a reload -- which local component state,
    #: the only thing that knew this before, could not.
    generating: bool = False
    #: Opening the reading surface should start one generation, unasked. The
    #: server decides eligibility and the client acts on it, so the GET stays
    #: at zero model calls and spend stays behind the POST.
    auto_due: bool = False
