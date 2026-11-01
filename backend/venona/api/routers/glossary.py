from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from venona.api.authz import actor_id
from venona.api.deps import Window, get_db, page_window
from venona.api.schemas.glossary import (
    AttachEntityResult,
    ChooseResult,
    CorpusAudit,
    CorroborateResult,
    GlossaryEntry,
    GlossaryList,
    GlossarySurface,
    KeepBothResult,
    LookupStarted,
    NotAnAcronymResult,
    ReopenResult,
    SameThingResult,
)
from venona.api.schemas.run import RunPreview, RunStarted
from venona.api.schemas.weburl import OptionalWebUrl
from venona.db import core as db
from venona.db.models import normalize, verify_expansion
from venona.db.repositories import glossary as repo
from venona.pipeline import web_grounding
from venona.pipeline.types import resolve_cap, utc_now_iso
from venona.services import run_launcher

router = APIRouter(prefix="/glossary", tags=["glossary"])

#: The corpus-and-definitions stamp as of the last full corroboration, per
#: database. See `corroborate_all` for why a repeat press is a guaranteed no-op.
_LAST_CORROBORATION: dict[str, tuple] = {}


class GlossaryPatch(BaseModel):
    """Manual curation. `method` becomes `manual` on any edit -- a human
    decision must be distinguishable from an extracted one in the audit trail."""

    expansion: str | None = None
    status: str | None = None  # active | rejected | undefined
    entity_id: str | None = None
    # Required alongside a hand-entered `expansion`. Invariant 3 waives the
    # SENTENCE check for a curator, not the audit trail: six months on, "where
    # did this come from" must still have an answer. Refused here rather than
    # quietly dropped at render, because it ends up in a card's `href`.
    grounding_url: OptionalWebUrl = None


def _col(row: db.Row, name: str):
    """Tolerate rows selected without the join (the PATCH round-trip)."""
    return row[name] if name in row.keys() else None


def _row_to_entry(conn: db.Conn, row: db.Row) -> dict[str, Any]:
    article = {
        "title": _col(row, "doc_title"),
        "url": _col(row, "doc_url"),
        "publisher": _col(row, "doc_publisher"),
    }

    return {
        "id": row["id"],
        "surface": row["surface"],
        "expansion": row["expansion"],
        "entity_id": row["entity_id"],
        "method": row["method"],
        "confidence": row["confidence"],
        # How many independent sources produced this expansion: the queue renders
        # it beside the confidence, because "0.55" alone invites "0.55 from what?"
        "corroboration_count": _col(row, "corroboration_count") or 1,
        "status": row["status"],
        "sighting_count": row["sighting_count"],
        "article_count": row["article_count"],
        # NULL means nobody has ruled on this row -- honestly different from
        # "reviewed and left alone", and what makes a rejection durable.
        "decided_at": _col(row, "decided_at"),
        # The address, joined by `ENTRY_SELECT`: on the one surface where two
        # curators reaching opposite verdicts is ordinary, WHO ruled matters.
        "decided_by_email": _col(row, "decided_by_email"),
        # NULL means no web lookup has ever run for this surface; set means one
        # ran and grounded nothing, which is what stops the card offering a
        # first-time lookup for a question already answered.
        "last_attempt_at": _col(row, "last_attempt_at"),
        "last_attempt_run_id": _col(row, "last_attempt_run_id"),
        "attempt_count": _col(row, "attempt_count") or 0,
        "grounding": {
            "quote": row["grounding_quote"],
            "url": row["grounding_url"] or article["url"],
            "article_id": row["grounding_article_id"],
            "article_title": article["title"],
            "publisher": article["publisher"],
            # Where the defining sentence sits in the source document.
            # `ground_in_article` writes this for every tier-1 grounding and
            # nothing read it back (220 rows, 2026-08-09), which is how it went
            # silently discarded on corroboration. Serving it makes the data
            # reachable and the discard detectable.
            "span": db.jload(_col(row, "grounding_span"), None),
        },
    }


@router.get("", response_model=GlossaryList)
def list_glossary(
    status: str | None = Query(None),
    q: str | None = Query(None),
    window: Window = Depends(page_window(default=200, cap=500)),
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """The review queue, one page at a time.

    Ordering puts GROUNDED entries first, then frequency: sorting by frequency
    alone buried the 25 definitions worth reviewing under 92 undefined ones
    (2026-08-06), and the undefined list is a backlog, not the headline.

    `status` takes a comma-separated set because the UI's "grounded" filter is
    two statuses (`active,ambiguous`), and `q` filters server-side -- both must
    move here with the paging, or a filter would only search the rows that happen
    to be loaded. `counts` stays whole-table so the chips read true from any page;
    `total` is the filtered set's size.
    """
    statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
    query = (q or "").strip() or None
    rows = repo.listing(conn, status=statuses, q=query, limit=window.limit, offset=window.offset)
    counts = repo.status_counts(conn)
    total = repo.listing_total(conn, status=statuses, q=query)
    return {
        "items": [_row_to_entry(conn, r) for r in rows],
        "counts": counts,
        "total": total,
    }


# What one tier-3 lookup costs and takes, when this machine has measured nothing
# yet (2026-08-09). Both give way to its own average the moment it has done one.
FALLBACK_LOOKUP_USD = 0.08
FALLBACK_LOOKUP_SECONDS = 30.0

# How many past lookups the averages are taken over. Recent, because the model and
# the page fetches per lookup both change -- a mean over all time means a different
# system.
COST_SAMPLE = 25

#: How many readings of one surface a single read returns. A surface has a
#: handful; this is a ceiling on the pathological case rather than a page, which
#: is why nothing pages past it. `ambiguous` is counted whole-set regardless.
SURFACE_ENTRY_LIMIT = 50


def _lane_harness(request: Request) -> str:
    """The service that will run the sweep. See `run_launcher.lane_harness`,
    which two routers quote a measured price from."""
    from venona.services import run_launcher, run_types

    return run_launcher.lane_harness(request, run_types.LIGHT)


def _lookup_stats(conn: db.Conn, *, harness: str) -> tuple[float, float, int]:
    """What a single grounded lookup has actually cost, and taken, lately.

    Measured, not asserted: the numbers in front of a button that may spend
    twenty dollars and run for two hours should come from this machine's own
    ledger, and they are the only figures a person can check against the Runs
    page.

    Returns `(usd, seconds, sample_size)`. Sample size 0 means the fallbacks are
    in use, and the modal says so rather than presenting a guess as a measurement.

    `cost_usd > 0` filters the sample: offline replays and coalesced runs write
    0.0, and a mean including them trends to zero -- which would put a $24 sweep
    behind a modal quoting pennies. Scoped to the active service for the reason
    `repo.lookup_run_costs` gives; with no samples for it the fallbacks are used.
    """
    rows = repo.lookup_run_costs(conn, limit=COST_SAMPLE, harness=harness)
    if not rows:
        return FALLBACK_LOOKUP_USD, FALLBACK_LOOKUP_SECONDS, 0

    usd = sum(r["cost_usd"] for r in rows) / len(rows)
    # Parsed in Python, not in SQL: `started_at`/`finished_at` are TEXT, so a SQL
    # version would need a cast on every row, and `_elapsed_seconds` already
    # returns None for the half-written pair (a started run with no
    # `finished_at`) that the filter below depends on.
    spans = [_elapsed_seconds(r["started_at"], r["finished_at"]) for r in rows]
    measured = [s for s in spans if s is not None and s > 0]
    seconds = sum(measured) / len(measured) if measured else FALLBACK_LOOKUP_SECONDS
    return usd, seconds, len(rows)


def _elapsed_seconds(started: str | None, finished: str | None) -> float | None:
    """Wall clock between two run timestamps, or None if either is unusable.

    A run still in flight has no `finished_at`, and neither that nor a format
    this cannot read is worth an exception on a read path whose job is to
    estimate.
    """
    if not started or not finished:
        return None
    try:
        return (
            datetime.fromisoformat(finished.replace("Z", "+00:00"))
            - datetime.fromisoformat(started.replace("Z", "+00:00"))
        ).total_seconds()
    except ValueError:
        return None


@router.get("/lookup-all/preview", response_model=RunPreview)
def lookup_all_preview(
    request: Request,
    retry: bool = Query(False),
    limit: int | None = Query(None, ge=1),
    uncapped: bool = Query(False),
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """What "Look up all" would do, and what it would cost -- before it does it.

    The confirmation modal is only worth having if its numbers are the run's
    numbers, so this calls `undefined_candidates` -- the same function, same
    arguments -- rather than counting undefined rows itself. A preview counting
    the 303-row queue while the run attempted the 180 never tried would quote
    nearly double and be believed (2026-08-09).

    `skipped_attempted` is reported separately because it is the one number a
    person acts on: what `retry=true` would add back. Without it the difference
    between the queue length and the estimate looks like a bug.

    Every parameter that changes what the run takes on -- `retry`, `limit`,
    `uncapped` -- is a parameter here too, resolved by the same expression. A
    preview that ignored one would quote the batch while the run took the queue.
    """
    # Imported here, like every pipeline import in this file: the module pulls in
    # trafilatura and the executor, and that second is paid on every dev reload.
    from venona.pipeline.acronyms import undefined_candidates

    settings = request.app.state.settings
    cap = None if uncapped else resolve_cap(limit, settings.pipeline.acronym_lookup_all_cap)

    eligible = undefined_candidates(conn, retry=retry)
    # With `retry` already on this counts what is in the list; with it off, the
    # size of the gap between this list and the queue, otherwise unexplained.
    attempted = (
        sum(1 for r in eligible if r["last_attempt_at"])
        if retry
        else len(undefined_candidates(conn, retry=True)) - len(eligible)
    )

    will_attempt = min(len(eligible), cap) if cap is not None else len(eligible)
    per_lookup, per_lookup_seconds, sample = _lookup_stats(conn, harness=_lane_harness(request))

    return {
        "eligible": len(eligible),
        "will_attempt": will_attempt,
        "cap": cap,
        "unit": "acronym",
        "unit_plural": "acronyms",
        # `skipped_attempted` and `retry` belong to this sweep alone, so they
        # travel in `extra` and the shared dialog renders them through this
        # caller's own slot rather than growing an opinion about retries.
        "extra": {"skipped_attempted": attempted, "retry": retry},
        "per_item_usd": round(per_lookup, 4),
        "estimate_usd": round(will_attempt * per_lookup, 2),
        # Sequential -- one surface at a time, so a cancel lands within one
        # lookup. Which makes the total the sum, and on a three hundred row queue
        # that is hours; a modal quoting only money would tell half the truth.
        "per_item_seconds": round(per_lookup_seconds, 1),
        "estimate_seconds": round(will_attempt * per_lookup_seconds),
        # 0 means the per-lookup figures are the fallbacks, not measurements.
        # The modal must not present a guess in the same words as an average.
        "cost_sample": sample,
        # The head of the queue, so the modal can show WHAT it is about to buy.
        "sample": [r["surface"] for r in eligible[:8]],
    }


@router.get("/audit/corpus", response_model=CorpusAudit)
def audit_corpus(conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """Which tier-3 definitions the corpus disagrees with.

    Closes the loop the curation flow could not close on its own. "Look again"
    and the meanings picker settle a surface a person already SUSPECTS -- but
    `BAE` = "brigade aviation element" looked settled: one grounded definition,
    four checks passed. Nobody presses "look again" on a definition that appears
    correct, so the wrong ones sat there.

    The automatic half. It reports; it changes nothing. See
    `services/corpus_check.py` for why absence of the phrase is deliberately NOT
    treated as evidence.
    """
    from venona.services import corpus_check

    results = corpus_check.audit(conn)
    return {
        "checked": len(results),
        "contradicted": [
            {
                "surface": r.surface,
                "expansion": r.expansion,
                "documents": r.documents,
                "always_followed_by": r.name_head,
                "share": r.name_head_share,
            }
            for r in results
            if r.contradicted
        ],
        "corroborated": sum(1 for r in results if r.corroborated),
        "unsupported": sum(1 for r in results if r.verdict == "unsupported"),
    }


@router.post("/audit/corroborate", response_model=CorroborateResult)
def corroborate_all(conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """Re-weigh every tier-3 definition against the corpus as it stands today.

    The corroboration a lookup does at grounding time only sees the corpus of
    that moment. Articles keep arriving, and a definition bought last week may be
    spelled out in something ingested since -- "accuracy improving over time", on
    demand and for free: no model calls, one pass over `documents`.

    Confidence only ever RISES here. `corroborate_from_corpus` adds sources and
    recomputes on the row's existing tier; it never demotes, never rewrites
    provenance, and never touches a row a human has ruled on.
    """
    from venona.pipeline.acronyms import corroborate_from_corpus
    from venona.services import corpus_check

    # NOTHING MOVED, NOTHING TO RE-READ. This is O(documents x definitions) of
    # regex over every body, and a second press a minute after the first cannot
    # find anything the first did not. The stamp covers BOTH inputs, because a
    # human verdict or a fresh grounding changes the answer without a single
    # article arriving. In-process rather than a stored row: a restart paying for
    # one honest pass beats a table that remembers a button press.
    fingerprint = corpus_check.audit_fingerprint(conn)
    if _LAST_CORROBORATION.get(conn.dsn) == fingerprint:
        return {"checked": 0, "definitions_raised": 0, "sources_added": 0, "skipped": True}

    documents = [(row["id"], row["text_nfc"] or "") for row in repo.document_texts(conn)]
    rows = repo.web_grounded_pairs(conn)

    now = utc_now_iso()
    raised = sources = 0
    for row in rows:
        added = corroborate_from_corpus(
            conn, row["surface"], row["expansion"], now=now, documents=documents
        )
        if added:
            raised += 1
            sources += added

    # Stamped AFTER the pass and re-read, not reused: this run may have raised
    # confidences, and the pre-run stamp would make the next press look like work.
    _LAST_CORROBORATION[conn.dsn] = corpus_check.audit_fingerprint(conn)
    return {
        "checked": len(rows),
        "definitions_raised": raised,
        "sources_added": sources,
        "skipped": False,
    }


#: What stopped us reaching a page, in the words of someone who does not work on
#: venona. `fetch_status` and `no_extractable_text` are this repo's names for a
#: refused request and a PDF, and they reach `runs.error`, which the Runs page
#: prints verbatim to a reader deciding whether to press the button again.
_BLOCKED_SAID: dict[str, str] = {
    "fetch_status": "every source refused the request",
    "fetch_error": "no source could be reached",
    "no_extractable_text": "the sources published nothing readable",
    "search_failed": "the search for sources failed",
    "search_failed_twice": "the search for sources failed twice",
    "retry_search_failed": "the second search for sources failed",
    "no_candidates": "no sources were suggested",
    "retry_proposed_nothing_new": "no further sources were suggested",
    "retry_skipped": "the budget for this run ran out",
    "subject_too_long": "the term is too long to look up",
    "subject_empty_after_scrub": "the term is empty",
}


def _unreachable_error(blocked: Counter) -> str:
    """Why a lookup that read nothing read nothing.

    A cause and a count, because those decide what to do next: every host
    refusing us is worth retrying from somewhere else, no source ever being
    suggested is not.
    """
    total = sum(blocked.values())
    if not total:
        return "Nothing was found to read."
    dominant = blocked.most_common(1)[0][0]
    said = _BLOCKED_SAID.get(dominant, "no source could be read")
    return f"Tried {total} source(s) and read none: {said}."


def _overturns(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """The `adjudicated` entries of a trace, as outcome data, or nothing.

    A grounding trace is kept whole only when a lookup FAILS -- a success reports
    the expansion and drops the rest. Right for the reasons a candidate was
    refused, wrong for the one entry that explains why an answer was TAKEN: an
    expansion accepted over the initials check is the row a reader is most
    entitled to interrogate.

    Empty yields no key at all, so a definition the deterministic check accepted
    on its own carries no field suggesting a verdict was involved.
    """
    entries = [
        {k: v for k, v in t.items() if k in ("url", "expansion", "overturned", "how")}
        for t in trace
        if t.get("reason") == "adjudicated"
    ]
    return {"adjudicated": entries} if entries else {}


@router.post(
    "/lookup-all",
    status_code=202,
    response_model=RunStarted,
    # `coalesced` is set only when an identical light-lane run was in flight.
    response_model_exclude_unset=True,
)
async def lookup_all(
    request: Request,
    limit: int | None = Query(None, ge=1),
    uncapped: bool = Query(False),
    retry: bool = Query(False),
) -> dict[str, Any]:
    """Ground the whole undefined acronym queue -- tier 3, in bulk.

    `POST /glossary/{surface}/lookup` over every eligible surface, in one run,
    with one confirmation. Three hundred presses of `L`, each waiting out its
    own thirty-second run, is not a way anyone works through a backlog -- and
    the queue is a backlog by construction: tier 3 inside the pipeline is capped
    at `acronym_lookup_cap_per_run` surfaces a night.

    **One BATCH per press, not the whole queue** (`pipeline.acronym_lookup_all_cap`,
    `?limit=` for a smaller bite, blank for uncapped). Uncapped was the first
    answer -- this is the one tier-3 caller a person explicitly asks for, and the
    button in front of it counts the queue and quotes what the last lookups cost
    before anything happens. A batch is the same work in pieces you can stop
    between. ADR 0114.

    **`?uncapped=true` takes the whole queue in one run.** The batch is a
    DEFAULT, not a ceiling: it exists so nobody starts six hours of lookups by
    accident, and the one person who cannot do it by accident is the one reading
    the count, the price and the duration for the whole queue in the modal. A
    knob only reachable by editing a settings file is a knob a reader does not
    have. ADR 0245.

    **`llm.max_calls_per_run` still bounds it.** A run that hits the cap defers
    the rest, reports how many, and ends `succeeded_partial`. The deferred
    surfaces are still undefined and still eligible for the next press.
    """
    from venona.llm import budget as budget_mod
    from venona.llm.executor import PermissionDenied
    from venona.pipeline.acronyms import (
        contradicted_by_corpus,
        corroborate_from_corpus,
        link_to_entities,
        load_lexicon,
        lookup_web,
        mark_undefined,
        store_definition,
        undefined_candidates,
    )

    settings = request.app.state.settings
    cap = None if uncapped else resolve_cap(limit, settings.pipeline.acronym_lookup_all_cap)

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        # Inside `work`, never before it: `load_lexicon` reads a user-editable
        # YAML, and a raise outside the runner's `try` leaks a registry slot.
        lexicon, _stoplist, _whitelist = load_lexicon(settings)
        rows = undefined_candidates(target.conn, retry=retry, cap=cap)

        # A budget sized to the batch, not to a pipeline run. `llm.max_calls_per_run`
        # is a per-pipeline figure and a tier-3 lookup averages five calls -- one
        # search, up to three reads, the retry round -- so it silently ceilings a
        # sweep at a fraction of what was authorised: measured 2026-08-09, a batch
        # of 100 considered 100, attempted 25 and deferred 75 while the modal had
        # quoted a price for 100. A confirmation accurate about money and wrong
        # about the work it buys is the same defect as a preview counting the
        # wrong rows.
        #
        # Proportional, not removed -- an unbounded sweep is what the confirmation
        # exists to prevent -- and the run still degrades to `succeeded_partial`
        # and reports what it deferred if it hits the new ceiling.
        per_lookup_calls = 6
        budget = budget_mod.Budget(
            settings=settings.llm.model_copy(
                update={
                    "max_calls_per_run": max(
                        settings.llm.max_calls_per_run, len(rows) * per_lookup_calls
                    )
                }
            )
        )
        grounded = 0
        rejected = 0
        deferred = 0
        failed: list[dict[str, str]] = []
        # What grounded, for the corpus check at the end. Collected rather than
        # re-queried so the check runs over exactly what this sweep wrote.
        landed: list[tuple[str, str]] = []
        overturned: list[dict[str, Any]] = []
        contradicted: list[dict[str, str]] = []
        # Read ONCE for the whole sweep; `contradicted_by_corpus` would otherwise
        # scan every document per surface. The end-of-loop corroboration re-reads
        # deliberately: a sweep runs for hours and that half wants the corpus as
        # it is when it finishes, while the guard only needs it as it was when
        # the surface was bought.
        guard_corpus = [
            (row["id"], row["text_nfc"] or "") for row in repo.document_texts(target.conn)
        ]
        # Why the ones that failed, failed -- `{reason: count}` over what
        # `lookup_web` records. A sweep of a hundred that grounds thirty is either
        # thirty definitions or seventy diagnosable defects, and `None` is the
        # same value whether the search found no pages, every host refused us, or
        # the model read a page that never spells the acronym out. Those want
        # three different fixes.
        reasons: dict[str, int] = {}

        for index, row in enumerate(rows):
            # Between surfaces, so a cancel from the Runs page lands within one
            # lookup rather than after the whole queue -- on a three hundred row
            # sweep, the difference between a cancel button and a decoration.
            # `target.check_cancel`, not `ctx.check_cancel`: only the launcher's
            # bridges the registry's flag into the context.
            target.check_cancel()

            # Deferring is not failing: what landed is written, what did not is
            # still undefined and still eligible next time.
            try:
                budget.check()
            except budget_mod.BudgetExceeded:
                # Count here, SAY it once at the end -- warning at this site and
                # the one below turned one budget exhaustion into two entries.
                deferred = len(rows) - index
                break

            surface = row["surface"]
            await target.emit(
                "stage_progress",
                "lookup",
                {
                    "surface": surface,
                    # `current`, not `done` -- see the note in `entities.py`.
                    "current": index,
                    "total": len(rows),
                    "grounded": grounded,
                },
            )

            trace: list[dict[str, Any]] = []
            try:
                result = await lookup_web(
                    target.ctx, surface, lexicon=lexicon, budget=budget, trace=trace
                )
            except PermissionDenied:
                # MUST propagate (contract C2). A denied tool means the answer was
                # produced without the capability it needed; degrading it here
                # would mark every remaining surface `undefined` on a lookup that
                # never ran, and `last_attempt_at` would stop the card offering
                # them again. One denial, three hundred questions quietly closed.
                raise
            except Exception as exc:  # noqa: BLE001 - one dead page is not a run failure
                failed.append({"surface": surface, "error": f"{type(exc).__name__}: {exc}"})
                continue

            if result is None:
                # `sightings` preserves the existing count -- passing 0 would make
                # an acronym seen 8 times read as never seen, purely because
                # someone tried to define it.
                mark_undefined(
                    target.conn,
                    surface,
                    now=target.ctx.clock.now(),
                    sightings=row["sighting_count"],
                    articles=row["article_count"] or 1,
                    attempted_run_id=target.run_id,
                )
                rejected += 1
                await target.ctx.counter("acronyms_undefined")

                # The LAST reason is the closest this surface got to a definition.
                # Trace entries are not equivalent -- `fetch_status` says a server
                # refused us, `initials_mismatch` says we read a real definition
                # and threw it away -- so counting all of them would report three
                # dead hosts and one genuine near-miss as 75% network trouble.
                closest = trace[-1]["reason"] if trace else "no_trace"
                reasons[closest] = reasons.get(closest, 0) + 1
                # Persisted per surface, so the analysis afterwards can ask WHICH
                # acronyms failed which way. `run_events` already holds it.
                await target.emit(
                    "stage_progress",
                    "lookup",
                    {
                        "surface": surface,
                        "grounded": False,
                        "reason": closest,
                        "trace": [
                            {k: v for k, v in t.items() if k in ("url", "reason", "expansion")}
                            for t in trace[-4:]
                        ],
                    },
                )
                continue

            expansion, quote, url = result
            if contradicted_by_corpus(target.conn, surface, expansion, documents=guard_corpus):
                # A page asserts it and our own documents assert otherwise. Named
                # on the run so the guard is as visible as what it lets through.
                rejected += 1
                contradicted.append({"surface": surface, "expansion": expansion})
                reasons["off_domain"] = reasons.get("off_domain", 0) + 1
                mark_undefined(
                    target.conn,
                    surface,
                    now=target.ctx.clock.now(),
                    sightings=row["sighting_count"],
                    articles=row["article_count"] or 1,
                    attempted_run_id=target.run_id,
                )
                await target.ctx.counter("acronyms_undefined")
                target.conn.commit()
                continue
            store_definition(
                target.conn,
                surface=surface,
                expansion=expansion,
                method="web_grounded",
                article_id=None,
                quote=quote,
                url=url,
                now=target.ctx.clock.now(),
            )
            grounded += 1
            landed.append((surface, expansion))
            # Kept per surface, not per candidate: the question is which
            # DEFINITIONS rest on a verdict, and success discards the trace.
            overturned.extend(
                {"surface": surface, **entry} for entry in _overturns(trace).get("adjudicated", ())
            )
            await target.ctx.counter("acronyms_grounded")
            # Explicit, though `db.connect` opens in autocommit. It is here for
            # what this loop needs to be true: a sweep runs for hours, and a
            # cancel or a crash two thirds through must keep the definitions
            # already paid for -- a later isolation change cannot take that away.
            target.conn.commit()

        # Once, at the end. A new definition is an alias, and without this the
        # hover card shows every one undefined until the next full run -- but it
        # is a table scan, so not inside the loop.
        if grounded:
            link_to_entities(target.conn)
            # Same corpus check the single lookup runs, with the document scan --
            # the expensive half, identical for every surface -- done ONCE and
            # threaded through rather than a hundred times.
            corpus = [
                (row["id"], row["text_nfc"] or "") for row in repo.document_texts(target.conn)
            ]
            for landed_surface, landed_expansion in landed:
                corroborate_from_corpus(
                    target.conn,
                    landed_surface,
                    landed_expansion,
                    now=target.ctx.clock.now(),
                    documents=corpus,
                )
            target.conn.commit()

        attempted = grounded + rejected + len(failed)
        data = {
            "considered": len(rows),
            "grounded": grounded,
            "rejected": rejected,
            "deferred": deferred,
            "failed": failed[:20],
            "retry": retry,
            # The headline number this feature is judged on, computed here rather
            # than left for a reader to divide two numbers on the run detail page.
            "grounded_pct": round(100 * grounded / attempted) if attempted else None,
            "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        }
        if overturned:
            data["adjudicated"] = overturned
        if contradicted:
            data["off_domain"] = contradicted
        # A sweep that tried two hundred surfaces and landed none must not read
        # like one that landed two hundred -- a statement about the ANSWER, so it
        # is the outcome. `deferred` is the one genuine degradation here: work
        # this run was asked to do and did not attempt, and it says so through
        # `ctx.warn`, the channel that decides the status.
        if deferred:
            target.ctx.warn(budget.deferral(deferred, "terms to look up"))
        if not attempted:
            verdict = "nothing_to_do"
        elif grounded == 0:
            verdict = "nothing_found"
        elif grounded < attempted:
            verdict = "some"
        else:
            verdict = "delivered"
        return run_launcher.LaunchOutcome(outcome=verdict, data=data)

    started = await run_launcher.launch(
        request,
        kind="lookup_all",
        # `target_kind` matches the single lookup's params, so a reader of the
        # runs table can tell an acronym sweep from an entity one.
        params={"target_kind": "acronym", "limit": cap, "retry": retry},
        work=work,
        stage="lookup",
    )
    return started


@router.get("/{surface}", response_model=GlossarySurface)
def get_entry(surface: str, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """All accepted expansions for a surface.

    More than one means ambiguous -- the card shows a disambiguation list rather
    than picking, because picking would be a silent, unrecoverable error.
    """
    normalized = normalize(surface)
    rows = repo.by_surface(conn, normalized, limit=SURFACE_ENTRY_LIMIT)
    if not rows:
        raise HTTPException(404, detail=f"no glossary entry for {surface!r}")

    entries = [_row_to_entry(conn, r) for r in rows]
    return {
        "surface": rows[0]["surface"],
        # Counted over the whole surface, never over `entries`: those are a
        # bounded slice, and whether a surface is ambiguous is a fact about all
        # of its readings. `live_reading_count` carries why `rejected` is
        # excluded.
        "ambiguous": repo.live_reading_count(conn, normalized) > 1,
        "entries": entries,
    }


@router.post(
    "/{surface}/lookup",
    status_code=202,
    response_model=LookupStarted,
    # `coalesced` is set only when an identical light-lane run was in flight.
    response_model_exclude_unset=True,
)
async def lookup(
    surface: str, request: Request, alternatives: bool = Query(False)
) -> dict[str, Any]:
    """Ground one acronym on demand -- Tier 3, for a single surface.

    **202, not 200.** A lookup is a search call plus up to three page fetches
    plus a read call; on a slow candidate that is well past any sane request
    timeout. Progress goes through the run event log, so the client gets
    `/api/runs/{id}/stream` for free and a drop resumes from `Last-Event-ID`.

    **Does not refuse while a run is active.** It runs in the `light` lane
    (contract C11) alongside the pipeline: a 409 here would guard a global
    registry slot, not the budget -- a lookup constructs its own -- and holding a
    reader's one-acronym question behind a forty-minute pipeline is too high a
    price for that.

    The near-copy is `routers/entities.py::define`; both are `work` closures over
    `run_launcher.launch`, which owns the lifecycle.
    """
    from venona.pipeline.acronyms import (
        contradicted_by_corpus,
        corroborate_from_corpus,
        link_to_entities,
        load_lexicon,
        lookup_web,
        mark_undefined,
        store_definition,
    )

    surface = surface.strip()
    if not surface:
        raise HTTPException(400, detail="empty term")

    settings = request.app.state.settings

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        # `load_lexicon` reads `profile/lexicon.yaml`, which is user-editable, so
        # a stray tab in it raises. That raise once happened outside the runner's
        # `try`: the task died before `finally`, the slot was never released, and
        # every later run answered 409 until restart. Inside `work` it cannot --
        # `launch` calls this from within the block that guarantees release.
        lexicon, _stoplist, _whitelist = load_lexicon(settings)
        trace: list[dict[str, Any]] = []

        # What we already hold. In `alternatives` mode it reframes the search from
        # "define this" to "what ELSE does this mean", the only question a
        # re-lookup is worth paying for -- the same search returns the same answer.
        known = [r["expansion"] for r in repo.expansion_texts(target.conn, normalize(surface))]
        result = await lookup_web(
            target.ctx,
            surface,
            lexicon=lexicon,
            trace=trace,
            collect=alternatives,
            known=known if alternatives else None,
        )

        if alternatives:
            # Every distinct meaning that survived the four checks, stored as
            # siblings. `store_definition` raises the surface to `ambiguous` on
            # the second one by itself.
            fresh = [
                (expansion, quote, url)
                for expansion, quote, url in (result or [])
                if normalize(expansion) not in {normalize(k) for k in known}
            ]
            for expansion, quote, url in fresh:
                store_definition(
                    target.conn,
                    surface=surface,
                    expansion=expansion,
                    method="web_grounded",
                    article_id=None,
                    quote=quote,
                    url=url,
                    now=target.ctx.clock.now(),
                )
            if fresh:
                link_to_entities(target.conn)
            elif not known:
                # "What else does this mean" asked of a surface with NOTHING on
                # file is just a lookup, and must leave the same trace as one:
                # `mark_undefined` stamps `last_attempt_at`, which stops the card
                # offering a first-time lookup and keeps the sweep from paying to
                # ask again. Otherwise this path spends and leaves no record.
                seen = repo.sighting_counts(target.conn, normalize(surface))
                mark_undefined(
                    target.conn,
                    surface,
                    now=target.ctx.clock.now(),
                    sightings=seen["sighting_count"] if seen else 0,
                    articles=seen["article_count"] if seen else 1,
                    attempted_run_id=target.run_id,
                )
            return run_launcher.LaunchOutcome(
                # Finding no NEW meaning is a real answer to "what else does this
                # mean" -- often the correct one -- but it is not landing one.
                # Neither answer is a degraded run, so neither touches `status`.
                outcome="delivered" if fresh else "nothing_found",
                data={
                    "surface": surface,
                    "alternatives": True,
                    "grounded": len(fresh),
                    "expansions": [e for e, _q, _u in fresh],
                    "known": known,
                    "rejected": trace,
                },
            )

        if result is None:
            # `sightings` preserves the existing count -- passing 0 would make an
            # acronym seen 8 times read as never seen.
            seen = repo.sighting_counts(target.conn, normalize(surface))
            mark_undefined(
                target.conn,
                surface,
                now=target.ctx.clock.now(),
                sightings=seen["sighting_count"] if seen else 0,
                articles=seen["article_count"] if seen else 1,
                # This one IS a real web lookup, so it stamps the attempt. The card
                # reads it to stop offering a lookup already asked and answered.
                attempted_run_id=target.run_id,
            )
            # Both are `nothing_found`: neither landed a definition. What differs
            # is the MACHINERY, and that is what `status` is for. A lookup that
            # READ pages and found no definition did its whole job -- `succeeded`.
            # A lookup whose every candidate refused us, timed out or published
            # only a PDF never had a page in its hands; that is a degradation, and
            # it says so through `ctx.warn`. One such lookup made two model calls,
            # zero reads, cost $0.28 (2026-08-31) and reported exactly what the
            # six-page lookup beside it reported. Either way the reason decides
            # whether to try again now or fix the network.
            read = web_grounding.pages_read(trace)
            blocked = Counter(
                str(e.get("reason"))
                for e in trace
                if web_grounding.REASON_STAGE.get(str(e.get("reason"))) in ("fetch", "search")
            )
            if not read:
                target.ctx.warn(_unreachable_error(blocked))
            return run_launcher.LaunchOutcome(
                outcome="nothing_found",
                data={
                    "surface": surface,
                    "grounded": False,
                    "pages_read": read,
                    "rejected": trace,
                },
            )

        expansion, quote, url = result
        if contradicted_by_corpus(target.conn, surface, expansion):
            seen = repo.sighting_counts(target.conn, normalize(surface))
            mark_undefined(
                target.conn,
                surface,
                now=target.ctx.clock.now(),
                sightings=seen["sighting_count"] if seen else 0,
                articles=seen["article_count"] if seen else 1,
                attempted_run_id=target.run_id,
            )
            trace.append({"url": url, "reason": "off_domain", "expansion": expansion})
            return run_launcher.LaunchOutcome(
                # The web offered a meaning and our own documents contradict
                # it, so we hold none. A correct, well-executed no.
                outcome="nothing_found",
                data={
                    "surface": surface,
                    "grounded": False,
                    "pages_read": web_grounding.pages_read(trace),
                    "rejected": trace,
                },
            )
        store_definition(
            target.conn,
            surface=surface,
            expansion=expansion,
            method="web_grounded",
            article_id=None,
            quote=quote,
            url=url,
            now=target.ctx.clock.now(),
        )
        # Ask our own corpus whether it agrees, immediately. A definition the
        # documents also spell out earns a second source and opens above the
        # provisional 0.55; one they never mention stays put. Free, deterministic,
        # and `PC-C6` (17 documents) against `FCC` (none) is the difference at
        # stake (2026-08-09).
        corroborated = corroborate_from_corpus(
            target.conn, surface, expansion, now=target.ctx.clock.now()
        )
        # A new definition is an alias; without this the hover card still shows
        # the surface undefined until the next full run.
        link_to_entities(target.conn)
        return run_launcher.LaunchOutcome(
            outcome="delivered",
            data={
                "surface": surface,
                "grounded": True,
                "expansion": expansion,
                "url": url,
                "corpus_sources": corroborated,
                # A definition accepted over the initials check has to say so
                # HERE: the trace is kept only on the failure branch, so without
                # this the one row resting on a model verdict has no record of it.
                **_overturns(trace),
            },
        )

    started = await run_launcher.launch(
        request,
        kind="lookup",
        # `alternatives` is part of the key, so "what else does FCC mean" is not
        # coalesced into a plain FCC lookup in flight -- different questions.
        params={"target_kind": "acronym", "surface": surface, "alternatives": alternatives},
        work=work,
        stage="lookup",
    )
    return {**started, "surface": surface}


class AttachEntityPayload(BaseModel):
    """Which entity a surface actually names."""

    entity_id: str


@router.post("/{surface}/attach-entity", response_model=AttachEntityResult)
def attach_entity(
    surface: str,
    payload: AttachEntityPayload,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """This surface is an ENTITY, not an acronym.

    The verdict the definitions queue could not express. Accept, reject, edit,
    look again and choose-a-meaning all assume the answer is a different
    DEFINITION -- and for `BAE` there is no right definition. It is a company,
    the corpus says so in all sixteen appearances of those letters ("BAE
    Systems", every time, 2026-08-09), and the correct outcome is that the
    surface resolves to the entity like any other name.

    Four steps, one transaction:

      1. refuse if another entity already owns the surface (409);
      2. write the alias -- strictness comes from `default_strictness`, which
         gives an all-caps 3+ surface **2**: global, case-sensitive, so `BAE`
         matches and `bae` does not;
      3. make the entity indexable. `_resolved_entity_aliases` admits only
         `active`/`merged` entities, so a provisional one's aliases never enter
         the automaton -- which is precisely why `BAE` never resolved;
      4. retire the glossary row: `entity_id` set (so it leaves the index and
         cannot match twice) and `rejected` + `decided_at` (so re-grounding
         cannot reinstate it).
    """
    from venona.services.alias_index import default_strictness

    surface = surface.strip()
    if not surface:
        raise HTTPException(400, detail="empty term")

    entity = repo.entity(conn, payload.entity_id)
    if entity is None:
        raise HTTPException(404, detail=f"no name {payload.entity_id!r}")

    key = normalize(surface)
    # Mirrors `Resolver._add_alias`, which refuses when another entity of the same
    # type already owns the surface and files an `alias_conflicts` row instead.
    # Two entities claiming one surface is what `_resolve_overlaps` would settle
    # by insertion order; a merge is the right remedy and it already exists.
    owner = repo.alias_owner(conn, normalized=key, entity_id=entity["id"])
    if owner is not None:
        raise HTTPException(
            409,
            detail=(
                f"{surface!r} already resolves to {owner['name']!r}. "
                f"If they are the same thing, merge them; if not, this term "
                f"cannot name both."
            ),
        )

    now = utc_now_iso()
    is_acronym = surface.isupper() and len(surface) >= 3
    repo.upsert_manual_alias(
        conn,
        entity_id=entity["id"],
        surface=surface,
        normalized=key,
        strictness=default_strictness(surface=surface, added_by="manual", is_acronym=is_acronym),
        is_acronym=is_acronym,
        now=now,
    )

    promoted = False
    if entity["status"] in ("provisional", "dormant"):
        repo.activate_entity(conn, entity["id"])
        promoted = True

    retired = repo.reject_surface_to_entity(
        conn, decided_at=now, entity_id=entity["id"], normalized=key, actor=actor_id(request)
    )

    return {
        "surface": surface,
        "entity": {"id": entity["id"], "name": entity["name"], "type": entity["type"]},
        "promoted": promoted,
        "definitions_retired": max(retired, 0),
    }


@router.post("/{surface}/not-an-acronym", response_model=NotAnAcronymResult)
def not_an_acronym(
    surface: str,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """This surface is not an acronym at all -- stop detecting it.

    `III` is a Roman numeral. The corpus uses it in "battleship Alexander III";
    the system spent a lookup deciding it meant the FBI's Interstate
    Identification Index. No definition is right, and rejecting the row only
    settles the row.

    Both halves are needed and neither is sufficient. The stoplist entry stops
    `detect()` ever counting the token again, so it is never re-queued and never
    eligible for a paid lookup; the rejection settles what is already on screen.
    For a row with NO expansion the rejection alone is not even durable --
    `mark_undefined`'s "already grounded" test requires `expansion IS NOT NULL`,
    so an undefined surface rejected today comes back on the next run.
    """
    from venona.services import config_service

    surface = surface.strip()
    if not surface:
        raise HTTPException(400, detail="empty term")

    try:
        added = config_service.add_to_stoplist(request.app.state.settings, surface)
    except config_service.ConfigError as exc:
        # The lexicon is a document the user owns and edits by hand. If its
        # shape is not what this expects, say so rather than guessing.
        raise HTTPException(422, detail=str(exc)) from exc

    rejected = repo.reject_surface(
        conn, decided_at=utc_now_iso(), normalized=normalize(surface), actor=actor_id(request)
    )

    return {
        "surface": surface,
        "stoplisted": added,
        "definitions_rejected": max(rejected, 0),
    }


def _readings(conn: db.Conn, surface: str) -> list[db.Row]:
    key = normalize(surface)
    return repo.readings(conn, key)


@router.post("/{surface}/keep-both", response_model=KeepBothResult)
def keep_both(surface: str, request: Request, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """These readings are all real. Keep them, and record that I said so.

    Keeping both is the default and nothing forces a pick: the hover card lists
    the alternatives and `pick_sense` marks whichever the surrounding brief
    spells out. What this adds is the verdict -- `decided_at` was NULL on every
    ambiguous row, so a surface a curator had reviewed and deliberately kept was
    indistinguishable from one nobody had ever opened.

    The definitions-queue counterpart of the merge queue's `keep-apart`, and
    deliberately the same shape: a verdict that changes no data except to stamp
    itself, and a `reopen` to take it back.

    The surface stays ambiguous ON PURPOSE. That is what makes `CPE` render as
    "Capability Program Executive" in one brief and "Common Platform Enumeration"
    in another instead of picking one and being wrong half the time.
    """
    rows = _readings(conn, surface)
    if len(rows) < 2:
        raise HTTPException(400, detail=f"{surface!r} has fewer than two sourced readings to keep")

    now = utc_now_iso()
    key = normalize(surface)
    for row in rows:
        # Normalised to `ambiguous`, not left as they were. From a settled state
        # -- one `active`, one `rejected` -- this is a REVERSAL, and half-applying
        # it would leave the surface annotating with the reading just disowned.
        repo.mark_ambiguous(conn, decided_at=now, entry_id=row["id"], actor=actor_id(request))
    repo.mark_surface_ambiguous(
        conn, normalized=key, reason=f"{len(rows)} meanings, kept by a curator", now=now
    )
    return {
        "surface": surface,
        "kept": [r["expansion"] for r in rows],
        "entries": [_row_to_entry(conn, r) for r in _readings(conn, surface)],
    }


class SameThingPayload(BaseModel):
    """Which spelling of one meaning is the canonical one."""

    entry_id: str


@router.post("/{surface}/same-thing", response_model=SameThingResult)
def same_thing(
    surface: str,
    payload: SameThingPayload,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """These readings are ONE meaning spelled differently. Collapse them.

    Distinct from `choose`, and the difference is the whole point. `choose` says
    *these are different things and this one is right here*, so the others are
    wrong and become `rejected`. This says *these are the same thing*, so the
    others are not wrong -- they are duplicates, and their evidence belongs to
    the survivor. Measured 2026-08-09, over half the ambiguous surfaces on this
    corpus are that case and not real ambiguity:

        fy    -> "Fiscal Year" | "Fiscal Years" | "In fiscal year"
        ai    -> "Artificial Intelligence" | "of artificial intelligence"
        nspa  -> "NATO Support and Procurement Agency" | "NATO's Support and …"

    So the duplicates' **sources are folded into the survivor** rather than
    discarded, which raises its confidence honestly through `confidence_for`: two
    spellings grounded from two documents are two independent sources for one
    meaning. See `docs/DATA-MODEL.md`, "Confidence is evidence, not a constant".

    What is deliberately NOT copied is the losers' quote and URL. The quote must
    contain the expansion -- three of the four tier-3 checks are about exactly
    that -- and a sentence spelling "Fiscal Years" is not evidence for the string
    "Fiscal Year". The survivor keeps its own provenance and gains only sources.
    """
    from venona.pipeline.acronyms import _source_key, confidence_for

    rows = _readings(conn, surface)
    if len(rows) < 2:
        raise HTTPException(400, detail=f"{surface!r} has fewer than two readings to collapse")
    survivor = next((r for r in rows if r["id"] == payload.entry_id), None)
    if survivor is None:
        raise HTTPException(400, detail=f"{payload.entry_id!r} is not a reading of {surface!r}")

    sources: list[str] = []
    for row in rows:
        stored = db.jload(row["corroborating_sources"], []) or []
        if not stored:
            seed = _source_key(row["grounding_article_id"], row["grounding_url"])
            stored = [seed] if seed else []
        for source in stored:
            if source and source not in sources:
                sources.append(source)

    now = utc_now_iso()
    repo.fold_together(
        conn,
        decided_at=now,
        sources=db.jdump(sources),
        corroboration=max(1, len(sources)),
        confidence=confidence_for(
            survivor["method"], max(1, len(sources)), declared=survivor["confidence"]
        ),
        entry_id=survivor["id"],
        actor=actor_id(request),
    )
    for row in rows:
        if row["id"] != survivor["id"]:
            repo.reject_entry(conn, decided_at=now, entry_id=row["id"], actor=actor_id(request))
    # One meaning stands, so the surface has stopped being ambiguous and the
    # annotator can mark it again without asking the context.
    repo.clear_ambiguous_surface(conn, normalize(surface))

    return {
        "surface": surface,
        "kept": survivor["expansion"],
        "collapsed": [r["expansion"] for r in rows if r["id"] != survivor["id"]],
        "sources": len(sources),
        "entries": [_row_to_entry(conn, r) for r in _readings(conn, surface)],
    }


@router.post("/{surface}/reopen", response_model=ReopenResult)
def reopen(surface: str, request: Request, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """Take back a verdict about this surface.

    Undoes `choose`, `keep-both` and `same-thing` alike: all three are a status
    plus a `decided_at` stamp, so one reversal covers them. The merge queue's
    `reopen` is the model, including its limit.

    Deliberately NOT reversed, for the same reason in two shapes: a verdict that
    wrote OUTSIDE these tables. A surface attached to an entity wrote an alias
    and promoted an entity; a surface called not-an-acronym wrote a line into
    the stoplist of `profile/lexicon.yaml`. Unpicking either is a different
    operation with different consequences, so both refuse rather than
    half-undoing.
    """
    rows = _readings(conn, surface)
    if not rows:
        raise HTTPException(404, detail=f"no sourced readings for {surface!r}")
    if any(r["entity_id"] for r in rows):
        raise HTTPException(
            409,
            detail=(
                f"{surface!r} names a thing in the graph. Detach it there rather than "
                f"reopening the definition."
            ),
        )
    # EVERY reading rejected is the `not-an-acronym` verdict, and that one also
    # writes a stoplist line into `profile/lexicon.yaml` -- its own route argues
    # that neither half is sufficient. Clearing the rejection alone puts the
    # readings back on screen while `detect` stays blind to the token, which is
    # the half-undo the entity branch above refuses. `choose` and `same_thing`
    # both leave one reading standing, so neither ever looks like this.
    if all(r["status"] == "rejected" for r in rows):
        raise HTTPException(
            409,
            detail=(
                f"{surface!r} is in your lexicon's stoplist. Take it out there rather "
                f"than reopening the definition."
            ),
        )
    if not any(r["decided_at"] for r in rows):
        raise HTTPException(400, detail=f"nothing has been decided about {surface!r}")

    key = normalize(surface)
    now = utc_now_iso()
    for row in rows:
        repo.reopen(
            conn,
            entry_id=row["id"],
            status="ambiguous" if len(rows) > 1 else "active",
        )
    if len(rows) > 1:
        repo.mark_surface_ambiguous(
            conn, normalized=key, reason=f"{len(rows)} accepted expansions", now=now
        )
    return {
        "surface": surface,
        "reopened": len(rows),
        "entries": [_row_to_entry(conn, r) for r in _readings(conn, surface)],
    }


class ChoosePayload(BaseModel):
    """Which of a surface's meanings is the right one."""

    entry_id: str


@router.post("/{surface}/choose", response_model=ChooseResult)
def choose_meaning(
    surface: str,
    payload: ChoosePayload,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """Settle an ambiguous surface: one meaning wins, the rest are rejected.

    The only way a PERSON can end that state. `store_definition` marks a surface
    `ambiguous` the moment a second expansion arrives, the alias index refuses to
    annotate an ambiguous surface, and the hover card shows a list rather than
    picking -- but rejecting readings one at a time, in a list that re-sorts under
    you as you do it, is not a verdict.

    One decision, applied atomically:

      * the chosen row becomes `active`;
      * its siblings become `rejected`;
      * every row is stamped `decided_at`, which is what makes the verdict
        durable -- `store_definition` defers to that stamp, so the next nightly
        run cannot quietly reinstate the reading a curator just discarded;
      * the `ambiguous_surfaces` row goes, which is what lets the annotator start
        marking the surface again.

    Rejecting the alternatives rather than deleting them is deliberate. Each was
    grounded in a real sentence on a real page, and a rejected row still carries
    that evidence -- so "why is FCC not the Federal Communications Commission
    here" has an answer six months from now, and a re-lookup does not pay to
    rediscover a reading already ruled out.
    """
    key = normalize(surface)
    rows = repo.expansions(conn, key)
    if not rows:
        raise HTTPException(404, detail=f"no sourced meanings for {surface!r}")
    if not any(r["id"] == payload.entry_id for r in rows):
        # The id must belong to THIS surface: without the check a typo would
        # reject every meaning here and activate a row from another acronym.
        raise HTTPException(400, detail=f"{payload.entry_id!r} is not a meaning of {surface!r}")

    now = utc_now_iso()
    for row in rows:
        chosen = row["id"] == payload.entry_id
        repo.set_status(
            conn,
            entry_id=row["id"],
            status="active" if chosen else "rejected",
            decided_at=now,
            actor=actor_id(request),
        )
    repo.clear_ambiguous_surface(conn, key)

    updated = repo.by_surface_for_verdict(conn, key)
    return {
        "surface": surface,
        "chosen": payload.entry_id,
        "rejected": [r["id"] for r in rows if r["id"] != payload.entry_id],
        "entries": [_row_to_entry(conn, r) for r in updated],
    }


@router.patch("/{entry_id:path}", response_model=GlossaryEntry)
def patch_entry(
    entry_id: str,
    payload: GlossaryPatch,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    from venona.pipeline.acronyms import load_lexicon

    row = repo.by_id(conn, entry_id)
    if row is None:
        raise HTTPException(404, detail=f"no glossary entry {entry_id}")

    updates: dict[str, Any] = {}
    if payload.expansion is not None:
        # A hand-entered expansion is grounded in the curator, so it is
        # `manual` at full confidence -- and distinguishable from an extraction.
        expansion = payload.expansion.strip()
        url = (payload.grounding_url or "").strip()

        if expansion and not url:
            raise HTTPException(
                400,
                detail="a manual expansion needs a source URL; a person may skip the "
                "sentence check, but the record of where it came from is kept",
            )
        # The real lexicon, not an empty one, or the escape hatch named in the
        # error below would not actually work.
        lexicon, _stoplist, _whitelist = load_lexicon(request.app.state.settings)
        if expansion and not verify_expansion(row["surface"], expansion, lexicon=lexicon):
            # The one gate that still applies, and it costs nothing: it catches
            # the paste error, not the judgement. A curator who means it declares
            # the expansion in `profile/lexicon.yaml`.
            raise HTTPException(
                400,
                detail=f"the initials of {expansion!r} do not spell {row['surface']!r}. "
                f"If it is genuinely right, declare it in profile/lexicon.yaml.",
            )

        updates["expansion"] = expansion or None
        updates["method"] = "manual"
        updates["confidence"] = 1.0
        if url:
            updates["grounding_url"] = url
    if payload.status is not None:
        if payload.status not in {"active", "rejected", "undefined", "ambiguous"}:
            raise HTTPException(400, detail=f"unknown status {payload.status!r}")
        updates["status"] = payload.status
        # Record that a PERSON ruled. As a bare status write, a pipeline-written
        # `active` and a curated one are indistinguishable, and a rejection is
        # silently overturned the next time the acronyms stage re-grounds the
        # surface. `store_definition` defers to this stamp.
        updates["decided_at"] = utc_now_iso()
        # And WHO -- two curators disagreeing is ordinary here. In `updates` so it
        # lands in the same UPDATE as the status it belongs to.
        updates["decided_by"] = actor_id(request)
    if payload.entity_id is not None:
        updates["entity_id"] = payload.entity_id or None

    if not updates:
        raise HTTPException(400, detail="nothing to update")

    repo.patch(conn, entry_id=entry_id, updates=updates)

    updated = repo.by_id(conn, entry_id)
    return _row_to_entry(conn, updated)
