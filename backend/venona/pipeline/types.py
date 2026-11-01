"""Contract C7: RunContext and the stage signature.

Two rules encoded here, both cheap now and rewrites later:

  1. Every nondeterminism source -- clock, ids, randomness, network, model --
     arrives through the context. `VENONA_OFFLINE=1` swaps them all at once, so
     two runs over the same fixtures are byte-identical. `make lint-determinism`
     enforces that nothing reaches around this.

  2. Stages read their INPUTS FROM THE DATABASE by run_id. A stage's return value is
     metadata only. That is what makes `venona run --only brief`
     possible, and it turns an 8-minute alias-tuning loop into a 5-second one.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from uuid_extensions import uuid7str

from venona import timeutil
from venona.db import core as db
from venona.db.repositories import pipeline_types as repo
from venona.llm.executor import LlmExecutor
from venona.settings import Settings


class RunCancelled(Exception):
    """Raised by check_cancel(). Unwinds to the orchestrator, which records the
    run as cancelled rather than failed."""


#: The widest window a run that writes a brief may retrieve over.
#:
#: `first_run_lookback_hours` is 168 and is legitimate -- a cold start has no
#: yesterday -- so the ceiling is that, not `lookback_hours`. Anything wider is
#: a run reading for the graph, which must not also be writing a brief.
MAX_BRIEF_LOOKBACK_HOURS = 168


class WindowTooWide(ValueError):
    """A widened lookback reached a run that writes a brief.

    Its own class rather than a bare `ValueError` because the orchestrator has
    to be able to tell "this run was configured wrong" from a stage failing, and
    because the API turns it into a 422 rather than a 500.
    """


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Clock(Protocol):
    def now(self) -> str: ...


class SystemClock:
    def now(self) -> str:
        return utc_now_iso()


class FrozenClock:
    """Offline clock. Advances only when told to, so timestamps are reproducible."""

    def __init__(self, start: str = "2026-08-04T12:00:00.000Z") -> None:
        self._t = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)

    def now(self) -> str:
        return self._t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._t += timedelta(seconds=seconds)


class IdGen(Protocol):
    def uuid7(self) -> str: ...


class SystemIdGen:
    def uuid7(self) -> str:
        return uuid7str()


class SeededIdGen:
    """Offline id generator. Monotonic and reproducible; keeps uuid7's sortable
    shape so ordering behaves the same as in production."""

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)
        self._counter = 0

    def uuid7(self) -> str:
        self._counter += 1
        tail = "".join(self._rng.choice("0123456789abcdef") for _ in range(12))
        return f"{self._counter:08x}-0000-7000-8000-{tail}"


@dataclass
class RunCounters:
    """Contract C6: these names are frozen. RunProgress renders them and the
    acceptance script asserts them."""

    documents_fetched: int = 0
    documents_new: int = 0
    clusters: int = 0
    acronyms_grounded: int = 0
    acronyms_undefined: int = 0
    entities_new: int = 0
    entities_seen: int = 0
    entities_defined: int = 0
    edges_written: int = 0
    entities_promoted: int = 0
    curation_open: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


#: Where a reader can act on a message. SYMBOLIC, never a route: the router
#: owns route strings, and a backend emitting `/curation` breaks silently the
#: day that path is renamed. `web/src/lib/runMessages.ts` resolves these.
DESTINATIONS = ("curation", "sources", "config")

#: What a message CLAIMS. The level is the message's own, not the run's.
#:
#: A flat list of strings cannot carry this, and nothing downstream can then
#: tell a degradation from a remark: a surface has to guess a heading from the
#: run's status, and on a degraded run every routine remark renders under amber
#: "Why", and one real degradation made an alarm out of the curation backlog.
MESSAGE_LEVELS = ("warning", "todo", "note")


@dataclass
class RunMessage:
    """One thing a run has to say, at its own level.

    `db.jdump` is `json.dumps` with no dataclass hook, so this never reaches a
    column as itself -- `as_json` is the boundary, and every persistence site
    goes through it.
    """

    level: str
    text: str
    #: One of `DESTINATIONS`, for a `todo` a reader can go and act on.
    to: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"level": self.level, "text": self.text}
        if self.to is not None:
            out["to"] = self.to
        return out


def as_json(messages: list[RunMessage]) -> list[dict[str, Any]]:
    """Messages, ready for `db.jdump`. The one conversion boundary."""
    return [m.as_dict() for m in messages]


def texts(messages: list[RunMessage]) -> list[str]:
    """Just the sentences, for the SSE log, which carries no levels."""
    return [m.text for m in messages]


@dataclass
class RunContext:
    """Passed to every stage. Nothing in pipeline/services/llm/graph may reach
    around it for time, ids, randomness or the network."""

    run_id: str
    db: db.Conn
    llm: LlmExecutor
    settings: Settings
    clock: Clock = field(default_factory=SystemClock)
    ids: IdGen = field(default_factory=SystemIdGen)
    rng: random.Random = field(default_factory=lambda: random.Random(0))
    graph: Any = None
    http: Any = None
    profile: Any = None
    counters: RunCounters = field(default_factory=RunCounters)
    #: Everything the run has to say, each item carrying its own level.
    warnings: list[RunMessage] = field(default_factory=list)
    # Set by `warn`, never by `note` or `todo`. This, not `warnings`, decides
    # whether the run reports `succeeded` or `succeeded_partial`.
    degraded: bool = False

    #: THE RUN'S SPEND CEILING, set by the lifecycle so no run executes
    #: unbudgeted. `max_calls_per_run` is the one real bound on a run and it
    #: bound only the kinds whose closure happened to build a `Budget` -- five
    #: of sixteen had none. A closure needing a wider ceiling replaces it.
    budget: Any = None

    #: THE RUN'S OWN WAIT HOOKS, handed to every model call it makes. Per-run
    #: and not per-executor: `executor_for` returns one object per LANE, and an
    #: instance attribute reports one run's waits into another's stream.
    on_wait: Any = None
    wait_abort: Any = None

    #: WHICH STAGE IS OPEN. The one source the three recorders read -- the
    #: event log, `run_metrics`, the `llm_calls` ledger -- so none can be handed
    #: a name that disagrees, which is how a sweep ledgered `analyze` while
    #: `run_stages` recorded `research`. A STAGE MUST NEVER BRANCH ON THIS: C7
    #: holds only while no stage behaves differently for knowing its own name.
    stage: str | None = None

    _cancelled: bool = False
    _emit: Callable[[str, str | None, dict[str, Any]], Awaitable[None]] | None = None

    # ── progress ─────────────────────────────────────────────────────
    async def emit(self, type_: str, stage: str | None = None, **data: Any) -> None:
        """`stage` defaults to the one that is open."""
        if self._emit is not None:
            await self._emit(type_, stage or self.stage, data)

    async def counter(self, name: str, delta: int | float = 1) -> None:
        current = getattr(self.counters, name, None)
        if current is None:
            raise KeyError(f"unknown counter {name!r}; the set is frozen (contract C6)")
        setattr(self.counters, name, current + delta)

    def metric(self, stage: str | None, key: str, value: float, unit: str | None = "count") -> None:
        """Record a measurement. The unfrozen counterpart to `counter`.

        `counter` raises on an unknown name because contract C6 freezes those
        thirteen and every addition costs five files and a parity test. This
        does not, deliberately: a stage should be able to record what it learns
        without amending a contract, and the cost of a wrong metric name is a
        row nobody reads rather than a broken client.

        Synchronous, unlike `counter` and `emit`, because it is a local database
        write on the connection the stage already holds -- there is nothing to
        await and making it async would put `await` on every drop site.
        """
        from venona.pipeline.metrics import record_metric

        record_metric(
            self.db,
            run_id=self.run_id,
            # THE OPEN STAGE WINS over the name the caller passed: inside a
            # stage there is no other stage for the measurement to belong to.
            # Forty-two sites name their own, right under the pipeline and wrong
            # the moment the helper runs inside someone else's stage. The
            # caller's name survives for work that belongs to no stage at all.
            stage=self.stage or stage,
            key=key,
            value=value,
            unit=unit,
            at=self.clock.now(),
        )

    def warn(self, message: str) -> None:
        """A DEGRADATION: something the run set out to do and could not, where
        the reader got less because of it.

        The ONLY thing that sets `degraded`, and so the only route to
        `succeeded_partial` -- which is never `failed`, because a partially
        useful run must not be reported as a failure.

        The test is what the reader lost, not whether something surprised us.
        "The window held nothing to write about" and "a person has to rule on
        this" both surprise, and neither costs the reader a sentence of brief:
        they are `note` and `todo`.
        """
        self.warnings.append(RunMessage("warning", message))
        self.degraded = True

    def todo(self, message: str, *, to: str | None = None) -> None:
        """Work waiting on a PERSON. Not a degradation.

        The curation queue re-fires every run until somebody rules on it -- 86
        open alias conflicts today, and the contested acronym surfaces beside
        them. Through `warn` it made `succeeded` unreachable; through `note` it
        read as an idle remark and nothing said it was actionable. `to` names
        the surface that can act on it, so the run page can link there instead
        of naming a shell command.
        """
        if to is not None and to not in DESTINATIONS:
            raise ValueError(f"unknown destination {to!r}; the set is {DESTINATIONS}")
        self.warnings.append(RunMessage("todo", message, to))

    def note(self, message: str) -> None:
        """An observation worth surfacing that is NOT a degradation.

        `succeeded` was unreachable. Two messages fire on essentially every
        run -- the open alias-conflict backlog (a curation queue length, 86 of
        them today) and the per-relation type rejections the ontology check is
        SUPPOSED to produce -- and both went through `warn`, so a run that did
        its whole job still reported `succeeded_partial`. A status that says
        "degraded" on every row teaches you to ignore it, which is the same
        damage as calling a partial run `failed`; the project already met this
        once and treated it as a rendering problem (DECISIONS, 2026-08-06).

        Notices still appear in `warnings` so nothing is hidden -- they carry
        `level` `note`, so they do not decide the run's status and do not
        render under the heading that explains a degradation.
        """
        self.warnings.append(RunMessage("note", message))

    # ── cancellation ─────────────────────────────────────────────────
    #
    # `cancel_run`'s docstring promises "stages check between items, so
    # cancellation is clean" -- and that was only true between STAGES. The
    # bridge from the registry's `handle.cancelled` to this flag lived in the
    # orchestrator's own `check_cancel`, which is awaited at stage boundaries
    # only; `analyze.py`'s per-cluster `ctx.check_cancel()` read a flag nothing
    # had set. Cancelling a run mid-analyze therefore did nothing until all 217
    # clusters had been paid for. `_is_cancelled` closes that: the context asks
    # the source of truth on every check, so a per-item call site works.
    _is_cancelled: Callable[[], bool] | None = None

    def cancel(self) -> None:
        self._cancelled = True

    def check_cancel(self) -> None:
        if self._is_cancelled is not None and self._is_cancelled():
            self._cancelled = True
        if self._cancelled:
            raise RunCancelled(self.run_id)


# (There was a nine-name `STAGES` here listing internal phases -- normalize,
# dedup, resolve, score, persist -- that are steps *inside* the four real
# stages, never separately runnable. It was dead, and it contradicted
# `orchestrator.STAGES`, which is what `--only` validates against.)


def build_context(
    *,
    run_id: str,
    db: db.Conn,
    llm: LlmExecutor,
    settings: Settings,
    graph: Any = None,
    http: Any = None,
    profile: Any = None,
    emit: Callable[[str, str | None, dict[str, Any]], Awaitable[None]] | None = None,
) -> RunContext:
    """One place decides which clock/ids/rng a run gets. Offline swaps all three."""
    if settings.offline:
        clock: Clock = FrozenClock()
        ids: IdGen = SeededIdGen(0)
        rng = random.Random(0)
    else:
        clock, ids, rng = SystemClock(), SystemIdGen(), random.Random()

    return RunContext(
        run_id=run_id,
        db=db,
        llm=llm,
        settings=settings,
        clock=clock,
        ids=ids,
        rng=rng,
        graph=graph,
        http=http,
        profile=profile,
        _emit=emit,
    )


# A cluster whose canonical document has no readable body cannot be briefed
# honestly. Lives here rather than in `brief.py` because ANALYZE has to apply
# the same bound: a document the brief will refuse is a document analyze must
# not pay to score. Measured 2026-08-16: one in-window cluster scored 31 at 548 characters,
# was analysed at full price, and was then dropped by `too_short`.
MIN_BRIEFABLE_CHARS = 600


def retrieval_window(
    conn: db.Conn, *, now_iso: str, settings: Settings, writes_brief: bool = True
) -> tuple[str, int]:
    """The recency bound that analyze spends against and the brief retrieves by.

    Returns `(cutoff_iso, hours)`.

    `writes_brief=False` is the ONE case where a widened window is legitimate,
    and the argument below is exactly why it has to be stated rather than
    assumed. The coupling holds because a cluster outside the window cannot
    reach a brief; a run that writes no brief has no such ceiling, and the spend
    buys graph instead. So the caller declares what it is doing and the refusal
    below catches anyone who widens the window with a brief still in the stage
    list. ADR 0228.

    ONE helper for BOTH stages, and that is the whole point. They disagreed
    before: `brief.build_retrieval` enforced `lookback_hours` and
    `analyze.pending_clusters` had no date predicate at all, so a run paid to
    score nine days of backlog against a 36-hour brief. Measured 2026-08-16 on
    `run:06a8160f`: 112 clusters analysed, 8 of them inside the window, ~$12 of
    the $13 analyze spend structurally unable to reach a brief. Two call sites
    reading the same setting would drift again; one function cannot.

    **First run widens.** A reader whose database is empty has no yesterday, so
    a 36-hour window on a cold start briefs whatever the last day and a half
    happened to contain -- which is how a first brief opens by apologising.
    Keyed on `briefs`, not `runs`: the window governs what a BRIEF may contain,
    and a run that fetched but skipped the brief has not spent that budget.
    """
    limits = settings.pipeline
    first_run = repo.select_briefs(conn).fetchone()["n"] == 0
    hours = limits.first_run_lookback_hours if first_run else limits.lookback_hours
    if writes_brief and hours > MAX_BRIEF_LOOKBACK_HOURS:
        raise WindowTooWide(
            f"a run that writes a brief may not read back {hours} hours; "
            f"the ceiling is {MAX_BRIEF_LOOKBACK_HOURS}"
        )
    return timeutil.cutoff_seconds(now_iso, hours), hours


def resolve_cap(argument: int | None, configured: int | None) -> int | None:
    """How much of its work a stage takes on: argument, then config, then all.

    ONE DIRECTION ONLY. An explicit argument -- a CLI flag, an API field --
    always wins, so `venona run --limit 5` means 5 whatever the file says. The
    config is the standing answer for every caller that names nothing, which is
    the API's "Run now" and the nightly scheduled run. `None` at both levels
    means uncapped, which is what every run did before either existed.

    Named and shared rather than written twice, because the two stages that use
    it are the two that bound what a run costs, and a precedence rule that
    disagreed between them would be invisible until a bill arrived.
    """
    return argument if argument is not None else configured


def with_overrides(
    settings: Settings,
    *,
    lookback_hours: int | None = None,
    discovery_max_age_hours: int | None = None,
    max_calls: int | None = None,
    adjudicate_limit: int | None = None,
) -> Settings:
    """A run's own view of the settings, for the knobs one run may widen.

    A COPY, NEVER A MUTATION. Every knob here is already read from `settings` at
    the point of use, so handing a run its own object is the whole mechanism --
    nothing downstream needs a new parameter. But `app.state.settings` is shared
    with every other run, the scheduler and `/api/health`, so assigning to it
    (as `cli.py::_analyze_run` does, safely, in a one-shot process) would leak
    one run's ceiling into all of them.

    `model_copy` does not re-validate. The bounds belong at the API boundary,
    where a number a person typed arrives.
    """
    pipeline_update: dict[str, Any] = {}
    if lookback_hours is not None:
        # BOTH, because `retrieval_window` picks between them on whether any
        # brief exists, and a backfill on an instance with no briefs yet would
        # otherwise silently get 168 hours instead of what was asked for.
        pipeline_update["lookback_hours"] = lookback_hours
        pipeline_update["first_run_lookback_hours"] = lookback_hours
    if discovery_max_age_hours is not None:
        pipeline_update["discovery_max_age_hours"] = discovery_max_age_hours
    if adjudicate_limit is not None:
        pipeline_update["adjudicate_limit_per_run"] = adjudicate_limit

    update: dict[str, Any] = {}
    if pipeline_update:
        update["pipeline"] = settings.pipeline.model_copy(update=pipeline_update)
    if max_calls is not None:
        update["llm"] = settings.llm.model_copy(update={"max_calls_per_run": max_calls})
    return settings.model_copy(update=update) if update else settings
