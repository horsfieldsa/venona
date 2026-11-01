"""Thin orchestration around a single analyze call.

Sits between the raw executor and the pipeline: assembles the prompt, calls,
ledgers the result, and applies the retry policy. The `analyze` stage calls this
once per cluster; `venona analyze` calls it once, which is how the round-trip is
exercised with no run behind it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from venona.db import core as db
from venona.llm import budget as budget_mod
from venona.llm.breaker import BreakerOpen
from venona.llm.executor import LlmError, LlmExecutor, LlmResult, PermissionDenied, Usage
from venona.llm.prompts import Profile, analyze_system_prompt, load_profile
from venona.llm.schemas import analyze_schema, compute_overall, tier_for
from venona.llm.transcript import summarize
from venona.llm.usage import record_call
from venona.settings import Settings
from venona.untrust import inline, quote


def provenance(llm: Any, settings: Any) -> tuple[str | None, str | None]:
    """Which service ran this call, and what paid for it.

    Reached by `getattr` rather than through `LlmExecutor`: the replay executor
    and every test stub satisfy that protocol without being a subprocess, and
    the same duck-typing already answers `health` and `breaker` on the health
    route. A stub returns `(None, None)`, which is exactly what an unattributed
    ledger row should say.
    """
    harness = getattr(llm, "harness", None)
    if harness is None:
        return None, None
    return harness.key, settings.llm.billing_mode


def resolve_model(llm: Any, value: str) -> str:
    """A configured tier or model name, in the active service's own vocabulary.

    HERE, not at every site that reads the setting, because there are exactly
    two paths to `llm.call` and both pass through this module -- and
    resolving before the call means `cache_key_for` sees the native name, so a
    replayed fixture keys identically to the live call that recorded it.

    Skipping this does not fail loudly. It sends one vendor's tier word to
    another, which answers `invalid_request_error: The 'sonnet' model is not
    supported` -- once per call, until the breaker gives up on a service that
    was working perfectly well.
    """
    harness = getattr(llm, "harness", None)
    if harness is None:
        return value
    from venona.llm.harness import model_for

    return model_for(harness, value)


#: What a caller's tool name means, in the vocabulary a service declares.
#: Both spellings, because the call sites name one vendor's tools and the
#: capability words are what a service answers for.
_CAPABILITY_OF = {
    "websearch": "web_search",
    "webfetch": "web_fetch",
}


def narrow_to_capabilities(
    llm: Any, tools: Sequence[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Drop what the active service cannot do, and SAY SO.

    The alternative is what shipped: a service without a fetch tool is handed
    one anyway, ignores it, and answers the prompt that asked it to read a page
    with a well-formed "nothing found". That reports as a successful call, so
    the only trace is a thinner brief nobody can account for.

    A note rather than a refusal, because every current caller can work with
    less -- the grounding step wants search and fetch and is useful with search
    alone. What a reader loses is recorded where they will look for it.
    """
    harness = getattr(llm, "harness", None)
    if harness is None or not tools:
        return tuple(tools), ()

    kept: list[str] = []
    dropped: list[str] = []
    for tool in tools:
        capability = _CAPABILITY_OF.get(tool.lower().replace("_", ""), tool)
        (kept if capability in harness.capabilities else dropped).append(tool)
    return tuple(kept), tuple(dropped)


def is_transient(llm: Any, error: str | None) -> bool:
    """Whether the provider said "not now" rather than "never".

    Asked of the harness because the vocabulary is one vendor's. `breaker.py`'s
    `classify` field carries the reason and the worked example.
    """
    harness = getattr(llm, "harness", None)
    if harness is None:
        return budget_mod.is_retryable(error)
    return harness.classify(error) == "transient"


async def _wait(ctx, attempt: int, base: float, *, why: str) -> float:
    """Back off, and say so while it happens.

    A run that is waiting looks exactly like a run that is wedged, and at the
    shipped retry settings the gap between them runs to about half a minute per
    call (2026-09-13). `stage_progress` is where the live signal goes -- the
    channel for what is happening RIGHT NOW, kept on the stage rather than in
    the log -- so a wait that recovers leaves no permanent mark on a run that
    took no damage, which `warn` would.

    Cleared on the way out rather than by the next attempt, so a call that goes
    on to succeed does not leave the strip claiming a wait that is over.
    """
    emit = getattr(ctx, "emit", None)
    if emit is None:
        return await budget_mod.backoff(attempt, rng=getattr(ctx, "rng", None), first=base)

    # NO STAGE NAMED: `ctx.emit` defaults to the stage the run has open (C7),
    # and a call does not know which one asked for it.
    #
    # THE REAL DELAY, announced BEFORE it is taken. `next_delay` jitters DOWN
    # to half the base, so saying the base promises a wait up to twice the one
    # happening -- on the one figure a strip reading "retrying in Ns" exists to
    # carry. Asked once, then slept.
    rng = getattr(ctx, "rng", None)
    delay = budget_mod.next_delay(attempt, rng=rng, first=base)
    await emit("stage_progress", None, waiting=True, retry_in=round(delay), why=why)
    await asyncio.sleep(delay)
    await emit("stage_progress", None, waiting=False, retry_in=None)
    return delay


#: The floor, for a caller with no settings to read -- the ceiling an operator
#: actually gets is `llm.retry_attempts`. Kept as a literal because the
#: breaker's own arithmetic is written against a number, and a cap that moved
#: with configuration would make it say nothing.
MAX_ATTEMPTS = 3

#: A headline, bounded. Long enough for the longest real trade-press title with
#: room to spare; short enough that a "title" carrying a paragraph of injected
#: instructions arrives visibly truncated instead of arriving whole.
MAX_TITLE_CHARS = 300


def transcript_emitter(ctx, task: str):
    """Forward the CLI session into the run's event stream, or nothing.

    Returns None when the context cannot emit -- `venona analyze` builds a
    context with no run behind it, and the replay executor ignores the callback
    entirely. Passing None rather than a no-op keeps the "is anyone watching?"
    decision in one place instead of at every call site.

    THE SHAPE OF A SESSION IS ONE VENDOR'S FACT. Each service names its own
    events, so the translation into contract C6's `model_message` belongs to
    the driver -- ask the wrong one and every event maps to nothing, which
    renders as a run that did its work with an empty transcript rather than as
    an error anybody can see. Reached by `getattr` for `provenance`'s reason,
    and the module-level fallback is the translation the replay executor's
    recorded envelopes were written by.
    """
    if getattr(ctx, "_emit", None) is None:
        return None

    harness = getattr(ctx.llm, "harness", None)
    translate = harness.summarize if harness is not None else summarize

    async def emit(raw: dict) -> None:
        payload = translate(raw, task=task)
        if payload is not None:
            await ctx.emit("model_message", getattr(ctx, "stage", None), **payload)

    return emit


@dataclass
class AnalyzeOutcome:
    result: LlmResult
    overall: int | None = None
    tier: str | None = None
    # Every model invocation this document took, retries included. `result` is
    # the last one; these two are what the ledger and the budget actually saw,
    # and they are what the caller must add to its counters.
    calls: int = 1
    cost_usd: float = 0.0

    @property
    def ok(self) -> bool:
        return self.result.ok


async def call_and_record(
    ctx,
    task: str,
    *,
    user_content: str,
    schema: dict[str, Any],
    system_prompt: str = "",
    model: str = "sonnet",
    effort: str = "medium",
    tools: Sequence[str] = (),
    timeout: int | None = None,
    budget: budget_mod.Budget | None = None,
    exempt: bool = False,
    degrade_when_spent: bool = False,
) -> LlmResult:
    """Make a model call AND ledger it. Use this for every non-analyze call.

    A call site that ledgers itself silently bypasses both this and
    `max_calls_per_run` -- `lookup_web` spending real money on WebSearch and
    appearing nowhere in `llm_calls`. A second, third and fourth bypass is how
    "why did this run cost that much" becomes unanswerable.

    `PermissionDenied` is deliberately NOT caught. A denied tool means the
    answer was produced without the capability it needed, and degrading that to
    a result is the failure mode the whole executor contract exists to prevent.

    Retries mirror `analyze_document`'s policy, and for the same reason: these
    are the run's LONGEST calls (a brief is minutes of Opus), and losing one to
    a 429 that a ten-second backoff would have cleared throws away the whole
    call. The budget is re-checked before each retry, so a retry storm cannot
    spend past the cap.

    An open circuit comes back as a RESULT carrying the refusal, not a raise.
    `BreakerOpen` is deliberately not an `LlmError`, so letting it propagate
    bypasses every `except LlmError` degrade path -- the brief's, the
    per-cluster skip -- and aborts the run at the first refusal: no brief, and
    `curate` and the projection skipped behind it. As an error result it takes
    the same path an `is_error` envelope does at every call site, and the
    ledger row says why nothing was asked.

    `degrade_when_spent` is the caller PROMISING it can tell "no answer was
    obtained" from "the answer was nothing" -- see the branch below for why the
    default is the exception. `test_only_a_caller_that_reports_it_may_degrade`
    holds the set allowed to opt in, and it is `discovery` alone.

    `exempt` skips the FIRST-attempt check, and ONLY the brief may pass it. The
    carve-out is for the brief alone -- one call, where a near-cap run skipping
    it entirely is the worst possible degradation. An unconditional version (`if
    attempt and budget is not None`) bounds no call site on its first attempt,
    and `define_write`, `adjudicate`, `acronym_read` and `discovery` do not check
    the budget in their own loops before dispatch. Measured 2026-08-06 on `run:06a8160f`:
    152 charged calls against a cap of 120, a 27% overshoot, while the run
    simultaneously deferred 107 clusters, 8 acronym lookups and 9 definitions for
    being out of budget. Retries are not the leak; first attempts are.
    """
    # The run's own ceiling, when the caller named none. Every run carries one,
    # so a call site that forgets to thread it is bounded anyway rather than
    # being the one unbounded path.
    if budget is None:
        budget = getattr(ctx, "budget", None)
    emit = transcript_emitter(ctx, task)
    harness_key, billing = provenance(ctx.llm, ctx.settings)
    model = resolve_model(ctx.llm, model)
    tools, unavailable = narrow_to_capabilities(ctx.llm, tools)
    if unavailable:
        ctx.note(
            f"{task}: this model service cannot {', '.join(sorted(unavailable))}, "
            f"so the answer was found without it"
        )
    result: LlmResult | None = None

    attempts = max(1, ctx.settings.llm.retry_attempts)
    base_wait = ctx.settings.llm.retry_base_seconds
    for attempt in range(attempts):
        if budget is not None and (attempt or not exempt):
            budget.check()
        try:
            result = await ctx.llm.call(
                task,
                user_content=user_content,
                schema=schema,
                system_prompt=system_prompt,
                model=model,
                effort=effort,
                tools=tools,
                timeout=timeout,
                on_event=emit,
                # THE RUN'S OWN: a shared lane executor otherwise reports one
                # run's wait into another's stream.
                on_wait=getattr(ctx, "on_wait", None),
                wait_abort=getattr(ctx, "wait_abort", None),
            )
        except PermissionDenied:
            raise
        except BreakerOpen as exc:
            _record_failed_call(
                ctx, task=task, model=model, effort=effort, tools=tools, error=str(exc)
            )
            return LlmResult(data=None, usage=Usage(), error=str(exc))
        except LlmError as exc:
            # Ledger the attempt even though it produced no envelope. This
            # module's contract is that EVERY call lands in `llm_calls`. A
            # raising call that leaves no row makes a brief that burned three
            # minutes of Opus and then threw invisible in both the ledger and
            # the run's cost.
            _record_failed_call(
                ctx, task=task, model=model, effort=effort, tools=tools, error=str(exc)
            )
            if not is_transient(ctx.llm, str(exc)):
                raise
            if attempt == attempts - 1:
                # SPENT PATIENCE IS A RESULT ONLY WHERE THE CALLER CAN SAY SO.
                # Nothing more can be obtained right now, which is the same
                # fact an open circuit reports -- but "no answer was obtained"
                # and "the answer was nothing" are opposite claims, and several
                # callers WRITE the second one down. `glossary.lookup_all`
                # stamps every surface `undefined` with an `attempted_run_id`,
                # and `last_attempt_at IS NULL` then excludes them from every
                # later sweep: one denial there, its own comment says, is
                # "three hundred questions quietly closed".
                if not degrade_when_spent:
                    raise
                return LlmResult(data=None, usage=Usage(), error=str(exc))
            await _wait(ctx, attempt, base_wait, why=str(exc))
            continue

        record_call(
            ctx.db,
            call_id=ctx.ids.uuid7(),
            run_id=ctx.run_id,
            task=task,
            result=result,
            model=model,
            effort=effort,
            tools=tools,
            created_at=ctx.clock.now(),
            # The open stage; `STAGE_FOR_TASK` is the fallback outside one.
            stage=getattr(ctx, "stage", None),
            harness=harness_key,
            billing_mode=billing,
        )
        await ctx.counter("llm_calls")
        await ctx.counter("cost_usd", result.cost_usd)
        if budget is not None:
            budget.charge(result.cost_usd)

        if result.error and is_transient(ctx.llm, result.error) and attempt < attempts - 1:
            await _wait(ctx, attempt, base_wait, why=result.error or "")
            continue
        return result

    assert result is not None
    return result


def _record_failed_call(
    ctx, *, task: str, model: str, effort: str, tools: Sequence[str], error: str
) -> None:
    """A row for a call that raised before returning an envelope.

    Cost is 0 because the envelope carried it and never arrived -- the row
    exists so the attempt is visible at all, not to account for it. `llm_calls`
    is deliberately NOT incremented: that counter tracks charged calls.
    """
    record_call(
        ctx.db,
        call_id=ctx.ids.uuid7(),
        run_id=ctx.run_id,
        task=task,
        result=LlmResult(data=None, usage=Usage(), error=error),
        model=model,
        effort=effort,
        tools=tools,
        created_at=ctx.clock.now(),
        stage=getattr(ctx, "stage", None),
    )


def build_user_content(
    *,
    title: str,
    url: str,
    publisher: str | None,
    published_at: str | None,
    text: str,
    max_chars: int,
) -> str:
    """The per-document half of the prompt.

    Everything here is per-call, so it goes in the USER turn -- putting any of
    it in the system prefix would break prompt caching for the rest of the run.

    Long documents are truncated head+tail rather than head-only: the closing
    paragraphs of an article routinely carry the award value, the competitors,
    and the schedule.

    EVERY FIELD HERE IS ADVERSARY-INFLUENCEABLE (ADR 0166). Whoever wrote the
    page chose the title and the body, and the cheapest way to reach this
    function is to publish something the discovery sweep will fetch. So the
    title goes through `inline` -- a newline in one would otherwise forge a
    `PUBLISHER:` line -- and the body goes in an envelope.
    """
    body = text.strip()
    if len(body) > max_chars:
        head = body[: int(max_chars * 0.7)]
        tail = body[-int(max_chars * 0.3) :]
        body = f"{head}\n\n[... truncated ...]\n\n{tail}"

    meta = [f"TITLE: {inline(title, limit=MAX_TITLE_CHARS)}", f"URL: {inline(url, limit=2048)}"]
    if publisher:
        meta.append(f"PUBLISHER: {inline(publisher, limit=200)}")
    if published_at:
        meta.append(f"PUBLISHED: {inline(published_at, limit=64)}")

    # `limit=None`: the head+tail truncation above already bounded this, and it
    # kept the tail on purpose. Letting the envelope re-truncate would drop the
    # half the truncation went out of its way to preserve.
    enveloped = quote(body, kind="article-body", limit=None, keep_lines=True)
    return "\n".join(meta) + "\n\n---\n\n" + enveloped


async def _sleep_and_say(on_wait, attempt: int, base: float, *, rng, why: str) -> float:
    """The backoff, reported through whatever channel the caller has.

    `analyze_document` takes a connection and settings rather than a context,
    so it cannot emit for itself -- and analyze, which runs for well over an
    hour, is the stage this signal exists for: `LiveRunPanel.vue`'s strip
    "cannot tell working from wedged", and a call waiting out a provider is
    that with the count frozen too.
    """
    delay = budget_mod.next_delay(attempt, rng=rng, first=base)
    if on_wait is not None:
        await on_wait(delay, why)
    await asyncio.sleep(delay)
    if on_wait is not None:
        await on_wait(None, why)
    return delay


async def analyze_document(
    *,
    conn: db.Conn,
    llm: LlmExecutor,
    settings: Settings,
    title: str,
    url: str,
    text: str,
    publisher: str | None = None,
    published_at: str | None = None,
    run_id: str | None = None,
    call_id: str = "call-0001",
    created_at: str = "",
    #: The stage the RUN has open, which is not always `analyze`: a sweep
    #: analyses inside its own `research` stage.
    stage: str | None = None,
    subject: str | None = None,
    profile: Profile | None = None,
    system_prompt: str | None = None,
    budget: budget_mod.Budget | None = None,
    rng=None,
    on_event=None,
    on_wait=None,
) -> AnalyzeOutcome:
    profile = profile or load_profile(settings)
    system_prompt = system_prompt or analyze_system_prompt(conn, profile)
    schema = analyze_schema()

    user_content = build_user_content(
        title=title,
        url=url,
        publisher=publisher,
        published_at=published_at,
        text=text,
        max_chars=settings.llm.max_doc_chars,
    )

    # EVERY attempt is ledgered and charged, not just the surviving one.
    #
    # Ledgering after the loop instead makes a document that hit a 429 twice
    # into three invocations, one `llm_calls` row and one call against
    # `max_calls_per_run` -- so a rate-limit storm, exactly when a run is
    # burning its allowance fastest, is the moment the cap stops seeing the
    # spend. That is the "second, third and fourth bypass" `call_and_record`'s
    # docstring says this ledger exists to prevent.
    result: LlmResult | None = None
    calls = 0
    spent = 0.0
    harness_key, billing = provenance(llm, settings)
    model = resolve_model(llm, settings.llm.analyze_model)

    attempts = max(1, settings.llm.retry_attempts)
    base_wait = settings.llm.retry_base_seconds
    for attempt in range(attempts):
        # Re-checked per attempt: a retry is a fresh call and may be the one
        # that crosses the cap.
        if budget is not None:
            budget.check()
        try:
            result = await llm.call(
                "analyze",
                user_content=user_content,
                schema=schema,
                system_prompt=system_prompt,
                model=model,
                effort=settings.llm.analyze_effort,
                on_event=on_event,
            )
        except PermissionDenied:
            # Never retried and never swallowed: a denied tool means the answer
            # was produced without the capability it needed.
            raise
        except BreakerOpen as exc:
            # A refusal is this cluster's outcome, not the stage's: the caller
            # skips it with a warning and the cluster stays unanalyzed for the
            # next run, exactly as it does for an `is_error` envelope.
            result = LlmResult(data=None, usage=Usage(), error=str(exc))
            break
        except LlmError as exc:
            # No result, so nothing to ledger -- there is no usage to record.
            # RAISED, and the caller is already built for it: `analyze`
            # gathers with `return_exceptions=True`, so one cluster's terminal
            # failure is skipped with a warning rather than taking the stage.
            # Degrading to a result here would take the quieter path and lose
            # that warning, which is the one thing telling a reader a story
            # went unread.
            if attempt == attempts - 1 or not is_transient(llm, str(exc)):
                raise
            await _sleep_and_say(on_wait, attempt, base_wait, rng=rng, why=str(exc))
            continue

        calls += 1
        spent += result.cost_usd
        if created_at:
            record_call(
                conn,
                # Retries need their own row; `record_call` upserts on id, so
                # reusing one would overwrite the attempt it is meant to record.
                call_id=call_id if attempt == 0 else f"{call_id}:retry{attempt}",
                run_id=run_id,
                task="analyze",
                result=result,
                model=model,
                effort=settings.llm.analyze_effort,
                tools=(),
                created_at=created_at,
                stage=stage,
                # The cluster this call was about. Every analyze call shares one
                # task name, so without it "which cluster took 53 seconds" has
                # no answer.
                subject=subject,
                harness=harness_key,
                billing_mode=billing,
            )
        if budget is not None:
            budget.charge(result.cost_usd)

        if result.error and is_transient(llm, result.error) and attempt < attempts - 1:
            await _sleep_and_say(on_wait, attempt, base_wait, rng=rng, why=result.error or "")
            continue
        break

    assert result is not None

    if not result.ok:
        return AnalyzeOutcome(result=result, calls=calls, cost_usd=spent)

    overall = score_from(result.data or {})
    return AnalyzeOutcome(
        result=result, overall=overall, tier=tier_for(overall), calls=calls, cost_usd=spent
    )


def score_from(data: dict[str, Any]) -> int:
    """Roll the model's per-axis scores into 0-100, in Python.

    The model is never asked for the overall. Keeping it here makes it
    deterministic and lets weights be retuned with no model calls.
    """
    axes = (data.get("relevance") or {}).get("axes") or {}
    scores = {axis_id: float((value or {}).get("score", 0)) for axis_id, value in axes.items()}
    return compute_overall(scores)
