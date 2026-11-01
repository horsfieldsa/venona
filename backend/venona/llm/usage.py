"""The model-call ledger.

Every call lands in `llm_calls`, successful or not. This is what answers "why
did this run cost that much", "is prompt caching actually working", and "did
something get refused" -- none of which are answerable after the fact if the
call was not recorded.

`models` is stored as a JSON map because every invocation returns a `modelUsage`
map containing a phantom `claude-haiku-4-5` entry alongside the requested model.
Cost is the sum over that map; keying on "the model I asked for" under-reports.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence

from venona.db import core as db
from venona.db.repositories import quota as quota_repo
from venona.llm.executor import LlmResult

# `task` maps to a pipeline stage by CONVENTION only -- `discovery` runs inside
# fetch, `adjudicate` inside curate, `define_*` inside define. That convention
# was in three people's heads and nowhere in the data, so "where did the 52
# minutes go" was a guess. Recording it makes the answer a GROUP BY.
#
# A FALLBACK, not the source: `RunContext.stage` is where a call's stage comes
# from, and this answers only for one made outside any stage. A convention
# cannot see which run it is in -- a sweep analyses inside its own `research`
# stage -- so a map alone attributes to the task and not to the run.
#
# A task absent from both records `stage=NULL` rather than guessing: an
# unattributed call is honest, a mis-attributed one corrupts the very number
# this exists to produce.
STAGE_FOR_TASK: dict[str, str] = {
    "discovery": "fetch",
    "analyze": "analyze",
    "acronym_search": "acronyms",
    "acronym_read": "acronyms",
    "acronym_adjudicate": "acronyms",
    "brief": "brief",
    "define_search": "define",
    "define_read": "define",
    "define_write": "define",
    "define_extract": "define",
    "adjudicate": "curate",
    # Neither of these runs INSIDE a pipeline stage -- each is its own
    # light-lane run kind whose single stage shares its name. They are here for
    # the reason the map exists: absent, every call ledgers `stage=NULL`, and a
    # GROUP BY over `llm_calls` attributes the reader-facing spend to nothing at
    # all. "Is the personal brief worth what it costs" is a question the ledger
    # could not answer.
    "personal_brief": "personal_brief",
    "questions": "questions",
    "setup_survey": "survey",
    "setup_sources": "sources",
    "setup_ontology": "ontology",
    "setup_domain": "profile",
    "setup_relevance": "profile",
    "setup_research_prompt": "profile",
    "setup_brief_prompt": "profile",
    "setup_rubric": "profile",
    "setup_sections": "profile",
    "setup_lexicon": "profile",
    "setup_anchors_trunk": "anchors",
    "setup_anchors_expand": "anchors",
    # The adjudication the anchors stage runs over its own proposals.
    "anchor": "anchors",
}


def record_call(
    conn: db.Conn,
    *,
    call_id: str,
    run_id: str | None,
    task: str,
    result: LlmResult,
    model: str,
    effort: str,
    tools: Sequence[str],
    created_at: str,
    stage: str | None = None,
    subject: str | None = None,
    harness: str | None = None,
    billing_mode: str | None = None,
) -> None:
    """`subject` is the thing the call was ABOUT -- a cluster id, an entity id,
    an acronym surface. Without it a per-item latency question ("which cluster
    took 53 seconds?") is unanswerable, because `task` is shared by every call
    in a stage.

    `harness` and `billing_mode` are OPTIONAL and default to NULL rather than
    to the service configured now. A row written before there was more than one
    service says nothing about which one ran, and answering with today's
    setting would invent a provenance nobody recorded -- the same reason the
    outcome column refuses to be backfilled.
    """
    status = "ok" if result.ok else ("refused" if result.refused else "error")
    # Upsert rather than insert: in a real run ids come from ctx.ids.uuid7() and
    # never collide, but re-running the same manual command (or replaying a
    # fixture) should update the row rather than abort.
    db.upsert(
        conn,
        "llm_calls",
        {
            "id": call_id,
            "run_id": run_id,
            "task": task,
            "cache_key": result.cache_key,
            "model": model,
            "models": db.jdump(result.models),
            "effort": effort,
            "tools": db.jdump(list(tools)),
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cache_read_input_tokens": result.usage.cache_read_input_tokens,
            "cache_creation_input_tokens": result.usage.cache_creation_input_tokens,
            "web_search_requests": result.usage.web_search_requests,
            "web_fetch_requests": result.usage.web_fetch_requests,
            "cost_usd": result.cost_usd,
            "duration_ms": result.duration_ms,
            "status": status,
            "error": result.error,
            "created_at": created_at,
            "stage": stage or STAGE_FOR_TASK.get(task),
            "subject": subject,
            "harness": harness,
            "billing_mode": billing_mode,
            # What `cost_usd` MEANS on this row. Four kinds of number share the
            # column -- a subscription's shadow price at published rates, what
            # a metered provider billed, tokens multiplied by a price table,
            # and a plain zero -- and an aggregate that cannot tell them apart
            # is how a chart claims a bill nobody received.
            "cost_basis": result.cost_basis if harness else None,
        },
        key=["id"],
    )
    _record_quota(
        conn,
        result=result,
        run_id=run_id,
        observed_at=created_at,
        call_id=call_id,
        harness=harness,
    )


def _record_quota(
    conn: db.Conn,
    *,
    result: LlmResult,
    run_id: str | None,
    observed_at: str,
    call_id: str,
    harness: str | None = None,
) -> None:
    """Ledger what the call learned about the subscription's remaining room.

    Hung off `record_call` rather than off either caller because there are two
    ledger paths -- `runner.call_and_record` and `analyze_document` -- and a
    reading captured on only one of them would under-report exactly during
    analyze, which is where a research run spends most of its window.

    `observed_at` is the caller's `created_at`, which is already `ctx.clock`
    derived. Nothing here asks what time it is; see ADR 0057 on why this is
    measurement and not a cap.

    `harness` is recorded because a window belongs to the service that reported
    it. Without it, switching services leaves both sets of windows in the
    latest-per-window read forever, half of them frozen at the moment a
    previous service last reported, and two rows claiming to be the binding one.

    The sample id is derived from the call id and the window name rather than
    drawn from `ctx.ids`, because `record_call` has no id source and the
    uniqueness that matters is enforced by the index, not by this string.

    Inside a savepoint, and AFTER the ledger row, because this is the auxiliary
    write of the two. `record_call` often runs inside a caller's transaction, so
    a shape the CLI grows that this does not expect would otherwise abort that
    transaction and take the `llm_calls` row down with it -- trading the record
    of what a run did for a reading of what it had left. The savepoint is the
    nested unit of work that is allowed to fail; see `db.savepoint`.
    """
    limits = result.rate_limits
    if limits is None:
        return

    for window in limits.windows:
        with contextlib.suppress(Exception), db.savepoint(conn, "quota_sample"):
            quota_repo.record_sample(
                conn,
                sample_id=f"{call_id}:{window.name}",
                observed_at=observed_at,
                harness=harness,
                window_name=window.name,
                utilization=window.utilization,
                resets_at=window.resets_at,
                status=limits.status,
                representative=window.name == limits.representative,
                run_id=run_id,
            )
