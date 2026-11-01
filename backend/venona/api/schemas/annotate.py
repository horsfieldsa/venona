"""Wire models for annotating text venona did not write.

`TextRun` is imported, not redefined. Contract C3 is the annotation wire format
and it has two producers -- a brief and this -- which is the whole reason the
browser extension can reuse `BriefBlock.vue`'s renderer and the app's mention
CSS without a translation layer. A second definition here would be the
first step towards two subtly different ideas of what a run is.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from venona.api.schemas.brief import TextRun

# A block longer than this is not a paragraph, it is a page with its markup
# stripped, and matching it tells the reader nothing about where anything is.
MAX_BLOCK_CHARS = 10_000

# Enough for a long feature with its pull quotes and photo captions. The cap
# exists so one tab cannot ask the API to scan a megabyte.
MAX_BLOCKS = 400
MAX_TOTAL_CHARS = 300_000


class AnnotateBlockIn(BaseModel):
    id: str = Field(max_length=64, description="caller's id, echoed back verbatim")
    text: str = Field(
        max_length=MAX_BLOCK_CHARS,
        description="text exactly as it will be spliced back; NOT normalised by the server",
    )
    # The caller keeps link text in `text` and says where it was, exactly as
    # `markdown_to_blocks` emits `href` marks for the brief. Dropping the text
    # instead -- which the extension did at first -- closes the gap and makes
    # words that are not adjacent on the page adjacent in the string, so the
    # index matches across a link that is not there.
    hrefs: list[tuple[int, int]] = Field(
        default_factory=list,
        max_length=MAX_BLOCK_CHARS,
        description="[start, end) ranges covered by a link; spans inside are suppressed",
    )


class AnnotateRequest(BaseModel):
    blocks: list[AnnotateBlockIn] = Field(max_length=MAX_BLOCKS)
    url: str | None = Field(
        default=None,
        max_length=2048,
        description="diagnostics only. Never stored, never logged with the text.",
    )

    @model_validator(mode="after")
    def _within_total(self) -> AnnotateRequest:
        total = sum(len(block.text) for block in self.blocks)
        if total > MAX_TOTAL_CHARS:
            raise ValueError(f"{total} characters exceeds the {MAX_TOTAL_CHARS} limit")
        return self


class AnnotateBlockOut(BaseModel):
    id: str
    runs: list[TextRun] = Field(description="concatenates to the submitted text, exactly")


class AnnotateResponse(BaseModel):
    blocks: list[AnnotateBlockOut]
    refs: list[str] = Field(
        default_factory=list,
        description="every distinct ref on the page, for one cards:batch round trip",
    )
    index_version: str = Field(description="client cache hint; see annotate_service")
