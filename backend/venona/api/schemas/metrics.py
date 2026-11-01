"""Run-metrics wire types. snake_case, ISO-8601 UTC with `Z` (Conventions)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StageMetrics(BaseModel):
    """One stage: when it ran, and everything it measured about itself."""

    stage: str
    status: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    #: Derived here rather than in the client, which would have to subtract two
    #: ISO strings and get the timezone right to do it.
    duration_ms: int = 0
    #: The stage's own `Funnel`, flattened: `offered`, `kept`, `drop_*`.
    metrics: dict[str, float] = Field(default_factory=dict)
    #: `offered == kept + sum(drops)`. False means the stage loses items it does
    #: not account for -- a finding about the pipeline, not about this endpoint.
    reconciled: bool = True


class SourceStats(BaseModel):
    """One feed's funnel for one run. The table that answers "too wide"."""

    source_id: str
    #: Items the source returned BEFORE the per-source cap. Known only at the
    #: cap site in `fetch.py` and never recorded until now.
    offered: int = 0
    capped: int = 0
    kept: int = 0
    duplicate: int = 0
    skipped: int = 0
    #: Counted against the brief's own `lookback_hours`, so "stale" means "could
    #: not have been briefed" rather than an arbitrary age.
    fresh: int = 0
    stale: int = 0
    #: NOT stale. The retrieval filter keeps undated documents, so these bypass
    #: the freshness window entirely.
    undated: int = 0
    age_p50_h: float | None = None
    status: str | None = None
    error: str | None = None


class TaskLatency(BaseModel):
    """Model-call latency and cost for one task, from the `llm_calls` ledger.

    The ledger has carried `duration_ms`, the token split and cost per task
    since it shipped, and no endpoint has ever read it.
    """

    task: str
    stage: str | None = None
    calls: int = 0
    p50_ms: int = 0
    p95_ms: int = 0
    max_ms: int = 0
    total_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: `cache_read_input_tokens / (input + cache_read)`. The only cache signal
    #: available -- there is no hit/miss flag.
    cache_hit_ratio: float = 0.0
    cost_usd: float = 0.0
    failed: int = 0


class SlowSubject(BaseModel):
    """The individual items that cost the most. Possible only because
    `llm_calls.subject` now records which cluster/entity a call was about."""

    subject: str
    task: str
    duration_ms: int
    cost_usd: float


class RunFreshness(BaseModel):
    """Named `Run…` because `brief.py` already exports a `Freshness`.

    Two pydantic models with one name make FastAPI emit BOTH as
    `venona__api__schemas__…__Freshness`, which renames the brief's existing
    schema and breaks every frontend alias onto it -- a generated-type failure
    a long way from the model that caused it.

    Itself: how old the corpus was when it arrived, against the window that
    decides whether any of it can be briefed.
    """

    lookback_hours: float = 0.0
    fresh: int = 0
    stale: int = 0
    undated: int = 0
    #: `stale / (fresh + stale)`. The waste number: work fetched and analysed
    #: that the brief's own filter can never select.
    stale_ratio: float = 0.0


class RejectedShape(BaseModel):
    """One (predicate, subject type, object type) the ontology refused at
    extraction time, with a verbatim example -- the count argues there is a
    problem, the example says what it is."""

    predicate: str
    subject_type: str
    object_type: str
    n: int = 0
    example_subject: str = ""
    example_object: str = ""
    example_article_id: str | None = None
    example_evidence: str | None = None


class RunMetrics(BaseModel):
    run_id: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int = 0
    #: Time between the run being asked for and its first stage starting.
    #: `mark_running` preserves `started_at` specifically so this is knowable.
    queue_ms: int = 0
    stages: list[StageMetrics] = Field(default_factory=list)
    sources: list[SourceStats] = Field(default_factory=list)
    tasks: list[TaskLatency] = Field(default_factory=list)
    slowest: list[SlowSubject] = Field(default_factory=list)
    freshness: RunFreshness = Field(default_factory=RunFreshness)
    #: Ordered yield chain, e.g. offered → kept → clusters → analyzed → items.
    funnel: list[dict[str, float | str]] = Field(default_factory=list)
    #: Model calls spent per item that reached the brief. The single number that
    #: says whether a run was too aggressive. `0` when no brief was written.
    cost_per_kept_item: float = 0.0
    calls_ledgered: int = 0
    #: From `runs.counters`. Deliberately reported ALONGSIDE `calls_ledgered`
    #: rather than reconciled with it: `_record_failed_call` does not increment
    #: the counter and a `PermissionDenied` call writes no ledger row at all, so
    #: the two legitimately disagree and neither is the attempt count.
    calls_counted: int = 0
    rejected_shapes: list[RejectedShape] = Field(default_factory=list)
