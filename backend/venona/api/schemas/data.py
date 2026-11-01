"""Data-management wire types. snake_case (Conventions)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

#: What the caller must send to prove they meant it. A boolean `confirm: true`
#: is one typo in a curl command away from an empty corpus; a phrase has to be
#: assembled on purpose. The UI asks the operator to type it.
CONFIRM_PHRASE = "delete everything"


class PurgeRequest(BaseModel):
    """The only destructive request the API accepts."""

    confirm: str = Field(description=f"must be exactly {CONFIRM_PHRASE!r}")

    @field_validator("confirm")
    @classmethod
    def _exact(cls, value: str) -> str:
        # Case- and whitespace-tolerant, because the operator is typing it under
        # a red warning and a trailing space should not read as "wrong phrase,
        # try again". Nothing else is tolerated.
        if value.strip().lower() != CONFIRM_PHRASE:
            raise ValueError(f"type {CONFIRM_PHRASE!r} to confirm")
        return CONFIRM_PHRASE


class DataStats(BaseModel):
    """What is on disk, so the confirmation names real numbers.

    A dialog that says "this cannot be undone" is a warning; one that says "this
    deletes 2,312 entities and 11 briefs" is information, and it is the
    difference between the operator confirming a sentence and confirming a
    decision.
    """

    counts: dict[str, int] = Field(default_factory=dict)
    total: int = 0
    preserved: list[str] = Field(default_factory=list)


class PurgeResult(BaseModel):
    deleted: dict[str, int] = Field(default_factory=dict)
    total: int = 0
    #: The graph is a projection, so it is cleared rather than deleted. False
    #: when Neo4j was unreachable -- the Postgres purge still happened, and the
    #: operator needs to know the projection is now stale rather than empty.
    graph_cleared: bool = False


class DataExport(BaseModel):
    """One archive that has been written out.

    `download_url` is filled in only where the file is still on disk, so the
    screen never has to work out whether a control is live and never offers one
    that answers 410. Note that `response_model` FILTERS: a field this model
    omits vanishes from the wire silently, and `status` vanishing would be a
    table of buttons that all fail.
    """

    id: str
    filename: str
    bytes: int = 0
    sha256: str = ""
    counts: dict[str, int] = Field(default_factory=dict)
    #: `ready`, `pruned` or `missing`. The last two are different facts:
    #: retention took one on purpose, the other went somewhere nobody recorded.
    status: str = "ready"
    created_at: str = ""
    #: An ADDRESS, never a `users.id`. A database key is not a thing a person
    #: is called.
    created_by: str | None = None
    download_url: str | None = None


class DataExportList(BaseModel):
    items: list[DataExport] = Field(default_factory=list)
    #: How many are kept, so the screen can say so without a second copy of the
    #: number living in TypeScript.
    keep: int = 0
    total: int = 0
