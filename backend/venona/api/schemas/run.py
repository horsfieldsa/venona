"""Contracts C6 and C11 -- the run wire format.

C6 has named this file as its home since it was written. It did not exist. The
runs router returned bare `dict[str, Any]` with no `response_model`, which made
runs the one API boundary in the project without a pydantic model -- against
CLAUDE.md's own convention -- and meant `make types` generated
`Record<string, unknown>` for every run endpoint. `web/src/types/run.ts` was
therefore hand-written and checked against nothing, and had already drifted:
its `RunCounters` omits `entities_defined`, which the backend has emitted and
`RunsView` has labelled for months.

Two rules carried over from C6 and worth restating where the models live:

  * **`data` is additive-only.** New keys yes, renamed keys no. Hence
    `model_config = {"extra": "allow"}` on the event payload -- a client on an
    older build must keep working, and a strict model here would turn a new
    key into a 500 on the server that produced it.
  * **`snake_case` on the wire**, no camelCase mapping layer (CLAUDE.md).

`lane` and `label` are DERIVED from `run_types`, not stored. A `kind` this
build has never heard of still gets both, because `type_for` falls back rather
than raising -- the runs table long outlives any one release's idea of what a
run is.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from venona.services import run_facts, run_types

# The two frozen vocabularies of contract C6, spelled out rather than derived
# from `run_service`'s tuples. A deliberate duplicate, pinned by
# `tests/test_run_contract_vocabulary.py`, for the reason `run_types` gives for
# copying `RESEARCH_STAGES`: importing would close a cycle, and an ENFORCED
# duplicate beats one nothing checks.
#
# Written as literals so they reach the OpenAPI document as an enum. Typed as
# bare `str`, the generated client cannot narrow them, so every consumer
# re-declares the same union by hand and the two drift in silence.
RunStatus = Literal["queued", "running", "succeeded", "succeeded_partial", "failed", "cancelled"]
#: What a run FOUND. `status` says whether the machinery worked; this says
#: whether there was an answer. The two were one field until ADR 0183, which is
#: why `succeeded_partial` read as a degradation on 44 of 68 lookups that had
#: degraded in no way at all -- the web simply held no definition.
#:
#: None for a run with no answer to report: every `failed`, every `cancelled`,
#: and every row written before the column existed.
RunOutcome = Literal["delivered", "some", "nothing_found", "nothing_to_do"]
#: What one message CLAIMS, as opposed to what the whole run claims.
#:
#: `warning` is the only level that means the run degraded. `todo` is work
#: waiting on a person -- the curation queue, which re-fires every run until
#: somebody rules on it -- and `note` is an observation with nothing to do
#: about it. The level is per MESSAGE because a run routinely has all three at
#: once: without it a surface can only pick one heading for the whole array out
#: of the run's status, and a degraded run then reads its routine remarks out
#: as reasons it degraded. comment-history: ok -- names the failure the field
#: exists to prevent, which is why it is a field and not a bare string.
RunMessageLevel = Literal["warning", "todo", "note"]
#: Where a reader can act on a `todo`. Symbolic, never a route: the router owns
#: route strings. `web/src/lib/runMessages.ts` resolves these.
RunMessageTo = Literal["curation", "sources", "config"]
RunEventType = Literal[
    "stage_started",
    "stage_progress",
    "stage_finished",
    "counter",
    "log",
    "warning",
    "error",
    "model_message",
    "done",
]


class ResolvedFact(BaseModel):
    """One line of a run's result panel, already resolved against the row.

    Server-side for the reason `outcome_text` is: the words belong to the run
    type (`run_types.Fact`), every surface reporting a finished run needs the
    same ones, and a second copy of the rules in TypeScript drifts inside a
    release. The client renders this list without knowing what kind of run it
    is looking at -- which is the point, since the panel it replaced was shaped
    for a lookup and rendered `sourced: no` at a reader's brief.

    Only facts the row could actually answer appear here. An absent key is
    omitted rather than sent as a blank, so a run recorded before a key existed
    shows fewer facts rather than a wall of nothing.
    """

    key: str
    label: str
    #: count | text | flag | url | link | reader | candidates. See `run_types.Fact`.
    shape: str
    #: The raw value, for `count` and `candidates`, which the client formats.
    value: Any = None
    #: What to show instead of `value`, when the two differ -- a flag's own
    #: words, a reader's name. None means render the value.
    text: str | None = None
    href: str | None = None


class RunStage(BaseModel):
    """One row of `run_stages`. Server-provided, so a run renders its own stage
    list rather than the client assuming the pipeline's six."""

    run_id: str
    stage: str
    status: str
    counts: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: str
    finished_at: str | None = None


class RunMessage(BaseModel):
    """One thing a run had to say, at its own level.

    The field on the run is still called `warnings` because that is the wire
    name and a wire name is never rendered; what changed is that each item now
    carries whether it is a degradation, and the surfaces render per message
    rather than picking one heading for all of them from the run's status.
    """

    level: RunMessageLevel
    text: str
    #: Set only on a `todo`, and only when there is a surface that can act on it.
    to: RunMessageTo | None = None


class RunSummary(BaseModel):
    """A row in the history table."""

    id: str
    kind: str
    # Derived. See the module docstring.
    lane: str
    label: str
    detail: str
    trigger: str
    status: RunStatus
    outcome: RunOutcome | None = None
    #: `outcome`, in this kind's own words -- "not found" for a lookup,
    #: "nothing new" for a research run, both from `nothing_to_do`. Derived
    #: server-side by `decorate`; see `run_types.RunType.outcomes`.
    outcome_text: str | None = None
    stage: str | None = None
    counters: dict[str, Any] = Field(default_factory=dict)
    warnings: list[RunMessage] = Field(default_factory=list)
    params: dict[str, Any] | None = None
    brief_id: str | None = None
    cost_usd: float = 0.0
    error: str | None = None
    started_at: str
    finished_at: str | None = None
    hidden_at: str | None = None
    #: Who asked for this run, as an address. `runs.started_by` has been written
    #: since migration 0002 and read by nothing, so "who started the run that
    #: spent this" was a question the database could answer and no surface asked.
    #: NULL for the scheduler, for `venona run`, and for every run started before
    #: identity existed — the same honest NULL every actor column carries (C1).
    started_by_email: str | None = None
    #: What this run was ABOUT, from the `params` key its kind declares
    #: (`run_types.RunType.subject`). The history list inferred this from a
    #: cascade over seven param names and returned nothing for a reader's brief,
    #: so those rows had a blank subject on a page whose whole job is telling
    #: nine runs of one kind apart.
    subject_text: str | None = None
    #: Which model service did the thinking, spelled for a reader. NULL on a
    #: run from before the column existed, and on one cancelled before it was
    #: ever admitted -- the executor is chosen at admission, not at submit, so
    #: a queued run genuinely has no answer. Omitted rather than guessed, the
    #: same rule `started_by_email` and `outcome` follow.
    harness_label: str | None = None
    #: How that run was paid for: `subscription` or `api`. From the ROW, never
    #: from the instance's current setting -- half a history spanning a switch
    #: would otherwise be labelled with the wrong one.
    billing_mode: str | None = None


class RunDetail(RunSummary):
    """`GET /runs/{id}`. Adds the per-stage breakdown and the profile hash --
    how you tell "the brief changed because the profile changed" from "the brief
    changed because the news did"."""

    profile_hash: str | None = None
    stages: list[RunStage] = Field(default_factory=list)
    #: What this run produced, in its kind's declared words. Detail only: the
    #: facts resolve from stage counts, and the list projection carries no
    #: stages (`db/repositories/run.py::_LIST_COLUMNS`).
    facts: list[ResolvedFact] = Field(default_factory=list)
    #: The sentence under the verdict chip. Never assembled in the browser --
    #: CLAUDE.md's "never invent words for a run outcome at the render site"
    #: covers the sentence as well as the chip.
    result_line: str | None = None


class RunList(BaseModel):
    items: list[RunSummary]
    total: int
    hidden: int = 0
    # The RESEARCH-lane run. Light runs appear in `items` with their own status;
    # hoisting one here would make the Runs page follow a 20-second lookup
    # instead of the pipeline someone was watching.
    active: str | None = None


class RunTypeOut(BaseModel):
    kind: str
    label: str
    description: str = ""
    lane: str
    # NOT `orchestrator.STAGES`. A lookup's only stage is `lookup`, which the
    # pipeline has never heard of and `venona run --only` must never accept.
    stages: list[str]
    summary: list[str]
    detail: str
    # `scheduled` | `on_demand` — three research kinds share one label, and
    # this is what still tells the nightly one from a button press.
    trigger: str
    # Outcome value -> this kind's word for it. A row carries its own resolved
    # phrase as `outcome_text`; this is the whole map, for a surface that has
    # to name an outcome the row has not reached yet.
    outcomes: dict[str, str] = Field(default_factory=dict)
    #: Whether a run of this kind talks to the model, and so whether its page
    #: offers the session behind it. Declared rather than inferred from whether
    #: any transcript happens to exist: a run that failed before its first call
    #: has none, and hiding the section would say the wrong thing about the kind.
    uses_model: bool = True


class RunTypeList(BaseModel):
    types: list[RunTypeOut]
    lanes: dict[str, int]


class RunPreview(BaseModel):
    """What a run would do and cost, before it does it. One shape for every
    launch dialog.

    Four endpoints grew this independently and all four reached the same
    discipline by different words: count what the RUN will attempt rather than
    what is queued, price it off this machine's own ledger, and say when the
    figure is a fallback rather than an average. What they did not share was
    field names -- `per_lookup_usd`, `per_search_usd`, a bare `estimate_usd`
    with no per-item figure at all -- so every dialog in front of them was a
    separate component restating the same three sentences, and the one that
    shipped without a duration quoted an afternoon as though it were a coffee.

    The unit differs and that is the only thing that does: acronyms, names,
    articles. `unit` carries it in the reader's words so the shared dialog can
    say "605 names" without knowing which run it is in front of.

    `per_item_usd` is ALWAYS a number, even with nothing to average -- the
    fallback constant, with `cost_sample: 0` beside it. A null price renders as
    a blank where a figure belongs, and a person authorising a spend reads a
    blank as free.
    """

    eligible: int
    will_attempt: int
    cap: int | None = None
    #: Reader words for what is counted, singular and plural. Never this
    #: repo's vocabulary: a person has not heard of an assertion or an entity.
    unit: str
    unit_plural: str
    per_item_usd: float
    estimate_usd: float
    #: Sequential, one item at a time so a cancel lands inside one of them.
    #: Which makes the total the sum, and on a long queue that is hours; a
    #: dialog quoting only money tells half the truth.
    per_item_seconds: float
    estimate_seconds: int
    #: 0 means the per-item figures are fallbacks rather than measurements, and
    #: no surface may present a guess in the words of an average.
    cost_sample: int
    #: The head of the queue, so a reader can see WHAT is about to be bought.
    sample: list[str] = []
    #: Numbers that belong to one kind of run and that this shape has no
    #: opinion about -- `skipped_attempted` and `retry` for the glossary sweep,
    #: `calls` for question generation, `would_appear` for corroboration. The
    #: dialog renders them through the caller's own slot.
    extra: dict[str, Any] = {}


class RunStarted(BaseModel):
    """What `run_launcher.launch` hands back — the ONLY place a lane slot is
    claimed, so this is the shape every start endpoint returns.

    202, not 200: the work is a background task. `status` is `running` or
    `queued`, and the difference matters to a caller in the light lane, which
    queues rather than refusing (contract C11).
    """

    run_id: str
    status: Literal["running", "queued"]
    lane: str
    #: True when an identical light-lane run was already in flight and this
    #: caller was attached to it instead of starting a second one. Two research
    #: runs are refused outright rather than coalesced.
    coalesced: bool = False


class ScheduledRunTriggered(BaseModel):
    """Whether the scheduled daily was asked to fire, and what happened to the
    ASK.

    `skipped` is a 202, not a 409: the caller is a cron, and a cron treats a
    non-2xx as a failed job worth retrying -- straight back into the same busy
    lane. Skipping on conflict is the scheduler's own long-standing behaviour;
    the wire just reports it.
    """

    #: `started | skipped | error`, and NOT a `RunOutcome` despite the name --
    #: this is about the request to start a run, which returns long before the
    #: run has found anything. The key is left as it is because an external
    #: cron parses this body; a reader wanting what the run FOUND reads
    #: `RunSummary.outcome`.
    outcome: str
    run_id: str | None = None
    reason: str | None = None


class RunEvent(BaseModel):
    """One row of the durable event log (contract C6).

    `seq` is what `Last-Event-ID` resumes from, which is why the log's
    durability — not the transport — is what makes a dropped SSE connection
    lossless.
    """

    model_config = ConfigDict(extra="allow")

    seq: int
    run_id: str
    ts: str
    type: RunEventType
    stage: str | None = None
    #: C6's additive-only rule lives here: a client on an older build must be
    #: able to ignore a key it does not know rather than fail on it. `dict` is
    #: already permissive; `extra="allow"` on the envelope is the half this
    #: module's docstring has promised since before the model existed.
    data: dict[str, Any] = Field(default_factory=dict)


class RunEventList(BaseModel):
    """Plain replay, for a client that would rather poll than stream.

    `last_seq` echoes the request's `after_seq` when there is nothing new, so a
    poller always has a cursor to send back.
    """

    events: list[RunEvent] = Field(default_factory=list)
    last_seq: int
    has_more: bool = False


class RunHidden(BaseModel):
    """Hiding, never deleting (ADR 0237). `hidden_at` is null for the unhide
    direction, which is the same row shape reporting the reverse."""

    run_id: str
    hidden_at: str | None = None


class RunHideAll(BaseModel):
    """`skipped` is runs still in flight. They are skipped, not refused, so
    clearing the history never fails because something is running."""

    hidden: int
    skipped: int


class RunCancelling(BaseModel):
    """The run was asked to stop, not observed stopping.

    A run between items unwinds within moments; one inside a model call is
    stopped for it after `run_service.CANCEL_GRACE_SECONDS`. Either way the
    answer here is 202 and the in-flight run reports `cancelling` until it is
    actually over. ADR 0231."""

    run_id: str
    cancelling: bool


def decorate(run: dict[str, Any], *, conn: Any = None) -> dict[str, Any]:
    """Add the derived `lane`/`label`/`detail`/`trigger`/`outcome_text` to a
    run row, plus `subject_text`, and -- when the row carries stages -- the
    resolved `facts` and `result_line`.

    One place, so a row is self-describing everywhere it appears and the UI
    never has to keep its own copy of the kind-to-label mapping.

    **The facts resolve only for a detail row.** They read `run_stages.counts`,
    and the list projection deliberately does not select stages, so asking for
    them on every row of a history page would be one query per row for values
    that page does not show. `stages` being present is the signal, not the
    caller passing a flag: `get_run` attaches them and `listing` does not.

    `conn` is optional and used only by a `reader` fact, which turns a user id
    into the name of a person. Without one, that fact is dropped rather than
    rendered as a database value.
    """
    run_type = run_types.type_for(run.get("kind", ""))
    outcome = run.get("outcome")
    subject = run_type.subject
    extra: dict[str, Any] = {}
    if "stages" in run:
        extra["facts"] = run_facts.resolve_facts(run, conn=conn)
        extra["result_line"] = run_facts.resolve_line(run)
    return {
        **run,
        "lane": run_type.lane,
        "label": run_type.label,
        "detail": run_type.detail,
        "trigger": run_type.trigger,
        # The row's OWN outcome, already in this kind's words. Resolved here
        # for the same reason `label` is: every surface that reports a finished
        # run needs the phrase, and a second copy of the map in TypeScript
        # would drift within a release. `None` when the run has no outcome, or
        # when its kind declares no phrase for the one it has.
        "outcome_text": run_type.outcomes.get(outcome) if outcome else None,
        "subject_text": run_facts.subject_text(run, subject),
        # The SERVICE'S OWN NAME, resolved here for the same reason `label` is:
        # a second copy of the key-to-name map in TypeScript drifts within a
        # release, and the row carries a key (`codex`) where a reader needs a
        # name (`Codex`). None on a row that predates the column and on a run
        # cancelled before it was admitted, and omitted rather than rendered as
        # "unknown".
        "harness_label": _harness_label(run.get("harness")),
        **extra,
    }


def _harness_label(key: Any) -> str | None:
    if not key:
        return None
    from venona.llm.harness import known, resolve

    return resolve(key).label if key in known() else str(key)
