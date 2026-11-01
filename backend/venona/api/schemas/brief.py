"""Contract C3 wire models. Source of truth for the generated TypeScript.

Runs are pre-split server-side. Do NOT "optimise" this back to offsets: Python
offsets are code points and JavaScript's are UTF-16 units, so one astral-plane
character in a headline would shift every highlight in the block -- and the bug
would present as a model error.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from venona.api.schemas.weburl import SafeWebUrl

RunKind = Literal["entity", "glossary", "undefined"]
BlockType = Literal["heading", "paragraph", "list_item", "blockquote", "code"]


class TextRun(BaseModel):
    """One span of text with at most one entity reference and flat marks.

    Field names are short because a 500-mention brief is the payload case the
    runs-over-offsets trade accepted a ~1.4x cost for.
    """

    t: str = Field(description="verbatim text; runs concatenate to the block text")
    r: str | None = Field(default=None, description="entity or glossary id")
    k: RunKind | None = Field(default=None, description="reference kind")
    c: float | None = Field(default=None, description="match confidence 0-1")
    u: bool | None = Field(
        default=None,
        description="uncorroborated: one publisher has said this exists and nothing "
        "has confirmed it. A modifier on `k`, never a fourth kind.",
    )
    b: bool | None = None
    i: bool | None = None
    code: bool | None = None
    s: bool | None = None
    href: str | None = None

    model_config = {"extra": "forbid"}


class BriefBlock(BaseModel):
    id: str
    type: BlockType
    depth: int = 0
    runs: list[TextRun]


ItemState = Literal["new", "follow_up", "ongoing"]


class BriefItem(BaseModel):
    """One story. Facts and judgement are separate FIELDS, not separate
    paragraphs of one blob -- that separation is the whole format.

    Attribution is joined from `documents`, never restated by the model, so a
    claim cannot be hung on the wrong outlet.
    """

    id: str
    headline: str
    #: The headline as annotated runs -- same shape as a block's, concatenating
    #: back to `headline` exactly. Computed at READ time against the current
    #: alias index (the extension's path), so the whole archive carries it and
    #: nothing stored is rewritten. Empty when annotation was unavailable; the
    #: client falls back to the plain string.
    headline_runs: list[TextRun] = Field(default_factory=list)
    state: ItemState = "new"
    state_note: str | None = Field(default=None, description="what is NEW, for follow_up/ongoing")
    publisher: str | None = None
    url: SafeWebUrl = None
    published_at: str | None = None
    age_hours: int | None = Field(
        default=None, description="age at generation time; renders as the 21H badge"
    )
    summary: list[BriefBlock] = Field(
        default_factory=list, description="what the article reports. Annotated."
    )
    why: list[BriefBlock] = Field(
        default_factory=list, description="the judgement. Annotated identically."
    )


class BriefSection(BaseModel):
    """`blocks` is retained for briefs written before the item format and is
    empty for every new one. Removing it would break the archive."""

    heading: str | None
    blocks: list[BriefBlock] = Field(default_factory=list)
    items: list[BriefItem] = Field(default_factory=list)


class BriefTop(BaseModel):
    item_id: str
    line: str
    age_hours: int | None = None


class Freshness(BaseModel):
    """The ledger under the masthead. Answers "is this a live brief or a
    rehash?" before the reader has read a word."""

    under_24h: int = 0
    one_to_three_days: int = 0
    older: int = 0
    total: int = 0


class BriefSource(BaseModel):
    article_id: str
    title: str
    url: SafeWebUrl = None
    publisher: str | None = None
    published_at: str | None = None


class Brief(BaseModel):
    id: str
    date: str
    run_id: str | None = None
    title: str
    #: `headline_runs`' twin for the brief's own title.
    title_runs: list[TextRun] = Field(default_factory=list)
    dek: str | None = None
    generated_at: str
    freshness: Freshness = Field(default_factory=Freshness)
    top: list[BriefTop] = Field(default_factory=list)
    sections: list[BriefSection]
    entities: list[str] = Field(
        default_factory=list,
        description="distinct entity ids, for a single cards:batch prefetch",
    )
    glossary: list[str] = Field(default_factory=list)
    unresolved_mentions: list[str] = Field(
        default_factory=list,
        description="written about but not in the graph yet; a curation backlog",
    )
    sources: list[BriefSource] = Field(default_factory=list)
    #: The adjacent briefs in archive order (date DESC, generated_at DESC),
    #: computed server-side. The reader's prev/next arrows read these rather
    #: than scanning the archive index, so a deep link works however little of
    #: that index the client has loaded.
    older_id: str | None = None
    newer_id: str | None = None


class BriefSummary(BaseModel):
    id: str
    date: str
    title: str
    dek: str | None = None
    #: How many BLOCKS the brief renders — paragraphs and list items, not
    #: entities. The archive column has always been labelled "blocks"; the
    #: field name is what disagreed with it.
    block_count: int = 0
    run_id: str | None = None
    # Several briefs can share a `date`, so the archive needs the time to tell
    # them apart. Optional because pre-0014 rows predate the guarantee.
    generated_at: str | None = None


class BriefList(BaseModel):
    items: list[BriefSummary]
    total: int


# ── search over brief content ────────────────────────────────────────


class BriefSearchMatch(BaseModel):
    """One place inside a brief that matched.

    `where` says which part matched, because the answer changes what the reader
    should do: a `title` match means the brief is about it, a `body` match means
    one item mentions it in passing.
    """

    where: Literal["title", "dek", "headline", "body"]
    #: The item the match sits in, when it is inside one. `None` for a title or
    #: dek match, which belong to the brief rather than to any item.
    item_id: str | None = None
    headline: str | None = None
    #: Plain text, windowed around the hit. Never markup -- the archive renders
    #: it as text, and C3's "the client never parses markdown" applies here too.
    snippet: str


class BriefSearchHit(BaseModel):
    brief: BriefSummary
    #: Capped per brief, so one brief that says a word forty times cannot crowd
    #: out the other nine. `match_count` is the true total.
    matches: list[BriefSearchMatch] = Field(default_factory=list)
    match_count: int = 0


class BriefSearchResults(BaseModel):
    """Deliberately echoes the query it answered.

    The archive filter is debounced, so a slow response can land after the one
    for a longer query. Echoing lets the client drop a stale answer instead of
    rendering results for a string the user has already changed.
    """

    query: str = ""
    entity: str | None = None
    items: list[BriefSearchHit] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False


class RewriteRequest(BaseModel):
    """Which brief `POST /briefs/rewrite` is rewriting.

    Optional, and absent means "the latest brief" -- the CLI and any caller that
    has no view state should not have to look one up to press the same button.
    The web client always sends the brief ON SCREEN, which is not the same
    thing: `Rewrite` sits on `BriefView`, and that view renders archived briefs
    too.

    The id names whose claim on its clusters to release, nothing more. A rewrite
    ADDS a brief and deletes none, so this is not a destructive parameter and a
    wrong id costs a run that finds nothing rather than a brief that is gone.
    """

    brief_id: str | None = Field(
        default=None,
        description="brief to rewrite; omit for the latest",
    )
