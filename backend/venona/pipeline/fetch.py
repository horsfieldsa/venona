"""Fetch stage: collect from every enabled source, normalise, dedup.

Failure isolation is the whole design. One dead feed must never take a run down
-- publishers 403, move paths, and reformat without notice, and a run that
aborts on the first failure would be down more often than up. Every source
failure is recorded, counted toward a strike limit, and surfaced as a warning
that makes the run `succeeded_partial` rather than `failed`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from venona.db import core as db
from venona.db.repositories import pipeline_fetch as repo
from venona.llm.budget import Budget
from venona.llm.executor import PermissionDenied
from venona.pipeline import dedup, normalize
from venona.pipeline.metrics import Funnel, record_source_stats
from venona.pipeline.types import RunCancelled, RunContext, resolve_cap
from venona.sources import registry
from venona.sources.base import RawItem, SourceResult, SourceSpec
from venona.sources.discovery import collect_discovery
from venona.sources.rss import collect_rss
from venona.sources.urlguard import BlockedUrl


@dataclass
class FetchReport:
    sources_ok: int = 0
    sources_failed: int = 0
    sources_not_modified: int = 0
    items_seen: int = 0
    documents_new: int = 0
    documents_duplicate: int = 0
    documents_skipped: int = 0
    clusters: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {k: v for k, v in self.__dict__.items() if isinstance(v, int)}


async def _collect_one(
    spec: SourceSpec, ctx: RunContext, budget: Budget | None = None
) -> SourceResult:
    """Dispatch on kind. `ctx` rather than `(conn, http)` because discovery
    needs `ctx.llm`, `ctx.rng` and the run's budget -- and threading a third
    positional argument through would have to happen the next time anyway."""
    health = registry.get_health(ctx.db, spec.id)

    if spec.kind == "rss":
        return await collect_rss(
            spec, ctx.http, etag=health.etag, last_modified=health.last_modified
        )
    if spec.kind == "discovery":
        return await collect_discovery(spec, ctx, budget=budget)

    # Unreachable: `SourceKind` is a Literal validated at startup. Kept so that
    # adding a kind without adding a branch fails loudly rather than silently
    # collecting nothing.
    return SourceResult(
        source_id=spec.id,
        ok=False,
        error=f"adapter for kind={spec.kind!r} is not implemented yet",
    )


def _age_hours(published_at: str | None, *, now: str) -> float | None:
    """How old the article was when we fetched it, in hours.

    `None` for an undated document, which is a distinct outcome rather than an
    age of zero: the brief's retrieval filter keeps undated documents, so they
    bypass the freshness window entirely and must be counted apart from both
    fresh and stale.
    """
    if not published_at:
        return None
    from datetime import datetime

    def parse(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    published, fetched = parse(published_at), parse(now)
    if published is None or fetched is None:
        return None
    # Clamped at zero: a feed whose clock is ahead of ours would otherwise
    # report a negative age and read as "fresher than possible".
    return max(0.0, (fetched - published).total_seconds() / 3600.0)


async def fetch_body(item: RawItem, http) -> tuple[str | None, str | None, str | None]:
    """Returns (html, http_last_modified, why_not). Never raises.

    `why_not` is the third return because the absence of a body has three causes
    that call for opposite actions, and `build_document` collapses all of them
    into `None`: a page we could not reach is a source or network problem, a page
    that yielded no text is an extraction problem, and a genuinely brief item is
    neither. Only this function can still tell the first one apart.
    """
    try:
        response = await http.get(item.url)
    except Exception as exc:  # noqa: BLE001 - a single bad article is not a run failure
        return None, None, f"unreachable_{type(exc).__name__}"
    if not response.ok:
        return None, None, "http_not_ok"
    return response.text, response.headers.get("last-modified"), None


async def run_fetch(
    ctx: RunContext,
    *,
    limit_per_source: int | None = None,
    budget: Budget | None = None,
) -> FetchReport:
    """`budget` is carried for source kinds that spend model calls (discovery),
    so they count against the same `max_calls_per_run` the analyze stage does
    rather than sitting outside it."""
    report = FetchReport()
    settings = ctx.settings
    conn = ctx.db
    http = ctx.http

    specs = registry.load_sources(settings.config_dir)
    registry.sync_sources(conn, specs, now=ctx.clock.now())
    order = registry.source_order(specs)
    active = registry.active_specs(conn, specs)

    # `phase` is what makes the two counters below tellable apart. This stage
    # counts SOURCES first and DOCUMENTS second, and both rode the same
    # `total`/`current` pair -- so the denominator changed meaning halfway
    # through (17 feeds, then 200-odd documents) with nothing on the wire saying
    # so. `data` is additive-only under C6, so the distinction goes in a field
    # rather than a second event type the store would drop.
    await ctx.emit("stage_progress", "fetch", total=len(active), current=0, phase="sources")

    # A COUNTER PER COMPLETION, not one emit before the gather. One `current=0`
    # over the whole `asyncio.gather` leaves the display on `0/17` for the entire
    # network-bound part of the stage -- the part that actually takes the time --
    # with the document loop below the first sign of life. The counting is here
    # rather than in `_collect_one` so the adapters stay ignorant of the run's
    # display.
    #
    # `collected_sources` is a plain int and needs no lock: one event loop, one
    # thread, and the increment happens between awaits.
    collected_sources = 0

    async def collect_counting(spec: SourceSpec) -> SourceResult:
        nonlocal collected_sources
        # BEFORE each source, not only after the whole gather. Collection is the
        # longest unattended stretch in the stage -- a dozen feeds, each with
        # retries and a per-host delay -- and a cancellation arriving inside it
        # was invisible until the slowest source came back. That is the gap
        # between an accepted cancel and the run actually stopping, and it is
        # measured in minutes: `cancel` returns 202, the reader believes the run
        # has stopped, and it keeps fetching and keeps spending.
        #
        # `gather(return_exceptions=True)` keeps the raised `RunCancelled` as a
        # result; the loop below re-raises it rather than degrading it, in the
        # same branch that refuses to swallow a denied tool.
        ctx.check_cancel()
        try:
            return await _collect_one(spec, ctx, budget)
        finally:
            # `finally`, so a source that raises still advances the counter --
            # `gather(return_exceptions=True)` keeps the failure as a result and
            # the loop below reports it, so a stalled display would be lying.
            collected_sources += 1
            await ctx.emit(
                "stage_progress",
                "fetch",
                total=len(active),
                current=collected_sources,
                phase="sources",
            )

    results = await asyncio.gather(
        *(collect_counting(spec) for spec in active),
        return_exceptions=True,
    )
    # Before the document loop, not inside it: a check that runs only after every
    # feed has been fetched leaves a cancellation arriving during collection
    # invisible until the slowest source comes back.
    ctx.check_cancel()

    by_id = {spec.id: spec for spec in active}
    collected: list[tuple[SourceSpec, RawItem]] = []

    # Per-source accumulators. Kept as plain dicts rather than a class because
    # the funnel is written once at the end of the stage -- a source's row is
    # not interesting until every one of its items has been through the loop
    # below, and a partial row would misreport `duplicate` as `skipped`.
    offered: dict[str, int] = {}
    capped: dict[str, int] = {}
    per_source: dict[str, dict[str, int]] = {
        spec.id: {"kept": 0, "duplicate": 0, "skipped": 0, "fresh": 0, "stale": 0, "undated": 0}
        for spec in active
    }
    ages: dict[str, list[float]] = {spec.id: [] for spec in active}
    outcomes: dict[str, tuple[str, str | None]] = {}

    for spec, result in zip(active, results, strict=False):
        now = ctx.clock.now()

        if isinstance(result, BaseException):
            # `return_exceptions=True` is what makes one dead feed survivable,
            # but it would also swallow the two failures that must never be
            # degraded: a denied tool (contract C2 -- the call ran without the
            # capability it needed) and a cancellation.
            if isinstance(result, PermissionDenied | RunCancelled):
                raise result

            # A URL we refused to fetch is a CONFIGURATION mistake, not a sick
            # feed. Striking for it would auto-disable the source after three
            # runs -- and since the URL is wrong every time, that is three runs
            # exactly, after which the feed stays off until someone runs
            # `venona sources --enable`. The fix is to correct the URL, and a
            # disabled source hides the reason.
            if isinstance(result, BlockedUrl):
                report.sources_failed += 1
                outcomes[spec.id] = ("blocked", str(result))
                report.warnings.append(f"{spec.id}: refused to fetch -- {result}")
                continue

            strikes = registry.record_failure(
                conn, spec.id, now=now, error=f"{type(result).__name__}: {result}"
            )
            report.sources_failed += 1
            outcomes[spec.id] = ("error", str(result))
            report.warnings.append(f"{spec.id}: {result} (strike {strikes})")
            continue

        if result.ok and result.error:
            # A source that reports `ok` WITH an error is saying "I am not
            # broken, but I produced nothing this time" -- a discovery call that
            # failed at the model, not at the source.
            #
            # This distinction is why `auto_disabled` is not a one-way door.
            # Nothing but a success resets the counter, so three offline runs
            # with a missing LLM fixture would have permanently disabled
            # discovery, recoverable only by hand-editing the database.
            outcomes[spec.id] = ("empty", result.error)
            report.warnings.append(f"{spec.id}: {result.error} (no strike)")
            continue

        if result.not_modified:
            report.sources_not_modified += 1
            outcomes[spec.id] = ("not_modified", None)
            registry.record_success(
                conn,
                spec.id,
                now=now,
                etag=result.etag,
                last_modified=result.last_modified,
                item_count=0,
            )
            continue

        if not result.ok:
            strikes = registry.record_failure(conn, spec.id, now=now, error=result.error or "")
            report.sources_failed += 1
            outcomes[spec.id] = ("error", result.error)
            note = f"{spec.id}: {result.error} (strike {strikes}"
            note += "; AUTO-DISABLED)" if strikes >= registry.STRIKE_LIMIT else ")"
            report.warnings.append(note)
            continue

        report.sources_ok += 1
        registry.record_success(
            conn,
            spec.id,
            now=now,
            etag=result.etag,
            last_modified=result.last_modified,
            item_count=len(result.items),
        )
        # Resolved HERE rather than in the orchestrator because `venona fetch`
        # calls this directly and would otherwise be the one caller that
        # ignored the config file.
        cap = resolve_cap(limit_per_source, settings.pipeline.fetch_limit_per_source)
        # Same reason as `pending_clusters`: 0 means none, not all, and a
        # negative slice would quietly drop items off the END of the list.
        items = result.items[: max(0, cap)] if cap is not None else result.items
        # `len(result.items)` is the population BEFORE the cap, and this line is
        # the only place it is ever known. Without it a feed returning 200 items
        # to be truncated to 25 is indistinguishable from one returning 25 --
        # which is exactly the "is fetching too wide" question.
        offered[spec.id] = len(result.items)
        capped[spec.id] = len(result.items) - len(items)
        collected.extend((spec, item) for item in items)

    report.items_seen = len(collected)
    await ctx.emit("stage_progress", "fetch", total=len(collected), current=0, phase="documents")

    # The funnel's population is what the sources OFFERED, not what survived the
    # cap -- so the cap itself shows up as a drop rather than as an absence.
    funnel = Funnel("fetch", offered=sum(offered.values()) or len(collected))
    funnel.drop("capped", sum(capped.values()))
    lookback_h = float(settings.pipeline.lookback_hours)

    # Cheap pre-filter: an article already stored under this url_key needs no
    # body fetch at all. This is what makes a second consecutive run fast as
    # well as idempotent.
    known_url_keys = {row["url_key"] for row in repo.select_documents(conn)}

    for index, (spec, item) in enumerate(collected, start=1):
        ctx.check_cancel()

        # AT THE TOP OF THE LOOP, and that is the whole point. Three `continue`
        # statements below jump straight over the foot of the body -- an
        # already-known `url_key`, a document that would not build, a stale
        # discovery hit -- and on a second run over the same feeds most items
        # take one of those paths, so an emit down there fires NOTHING for the
        # whole stage. A
        # dropped item is still an item read, and the reader watching the spine
        # is asking how far along it is, not how many survived -- `kept` answers
        # that separately.
        #
        # `index == len(collected)` is here because the decade gate alone never
        # reports the final partial one: a 97-item loop stopped at 90 and the
        # next thing on the wire was `stage_finished`.
        if index % 10 == 0 or index == len(collected):
            await ctx.emit(
                "stage_progress",
                "fetch",
                total=len(collected),
                current=index,
                kept=funnel.kept,
                phase="documents",
            )

        url_key = normalize.url_key_for(item.url)

        if url_key in known_url_keys:
            report.documents_duplicate += 1
            per_source[spec.id]["duplicate"] += 1
            funnel.drop("duplicate_url_key")
            _record_sighting_only(ctx, conn, item, url_key)
            continue

        html = item.body_html
        header_date = None
        unreachable = None
        if html is None and by_id[spec.id].fetch_body:
            html, header_date, unreachable = await fetch_body(item, http)

        document = normalize.build_document(
            url=item.url,
            title=item.title,
            html=html,
            text=item.body_text,
            source_id=spec.id,
            publisher=item.publisher,
            publisher_asserted=item.publisher_asserted,
            feed_date=item.published_at,
            header_date=header_date,
            summary=item.summary,
        )
        if document is None:
            report.documents_skipped += 1
            per_source[spec.id]["skipped"] += 1
            # THREE CAUSES, NOT ONE, and they call for opposite actions: fix the
            # source, fix the extractor, or nothing at all. `build_document`
            # returns None for all three, and naming them all `too_short` is how
            # Federal News Network lost 529 of the 540 items it ever offered
            # while its row read like a thin feed. See `normalize.extract_text`.
            if unreachable:
                funnel.drop(f"body_{unreachable}")
            elif html and not (item.body_text or item.summary):
                funnel.drop("body_empty")
            else:
                funnel.drop("too_short")
            continue

        # Freshness, measured against the BRIEF's own window rather than an
        # arbitrary bucket, so the number means "could this have been briefed".
        # Undated is counted separately and is NOT stale: the retrieval filter
        # is `published_at IS NULL OR >= cutoff`, so an undated document skips
        # the freshness gate entirely -- which makes a feed with no dates a
        # silent way to fill a brief with anything.
        age_h = _age_hours(document.published_at, now=ctx.clock.now())
        if age_h is None:
            per_source[spec.id]["undated"] += 1
        else:
            ages[spec.id].append(age_h)
            if age_h <= lookback_h:
                per_source[spec.id]["fresh"] += 1
            else:
                per_source[spec.id]["stale"] += 1

        # The recency floor, and it applies to DISCOVERY ONLY. This is the first
        # point at which a discovered page has a date worth trusting:
        # `discovery.py` throws the model's guess away deliberately, so the date
        # has to come out of the fetched page via `resolve_date`. The body fetch
        # above is therefore spent either way -- but it costs no model call, and
        # dropping here is what stops a stale sweep result reaching the analyze
        # queue, where it would.
        #
        # RSS is exempt: `lookback_hours` bounds it at analyze instead. A feed we
        # subscribed to on purpose publishing something last week is a different
        # proposition from a search reaching back for it.
        #
        # Undated discovery results are dropped by the same rule. The brief
        # treats a missing `published_at` as ALWAYS in-window, so undated is not
        # a neutral state here -- it is the widest one. For RSS that is an
        # honest unknown worth keeping; for a page a search engine surfaced and
        # we then could not date, it is the silent way to fill a brief with
        # anything.
        if by_id[spec.id].kind == "discovery":
            max_age_h = float(settings.pipeline.discovery_max_age_hours)
            if age_h is None or age_h > max_age_h:
                report.documents_skipped += 1
                per_source[spec.id]["skipped"] += 1
                funnel.drop("discovery_stale")
                continue

        outcome = dedup.take(ctx, document)

        if outcome.is_new:
            report.documents_new += 1
            known_url_keys.add(url_key)
            per_source[spec.id]["kept"] += 1
            funnel.keep()
            await ctx.counter("documents_new")
        else:
            report.documents_duplicate += 1
            per_source[spec.id]["duplicate"] += 1
            # `DedupOutcome.reason` has been computed on every document since
            # dedup shipped and read by nobody -- `is_new` was the only field
            # anyone looked at. It distinguishes a url_key repeat from a wire
            # reprint (`content_hash`) from a same-story-different-outlet match
            # (`title_date`), which is the difference between "this feed
            # duplicates itself" and "these two feeds carry the same wire".
            funnel.drop(f"duplicate_{outcome.reason or 'unknown'}")

        await ctx.counter("documents_fetched")

    report.clusters = dedup.refresh_clusters(conn, now=ctx.clock.now(), source_order=order)
    ctx.counters.clusters = report.clusters

    funnel.emit(ctx)
    ctx.metric("fetch", "sources_active", len(active))
    ctx.metric("fetch", "clusters", report.clusters)
    # docs-per-cluster: 1.0 means clustering collapsed nothing, which is the
    # single number that says whether one model call per cluster is the right
    # shape. Measured at 1.016 across the corpus.
    #
    # BOTH SIDES ARE CORPUS TOTALS. `refresh_clusters` returns every cluster in
    # the database, not this run's new ones, so dividing THIS run's kept
    # documents by it reported 0.13 on a fetch that added 33 documents to 247
    # existing clusters -- a ratio that cannot go below 1 by construction,
    # printed as an eighth. A run's own docs-per-cluster is not knowable here
    # anyway: clustering is a whole-corpus operation and a new document can join
    # a cluster that already existed.
    documents = repo.select_documents_2(conn).fetchone()["n"]
    if report.clusters:
        ctx.metric("fetch", "docs_per_cluster", documents / report.clusters, unit="ratio")
    ctx.metric("fetch", "lookback_hours", lookback_h, unit="hours")

    for spec in active:
        counts = per_source[spec.id]
        seen = ages[spec.id]
        seen.sort()
        status, error = outcomes.get(spec.id, ("ok", None))
        record_source_stats(
            conn,
            run_id=ctx.run_id,
            source_id=spec.id,
            offered=offered.get(spec.id, 0),
            capped=capped.get(spec.id, 0),
            kept=counts["kept"],
            duplicate=counts["duplicate"],
            skipped=counts["skipped"],
            fresh=counts["fresh"],
            stale=counts["stale"],
            undated=counts["undated"],
            age_p50_h=seen[len(seen) // 2] if seen else None,
            status=status,
            error=error,
        )

    for warning in report.warnings:
        ctx.warn(warning)

    return report


def _record_sighting_only(ctx: RunContext, conn: db.Conn, item, url_key: str) -> None:
    """A known article seen again is still a sighting -- that is how repeat
    pickup shows up as salience."""
    row = repo.select_documents_by_url_key(conn, url_key).fetchone()
    if row is None:
        return
    db.upsert(
        conn,
        "sightings",
        {
            "id": ctx.ids.uuid7(),
            "cluster_id": row["cluster_id"],
            "document_id": row["id"],
            "source_id": item.source_id,
            "run_id": ctx.run_id,
            "seen_at": ctx.clock.now(),
        },
        key=["id"],
    )
