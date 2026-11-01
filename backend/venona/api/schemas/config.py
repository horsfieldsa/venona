"""Wire models for `routers/config.py` — the config editor's transport.

Two kinds of document ride the same shapes and the difference is `readonly`:
the EDITABLE files under `profile/` and `config/`, and the ASSEMBLED system
prompts, which are generated from those and cannot be written. The index renders
them in one list, which is why `GET /api/config` returns them together and why
the fields only one kind carries are optional here rather than split into two
models the frontend would have to discriminate.

Everything below is a projection of `config_service`'s dataclasses. It decides
nothing — `services/config_service.py` is the only write path (its allowlist,
its validate-through-the-real-loader rule, its `.history/` snapshot), and a
model here that started enforcing something would be a second opinion about
rules that live there.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConfigSummary(BaseModel):
    """A row in the document index: enough to list one without its body.

    `content` is deliberately absent — the index lists every document and the
    editor loads one, so shipping every body to draw a list would send the
    relevance profile, the rubric, the lexicon and the ontology on every visit.
    """

    key: str
    title: str
    description: str
    #: A key into `ConfigList.groups`, not a display string.
    group: str
    format: str
    #: True for the assembled prompts. They are generated from the editable
    #: documents, so a save would be overwritten by the next render.
    readonly: bool = False
    #: Editable documents only — what re-scoring this file does or does not cost.
    rescore_hint: str | None = None
    #: Content hash. Absent for an index entry that has not been rendered.
    version: str | None = None
    bytes: int | None = None
    updated_at: float | None = None
    #: Editable documents only -- a generated draft is staged for review.
    has_draft: bool | None = None


class ConfigGroup(BaseModel):
    """A heading on the index, and the sentence saying what lives under it.

    Declaration order is reading order, so the client never sorts these.
    """

    key: str
    title: str
    blurb: str


class ConfigList(BaseModel):
    """Editable documents and assembled prompts in one list. One request, not
    two: the index renders them together, and a second round trip would only let
    it draw half of itself first."""

    items: list[ConfigSummary] = Field(default_factory=list)
    groups: list[ConfigGroup] = Field(default_factory=list)


class ConfigDocOut(ConfigSummary):
    """One EDITABLE document, with its body.

    Split from `AssembledDocOut` rather than one model with both sets of fields
    optional, and the reason is the wire: a shared model emits
    `generated_from: null, tokens_estimate: null` on every editable document —
    two keys that were never there, on the response the settings editor loads
    every time it opens a file. Two routes, two shapes, no invented nulls.
    """

    content: str


class AssembledDocOut(ConfigSummary):
    """One generated system prompt, with its body."""

    content: str
    #: Which editable documents this was rendered from, so a reader can get to
    #: the file that would change it.
    generated_from: list[str] = Field(default_factory=list)
    #: Rough — a quarter of the character count. The real number comes back on
    #: the envelope; this answers "is this prefix getting expensive?" and is
    #: labelled as an estimate wherever it is shown.
    tokens_estimate: int = 0


class ConfigSnapshot(BaseModel):
    """One entry in `.history/`.

    `timestamp` dates the version you would be RESTORING, not when the snapshot
    was cut — see `config_service._snapshot_stamp`, where the difference is one
    save. It is an int.
    """

    file: str
    timestamp: int
    bytes: int
    #: The content hash from the filename, absent on snapshots cut before the
    #: field existed. It is what tells two versions apart: real snapshots share
    #: a timestamp when two saves land in one second, and share a byte count
    #: whenever an edit is the same length as what it replaced.
    version: str | None = None


class ConfigSnapshotOut(ConfigSnapshot):
    """One saved version, with its text.

    Read-only. Restoring is the client loading this into the editor and pressing
    Save, so it goes through `write_doc` like any other edit -- same validation,
    same `.history/` snapshot of what it replaces, same conflict check.
    """

    content: str


class ConfigHistory(BaseModel):
    items: list[ConfigSnapshot] = Field(default_factory=list)


class ConfigSaved(ConfigDocOut):
    """The saved document, plus what the save could not do by itself."""

    #: What the save did not reach in the RUNNING process, in the reader's
    #: words: a model-service rebuild a live run blocked, one that failed, a
    #: daily reading that could not be re-pointed. Reporting it is the whole
    #: point, and ADR 0081 is the decision about a setting that read as applied
    #: and was not. Set by `routers/config.py::_settings_saved`, which is the
    #: only place that knows.
    restart_required: list[str] = Field(default_factory=list)
    history: list[ConfigSnapshot] = Field(default_factory=list)


class AnchorsApplied(BaseModel):
    """`POST /config/anchors/apply`, in both its modes.

    Saving `profile/anchors.yaml` does nothing on its own — nothing in a run
    re-reads it — so the editor ships this beside the save. It is destructive:
    it deletes and rewrites the anchor run's assertions, which is why the UI
    confirms and calls `dry_run` first.

    The dry run reports `entities`/`edges`/`merges` from `validate_anchors` and
    leaves `aliases`/`skipped` at zero, because counting those means doing the
    insert. Both modes return the same keys so the dialog and the result render
    through one shape.
    """

    entities: int = 0
    aliases: int = 0
    edges: int = 0
    merges: int = 0
    skipped: int = 0
    dry_run: bool
    #: Neo4j is a projection (invariant 1); anchors that exist only in Postgres
    #: are not in the graph until `venona graph rebuild`.
    rebuild_required: bool


class DraftSummary(BaseModel):
    """A staged generated document: enough to draw the review checklist row."""

    key: str
    title: str
    run_id: str
    task: str
    generated_at: str
    bytes: int
    #: False when the generator's output failed the document's validator. The
    #: draft is kept anyway -- review-then-apply is the backstop -- and `error`
    #: carries the validator's message.
    valid: bool
    error: str | None = None


class DraftList(BaseModel):
    items: list[DraftSummary] = Field(default_factory=list)


class DraftOut(DraftSummary):
    content: str


class DraftDiscarded(BaseModel):
    key: str
    discarded: bool = True


class OntologyRejectedShape(BaseModel):
    """A shape the press keeps asserting that the CURRENT ontology refuses,
    aggregated across every stored run. `problem` is recomputed at read time --
    a stored reason goes stale the moment the ontology is edited."""

    predicate: str
    subject_type: str
    object_type: str
    n: int
    runs: int
    example_subject: str = ""
    example_object: str = ""
    last_run_id: str
    last_seen_at: str
    problem: str


class OntologyRejections(BaseModel):
    shapes: list[OntologyRejectedShape] = Field(default_factory=list)
