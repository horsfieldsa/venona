"""Analyze + resolve: model output becomes assertions.

One call per CLUSTER, on its canonical document's text. Clusters rather than
documents because a wire story carried by five outlets should cost one call and
produce one set of assertions, not five.

Every assertion written here carries a verbatim evidence quote (C1) and a
resolved entity id. Relations are additionally type-checked against the ontology
before they are written -- `check_relation` catches a real predicate used
backwards, which no enum can.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from venona.db import core as db
from venona.db import retry
from venona.db.repositories import pipeline_analyze as repo
from venona.llm import budget as budget_mod
from venona.llm.executor import PermissionDenied
from venona.llm.prompts import analyze_system_prompt, load_profile
from venona.llm.runner import analyze_document, resolve_model, score_from, transcript_emitter
from venona.llm.schemas import tier_for
from venona.ontology import get_ontology
from venona.pipeline import define
from venona.pipeline.metrics import Funnel, record_rejected_shapes
from venona.pipeline.resolve import (
    Resolver,
    promote_provisionals,
    refresh_mention_counts,
)
from venona.pipeline.types import (
    MIN_BRIEFABLE_CHARS,
    RunContext,
    resolve_cap,
    retrieval_window,
)

#: A date predicate that is true of everything, for a read with no window.
#: See `run_analyze` for why this is not `cutoff=None`.
BEGINNING_OF_TIME = "0000-01-01T00:00:00Z"


@dataclass
class AnalyzeReport:
    clusters_seen: int = 0
    clusters_analyzed: int = 0
    clusters_skipped: int = 0
    clusters_deferred: int = 0
    refused: int = 0
    entities_resolved: int = 0
    entities_minted: int = 0
    entities_ambiguous: int = 0
    relations_written: int = 0
    relations_rejected: int = 0
    promoted: int = 0
    alias_conflicts: int = 0
    cost_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)
    # Surfaced, but not status-affecting. A relation the ontology rejects is the
    # type check DOING ITS JOB -- every real corpus produces some -- so it must
    # not be what makes a run report `succeeded_partial`. See `RunContext.note`.
    notices: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if isinstance(v, int | float)}


def pending_clusters(
    conn: db.Conn,
    *,
    limit: int | None = None,
    cluster_id: str | None = None,
    cutoff: str | None = None,
    now: str | None = None,
) -> list[db.Row]:
    """Clusters with a canonical document and no analysis yet.

    `cutoff` is the recency bound from `retrieval_window`, and it is the same
    one `brief.build_retrieval` retrieves by -- deliberately, because a cluster
    outside it cannot reach a brief under any ranking, so scoring it is spend
    that buys nothing. Predicate shape mirrors the brief's EXACTLY, undated
    clause included; the two drifting is the defect this closes.

    Ordered newest-first so a budget-limited run spends its calls on the most
    recent news rather than whatever happens to sort first. Undated clusters
    sort as `now` rather than as `'0000'`: the brief treats a missing
    `published_at` as always in-window, so sinking them to the bottom of the
    analyze queue guaranteed the ones the brief most wanted were the ones it
    never got. Measured 2026-08-06: all six undated clusters on `run:06a8160f` were
    deferred, and five of them cleared every other brief gate.

    `cluster_id` narrows to one, and SUSPENDS both bounds. An `ingest` run asks
    for a single article by name because a reader clicked a button about that
    page; refusing it for being old or short would make the button silently do
    nothing. "Not yet analysed" is not the same question either -- without the
    narrowing it would quietly analyse the whole backlog too, on the model's
    meter.
    """
    return repo.pending_clusters(
        conn,
        cluster_id=cluster_id,
        cutoff=cutoff,
        now=now,
        limit=limit,
        min_chars=MIN_BRIEFABLE_CHARS,
    )


async def run_analyze(
    ctx: RunContext,
    *,
    limit: int | None = None,
    budget: budget_mod.Budget | None = None,
    only_cluster: str | None = None,
    writes_brief: bool = True,
    bounded: bool = True,
) -> AnalyzeReport:
    report = AnalyzeReport()
    conn = ctx.db
    settings = ctx.settings
    ontology = get_ontology()

    # A wiped database looks populated after one run, so a missing trunk is
    # invisible unless someone counts (see the Makefile's reset-bare note: the
    # pipeline just starts minting its own ids and the corpus is simply worse,
    # in a way nothing reports). Counted directly -- gazetteer() always returns
    # at least its header line, so testing its emptiness silently never fires.
    # `warn`, not `note`: a run with no prior identity to attach to and no
    # ambiguity guard IS partial. The two conditions are independent -- the
    # second can fail alone and the run still looks entirely healthy while it
    # mints `unit:navy`.
    trunk = repo.select_trunk_counts(conn).fetchone()
    if not trunk["active_entities"]:
        report.warnings.append(
            "Venona knows no names yet, so every name in today's reading is filed as "
            "something new. Load the starting set of names before the next run."
        )
    if not trunk["ambiguous_surfaces"]:
        report.warnings.append(
            "Nothing tells Venona which names mean more than one thing, so a name "
            "shared by two organisations will be filed as one. Load the starting set "
            "of names before the next run."
        )

    profile = load_profile(settings)
    # Assembled ONCE for the whole run: this is what makes calls 2..N read from
    # the prompt cache. Rebuilding it per call would silently cost ~10x.
    system_prompt = analyze_system_prompt(conn, profile)
    resolver = Resolver(conn, settings=settings)

    # The window analyze spends against. Same helper the brief retrieves by --
    # see `retrieval_window` for why that must be one function and not two
    # readings of the same setting. An `ingest` run is exempt: it was asked for
    # one page by name.
    now = ctx.clock.now()
    # NO WINDOW NAMED, NO WINDOW APPLIED. A run reading for the graph rather than
    # for a brief has no ceiling to fall outside of, so "everything waiting" has
    # to mean every pending cluster and not the configured default -- which is
    # the brief's 36 hours, which the daily has already read. Getting this wrong
    # is silent and total: the stage reports `clusters_seen: 0`, finishes in two
    # seconds, and the backlog it exists to drain is untouched. ADR 0228.
    #
    # A cutoff AT THE BEGINNING OF TIME rather than `cutoff=None`, and the
    # difference matters. `None` means "no bounds at all" and belongs to
    # `only_cluster`: an ingest run was asked for one article by name, so it
    # suspends the LENGTH floor too. A deep read has no use for a two-paragraph
    # stub -- it would pay a model call to extract from it -- so it wants the
    # date bound gone and every other guard kept, which is what a vacuous date
    # predicate gives without moving anything else.
    if bounded:
        cutoff, window_hours = retrieval_window(
            conn, now_iso=now, settings=settings, writes_brief=writes_brief
        )
    else:
        cutoff, window_hours = BEGINNING_OF_TIME, 0
    if only_cluster is not None:
        cutoff = None

    # Analyze is the stage that calls the model per cluster, so this is the cap
    # that actually bounds what a run spends.
    rows = pending_clusters(
        conn,
        limit=resolve_cap(limit, settings.pipeline.analyze_limit),
        cluster_id=only_cluster,
        cutoff=cutoff,
        now=now,
    )
    report.clusters_seen = len(rows)
    # The un-LIMITed backlog, and what the window and length bounds removed from
    # it. `clusters_seen` is assigned AFTER the LIMIT, so a run that analysed
    # 120 of 900 pending clusters reported "120 seen" and the 780 it never
    # looked at were invisible -- which is the single number that says whether
    # the pipeline is keeping up with its own feeds.
    #
    # The three drops are counted separately because they mean different
    # things. `outside_window` is healthy and expected -- it is the backlog this
    # stage now refuses to pay for. `over_limit` is the one that means the
    # pipeline is behind.
    # `?::text IS NULL` rather than `? IS NULL`. A parameter whose only use is a
    # NULL test gives Postgres nothing to infer a type from, and it refuses with
    # "could not determine data type of parameter $1" -- where SQLite simply
    # guessed. The cutoff is an ISO-8601 string, so `::text` is the honest type.
    counts = repo.select_clusters(
        conn, cutoff, cutoff, cutoff, cutoff, MIN_BRIEFABLE_CHARS
    ).fetchone()
    backlog = counts["backlog"]
    in_window = counts["in_window"] or 0
    eligible = counts["eligible"] or 0

    funnel = Funnel("analyze", offered=backlog)
    funnel.drop("outside_window", max(0, backlog - in_window))
    funnel.drop("too_short", max(0, in_window - eligible))
    funnel.drop("over_limit", max(0, eligible - len(rows)))
    ctx.metric("analyze", "window_hours", window_hours)
    rejections: dict[str, int] = {}
    rewrites: dict[str, int] = {}
    rejected_shapes: dict[tuple[str, str, str], dict] = {}

    await ctx.emit("stage_progress", "analyze", total=len(rows), current=0)

    # Dispatched in BATCHES rather than one at a time. The executor already
    # holds `Semaphore(llm.concurrency)`, but a `for ... await` loop never gives
    # it two things to hold: effective parallelism on `run:06a8160f` measured
    # 2026-08-16 at 1.00 against a semaphore of 2, and the stage's wall clock
    # (77.21 min) equalled the sum of its 112 call durations (77.18 min) to four
    # figures.
    #
    # A batch, not a free-running worker pool, and results persisted in ROW
    # ORDER after each batch. Stages must be byte-reproducible offline
    # (`VENONA_OFFLINE=1`), and a pool that writes as calls land makes the
    # write order depend on model latency. Ordering the writes costs the
    # tail of each batch and buys determinism.
    index = 0
    for start in range(0, len(rows), max(1, settings.llm.concurrency)):
        ctx.check_cancel()

        batch = rows[start : start + max(1, settings.llm.concurrency)]
        if budget is not None:
            # `remaining`, not `check()`: `check()` answers "may I make one
            # more", which a batch of N would overshoot by up to N-1.
            if budget.remaining <= 0:
                remaining = len(rows) - start
                report.clusters_deferred += remaining
                report.warnings.append(budget.deferral(remaining, "stories"))
                funnel.drop("budget_deferred", remaining)
                break
            batch = batch[: budget.remaining]

        outcomes = await asyncio.gather(
            *(
                analyze_document(
                    conn=conn,
                    llm=ctx.llm,
                    settings=settings,
                    title=row["title"],
                    url=row["url"],
                    text=row["text_nfc"],
                    publisher=row["publisher"],
                    published_at=row["published_at"],
                    run_id=ctx.run_id,
                    call_id=ctx.ids.uuid7(),
                    created_at=ctx.clock.now(),
                    stage=ctx.stage,
                    subject=row["cluster_id"],
                    profile=profile,
                    system_prompt=system_prompt,
                    budget=budget,
                    rng=ctx.rng,
                    on_event=transcript_emitter(ctx, "analyze"),
                    # The stage this signal exists for: analyze runs for over
                    # an hour, so a call sitting out a provider's bad minute
                    # freezes the count under a segment that keeps pulsing.
                    # No stage named; the orchestrator fills in the one running.
                    on_wait=lambda retry_in, why: ctx.emit(
                        "stage_progress",
                        None,
                        waiting=retry_in is not None,
                        retry_in=round(retry_in) if retry_in is not None else None,
                        why=why,
                    ),
                )
                for row in batch
            ),
            # ONE DOCUMENT'S TERMINAL FAILURE IS NOT THE RUN'S. Without this the
            # first exception propagates out of `gather` and takes the whole
            # stage with it -- so `brief`, `curate` and the projection never run,
            # and a day's work is lost to one article. It also CANCELS the
            # siblings, whose model calls are already in flight and already being
            # paid for: their spend is never ledgered, because the code that
            # records it is what gets cancelled.
            #
            # The envelope-error path below already degrades per cluster. This
            # makes a raised exception take the same route.
            return_exceptions=True,
        )

        for row, outcome in zip(batch, outcomes, strict=True):
            index += 1

            if isinstance(outcome, BaseException):
                if isinstance(outcome, PermissionDenied):
                    # Never a result (`llm/executor.py`): the tool the model
                    # needed was denied, so every later call fails the same way
                    # and continuing would spend the rest of the budget proving
                    # it.
                    raise outcome
                report.clusters_skipped += 1
                funnel.drop("call_failed")
                report.warnings.append(str(outcome))
                continue

            # `outcome.calls` / `outcome.cost_usd`, not `outcome.result.*`: a
            # document that was retried made more than one call, and counting
            # only the last one made the run's own figures disagree with
            # `llm_calls`.
            await ctx.counter("llm_calls", outcome.calls)
            await ctx.counter("cost_usd", outcome.cost_usd)
            report.cost_usd += outcome.cost_usd

            if outcome.result.refused:
                report.refused += 1
                funnel.drop("refused")
                _mark_analyzed(conn, row["cluster_id"], now=ctx.clock.now())
                continue

            if not outcome.ok:
                report.clusters_skipped += 1
                funnel.drop("call_failed")
                report.warnings.append(str(outcome.result.error))
                continue

            persist_cluster(
                ctx,
                resolver=resolver,
                ontology=ontology,
                cluster_id=row["cluster_id"],
                article_id=row["doc_id"],
                data=outcome.result.data or {},
                report=report,
                rejections=rejections,
                rewrites=rewrites,
                rejected_shapes=rejected_shapes,
                profile_relevance=profile.relevance,
                text=row["text_nfc"],
            )
            report.clusters_analyzed += 1
            funnel.keep()

        # `data` is additive-only by C6, so the live numbers ride the event that
        # already exists rather than a new type the store would silently drop.
        # `backlog` is the un-LIMITed count, which is what makes "120 of 900"
        # readable while it happens.
        await ctx.emit(
            "stage_progress",
            "analyze",
            total=len(rows),
            current=index,
            kept=funnel.kept,
            backlog=backlog,
        )

    # C6's `entities_new`/`entities_seen` were declared and never assigned, so
    # a run that minted 115 entities and resolved 288 still reported 0/0 on the
    # run card while the analyze stage's own counts said otherwise. Emitted
    # once here rather than per-cluster because the orchestrator publishes the
    # `counter` event at stage end anyway.
    await ctx.counter("entities_new", report.entities_minted)
    await ctx.counter("entities_seen", report.entities_resolved)

    # These were hardcoded: `provisional_promote_sightings` was assigned in the
    # Resolver and never read, so editing config/settings.yaml did nothing.
    limits = ctx.settings.pipeline
    promoted = promote_provisionals(
        conn,
        now=ctx.clock.now(),
        min_sightings=limits.provisional_promote_sightings,
        min_sources=limits.provisional_promote_sources,
    )
    report.promoted = len(promoted)
    refresh_mention_counts(conn)

    report.alias_conflicts = repo.select_alias_conflicts(conn).fetchone()["n"]

    funnel.emit(ctx)
    for reason, n in rejections.items():
        ctx.metric("analyze", f"reject_{reason}", n)
    # Keyed by the ORIGINAL predicate: `rewrite_PART_OF` beside
    # `reject_ontology_PART_OF` is what lets a run answer whether the rewrite
    # table is absorbing the shapes the rejection log surfaces.
    for original, n in rewrites.items():
        ctx.metric("analyze", f"rewrite_{original}", n)
    ctx.metric("analyze", "entities_minted", report.entities_minted)
    ctx.metric("analyze", "entities_resolved", report.entities_resolved)
    ctx.metric("analyze", "relations_rejected", report.relations_rejected)
    # The per-shape aggregate the metric keys cannot carry (a key is one flat
    # string; the shape is three). This is what the ontology editor reads back.
    record_rejected_shapes(conn, run_id=ctx.run_id, shapes=rejected_shapes, at=ctx.clock.now())

    # ONE OUTAGE IS ONE MESSAGE. An expired credential fails every article in
    # the window identically, and warning per article wrote sixty near-identical
    # paragraphs onto `/admin/runs/:id` -- each one keyed by a cluster id, which
    # is this repo's word for a thing the reader has no way to act on. The
    # reason is what differs between real failures, so it is what groups them.
    for warning, n in Counter(report.warnings).items():
        ctx.warn(warning if n == 1 else f"{warning} (on {n} articles)")
    for notice in report.notices:
        ctx.note(notice)

    return report


def persist_cluster(
    ctx: RunContext,
    *,
    resolver: Resolver,
    ontology,
    cluster_id: str,
    article_id: str,
    data: dict,
    report: AnalyzeReport,
    rejections: dict[str, int] | None = None,
    rewrites: dict[str, int] | None = None,
    rejected_shapes: dict[tuple[str, str, str], dict] | None = None,
    profile_relevance: str = "",
    text: str = "",
) -> None:
    """One cluster's assertions and its `analyzed_at` stamp, or neither.

    `analyzed_at` IS the idempotency key -- `pending_clusters` selects on it
    being NULL -- so a death between the two leaves the assertions written and
    the cluster still pending, and the next run writes a SECOND copy of every
    one of them. Assertions are append-only by invariant 1 and carry a fresh
    uuid7 each, so nothing downstream would ever notice: it simply doubles the
    evidence behind every count the entity is promoted and ranked on.

    One transaction per CLUSTER rather than per batch. A batch-wide transaction
    would roll a sibling's good work back for a neighbour's failure, which is
    the stage-wide abort C5 fixed.

    RETRIED, because two lanes writing entities and assertions concurrently can
    deadlock and the server has already chosen a victim by the time we hear
    about it. A rolled-back transaction is safe to run again by definition --
    nothing it did survived -- which is the one condition `with_retry` requires.
    Losing a cluster's whole analysis to a lock cycle would mean paying the
    model for it a second time tomorrow.
    """

    def write() -> None:
        with db.transaction(ctx.db, what="analyze_cluster"):
            _ingest(
                ctx,
                resolver=resolver,
                ontology=ontology,
                cluster_id=cluster_id,
                article_id=article_id,
                data=data,
                report=report,
                rejections=rejections,
                rewrites=rewrites,
                rejected_shapes=rejected_shapes,
                text=text,
            )
            _store_scores(
                ctx.db,
                cluster_id,
                data,
                now=ctx.clock.now(),
                profile_relevance=profile_relevance,
            )

    retry.with_retry(write, what="analyze cluster")


def _ingest(
    ctx: RunContext,
    *,
    resolver: Resolver,
    ontology,
    cluster_id: str,
    article_id: str,
    data: dict,
    report: AnalyzeReport,
    # Optional so `tests/test_contracts.py` can call this directly to assert the
    # rejected-triple notice, which is what it is actually about.
    rejections: dict[str, int] | None = None,
    rewrites: dict[str, int] | None = None,
    rejected_shapes: dict[tuple[str, str, str], dict] | None = None,
    #: The document being read. Offered to the resolver so a surface declared
    #: ambiguous with readings can be settled by what this article actually
    #: names -- see `resolve.Resolver._pick_reading`. Empty is a caller with no
    #: document to offer, and an ambiguous surface then declines.
    text: str = "",
) -> None:
    conn = ctx.db
    now = ctx.clock.now()
    if rejections is None:
        rejections = {}
    if rejected_shapes is None:
        rejected_shapes = {}
    if rewrites is None:
        rewrites = {}

    # Surface -> resolved id, per document. Reused by the relation pass so a
    # subject and object mentioned once each resolve consistently.
    resolved: dict[tuple[str, str], str] = {}

    for entity in data.get("entities") or []:
        surface = (entity.get("surface") or "").strip()
        entity_type = entity.get("type") or ""
        if not surface:
            continue

        resolution = resolver.resolve(
            surface,
            entity_type,
            confidence=float(entity.get("confidence", 0.8)),
            article_id=article_id,
            now=now,
            # Optional in the schema and meaningless for five of the nine types,
            # so `None` is the normal answer. `_mint` validates it against the
            # vocabulary THIS type declares and discards a mismatch rather than
            # storing `program_office` on a Company.
            org_type=entity.get("org_type"),
            context=text,
        )

        if resolution.method == "ambiguous":
            report.entities_ambiguous += 1
            continue
        if not resolution.ok:
            # A bare `continue` since this shipped -- every rejected resolution
            # vanished without trace, so "the model named 900 entities and 700
            # became rows" had no explanation.
            rejections["entity_unresolved"] = rejections.get("entity_unresolved", 0) + 1
            continue

        resolved[(surface, entity_type)] = resolution.entity_id
        if resolution.created:
            report.entities_minted += 1
        report.entities_resolved += 1

        db.insert(
            conn,
            "assertions",
            {
                "id": ctx.ids.uuid7(),
                "run_id": ctx.run_id,
                "article_id": article_id,
                "cluster_id": cluster_id,
                "kind": "entity",
                "surface": surface,
                "entity_type": entity_type,
                "entity_id_raw": resolution.entity_id,
                "evidence_quote": (entity.get("context_sentence") or surface)[:2000],
                # Verified against THIS document before it is stored, so a row
                # here is quotable evidence rather than a model's summary.
                # `text` is empty for the direct callers in `tests/`, and the
                # gate then rejects everything, which is the right answer: a
                # sentence with no document to check it against is not
                # traceable to one.
                "definition_quote": define.verify_definition_sentence(
                    entity.get("definition_sentence"), surface, text
                ),
                "salience": float(entity.get("salience", 0.5)),
                "confidence": resolution.confidence,
                "method": resolution.method,
                # The model that ACTUALLY RAN, not the tier that was
                # configured. `assertions` is append-only provenance, and a
                # row naming a tier word cannot be traced to a provider.
                "model": resolve_model(ctx.llm, ctx.settings.llm.analyze_model),
                "created_at": now,
            },
        )

    for relation in data.get("relations") or []:
        subject_surface = (relation.get("subject_surface") or "").strip()
        object_surface = (relation.get("object_surface") or "").strip()
        subject_type = relation.get("subject_type") or ""
        object_type = relation.get("object_type") or ""
        predicate = relation.get("predicate") or ""
        evidence = (relation.get("evidence") or "").strip()

        # C1: an assertion nobody can trace to a sentence is not evidence.
        if not evidence:
            report.relations_rejected += 1
            rejections["no_evidence"] = rejections.get("no_evidence", 0) + 1
            continue

        # An ill-typed shape with exactly one safe legal rendering is rewritten
        # instead of dropped -- the ontology's `rewrites:` table, whose loader
        # guarantees the result passes `check_relation`. The ORIGINAL predicate
        # is kept for every counter and notice: keyed on the rewritten one,
        # `reject_ontology_PART_OF` would read zero whether or not anything was
        # fixed, and the metric this exists to produce would be self-fulfilling.
        asserted_predicate = predicate
        rewrite = ontology.rewrite_for(predicate, subject_type, object_type)
        if rewrite is not None:
            predicate = rewrite.to_predicate
            if rewrite.swap:
                # All four locals, as PAIRS. `resolved` is keyed (surface, type)
                # and `_resolve_endpoint` writes its argument pair back into
                # that cache -- a half-swap poisons every later relation in the
                # document.
                subject_surface, object_surface = object_surface, subject_surface
                subject_type, object_type = object_type, subject_type
            rewrites[asserted_predicate] = rewrites.get(asserted_predicate, 0) + 1
            report.notices.append(
                f"{article_id}: rewrote {asserted_predicate} -> {predicate}"
                f"{' (endpoints swapped)' if rewrite.swap else ''}: "
                f"{subject_surface or '?'} [{subject_type or '?'}] --{predicate}--> "
                f"{object_surface or '?'} [{object_type or '?'}]"
            )

        problem = ontology.check_relation(predicate, subject_type, object_type)
        if problem:
            report.relations_rejected += 1
            # Keyed by the predicate the MODEL asserted: "the ontology refused
            # 40 relations" is a number, "it refused 38 PRODUCES" is a finding
            # about the prompt.
            rejections[f"ontology_{asserted_predicate or 'unknown'}"] = (
                rejections.get(f"ontology_{asserted_predicate or 'unknown'}", 0) + 1
            )
            # This triple IS the asserted shape: the SAME parsed ontology object
            # does both the rewrite and this check, and a rewrite's result is
            # well-typed by the loader's invariant -- so nothing rewritten ever
            # reaches this branch. First example kept: as good as any, and
            # deterministic across re-runs.
            shape = rejected_shapes.setdefault(
                (predicate, subject_type, object_type),
                {
                    "n": 0,
                    "example_subject": subject_surface,
                    "example_object": object_surface,
                    "example_article_id": article_id,
                    "example_evidence": evidence[:500],
                },
            )
            shape["n"] += 1
            # The rejected triple, verbatim. Without it the warning names only
            # the ontology's example, and since the model's raw output is
            # clipped to 300 chars in the transcript there is then NO record
            # anywhere of what was actually dropped.
            report.notices.append(
                f"{article_id}: {problem} -- rejected: "
                f"{subject_surface or '?'} [{subject_type or '?'}] --{predicate}--> "
                f"{object_surface or '?'} [{object_type or '?'}]"
            )
            continue

        subject_id = resolved.get((subject_surface, subject_type)) or _resolve_endpoint(
            resolver, subject_surface, subject_type, article_id, ctx, resolved, context=text
        )
        object_id = resolved.get((object_surface, object_type)) or _resolve_endpoint(
            resolver, object_surface, object_type, article_id, ctx, resolved, context=text
        )
        if not subject_id or not object_id or subject_id == object_id:
            report.relations_rejected += 1
            # Three genuinely different failures shared one counter here, and
            # they point at different fixes: an unresolvable endpoint is the
            # RESOLVER's problem, a self-loop is the MODEL's.
            if subject_id and object_id:
                rejections["self_loop"] = rejections.get("self_loop", 0) + 1
            else:
                rejections["endpoint_unresolved"] = rejections.get("endpoint_unresolved", 0) + 1
            continue

        db.insert(
            conn,
            "assertions",
            {
                "id": ctx.ids.uuid7(),
                "run_id": ctx.run_id,
                "article_id": article_id,
                "cluster_id": cluster_id,
                "kind": "relation",
                "predicate": predicate,
                "predicate_kind": _kind_for(ontology, predicate, relation),
                "subject_id_raw": subject_id,
                "object_id_raw": object_id,
                "evidence_quote": evidence[:2000],
                # Unchanged through a rewrite, deliberately: the rewrite
                # corrects the predicate choice, not the evidence, and the
                # quote is the same sentence either way.
                "confidence": float(relation.get("confidence", 0.7)),
                "method": "extraction_rewritten" if rewrite is not None else "extraction",
                # The model that ACTUALLY RAN, not the tier that was
                # configured. `assertions` is append-only provenance, and a
                # row naming a tier word cannot be traced to a provider.
                "model": resolve_model(ctx.llm, ctx.settings.llm.analyze_model),
                "created_at": now,
            },
        )
        report.relations_written += 1


def _kind_for(ontology, predicate: str, relation: dict) -> str | None:
    """Qualifier for predicates that declare `kinds` in the ontology.

    The model is not asked for it (the schema has no such field yet), so this
    applies the declared default: for PART_OF that is `org_hierarchy`, which is
    correct for the overwhelming majority of extracted cases. A corporate or
    joint relationship is a curation decision, not an extraction one.
    """
    spec = ontology.predicates.get(predicate)
    if not spec or not spec.kinds:
        return None
    declared = relation.get("kind")
    return declared if declared in spec.kinds else spec.kinds[0]


def _resolve_endpoint(
    resolver: Resolver,
    surface: str,
    entity_type: str,
    article_id: str,
    ctx: RunContext,
    cache: dict[tuple[str, str], str],
    context: str = "",
) -> str | None:
    """A relation endpoint the entity list did not mention.

    Happens routinely -- the model names an organisation only in a relationship.
    Resolve it, but do not mint from a relation alone: an entity that appears
    nowhere in the extracted entity list is weak evidence.
    """
    if not surface:
        return None
    resolution = resolver.resolve(
        surface,
        entity_type,
        article_id=article_id,
        now=ctx.clock.now(),
        allow_mint=False,
        context=context,
    )
    if resolution.ok:
        cache[(surface, entity_type)] = resolution.entity_id
        return resolution.entity_id
    return None


def verified_hooks(hooks: Any, profile_relevance: str) -> list[str]:
    """The hooks that are genuinely IN the profile, dropping the rest.

    The prompt asks for "verbatim phrases copied from the profile", and they are
    shown to the reader as the evidence for a score. Until now nothing checked
    it, so a model steered by an injected document could put any sentence on a
    reader's screen wearing the authority of the operator's own profile.

    The same rule the acronym path has had since ADR 0003 -- the model never
    quotes itself, and a quote that is not a substring of what we supplied is
    rejected. This is that rule, applied to the other place a model is asked to
    quote something back.

    DROPPED, NOT REFUSED: an unverifiable hook is decoration on a score that is
    computed in Python from axes, so discarding it costs a line of explanation.
    Failing the cluster would throw away a paid-for analysis over a caption.

    Whitespace is normalised on both sides before comparing, because a model
    that re-wraps a copied line has still copied it -- and a check that fails on
    line breaks would drop nearly everything and look like the model ignoring
    the instruction.
    """
    if not isinstance(hooks, list):
        return []
    haystack = " ".join(profile_relevance.split()).casefold()
    kept: list[str] = []
    for hook in hooks:
        if not isinstance(hook, str):
            continue
        needle = " ".join(hook.split())
        if needle and needle.casefold() in haystack:
            kept.append(needle)
    return kept


def _store_scores(
    conn: db.Conn, cluster_id: str, data: dict, *, now: str, profile_relevance: str = ""
) -> None:
    relevance = data.get("relevance") or {}
    axes = relevance.get("axes") or {}
    scores = {k: float((v or {}).get("score", 0)) for k, v in axes.items()}
    overall = score_from(data)

    # `key_facts` and `flags` are REQUIRED by `analyze_schema`, so the model has
    # returned them on every call since S2 -- and nothing read them. That is the
    # density the brief wants and the opinion/press-release filter the research
    # prompt asks for in prose, both already paid for. Both columns are in
    # `0001_baseline.sql`.
    key_facts = [
        f.strip() for f in (data.get("key_facts") or []) if isinstance(f, str) and f.strip()
    ]
    flags = data.get("flags") or {}

    repo.update_clusters_by_relevance_overall_tier(
        conn,
        overall,
        tier_for(overall),
        relevance.get("note"),
        relevance.get("so_what"),
        db.jdump(
            {k: {"score": scores.get(k, 0), "why": (v or {}).get("why")} for k, v in axes.items()}
        ),
        db.jdump(verified_hooks(relevance.get("profile_hooks"), profile_relevance)),
        db.jdump(key_facts[:5]),
        db.jdump({k: bool(v) for k, v in flags.items()}) if flags else None,
        now,
        cluster_id,
    )


def _mark_analyzed(conn: db.Conn, cluster_id: str, *, now: str) -> None:
    """A refusal still counts as attempted -- otherwise every run retries it."""
    repo.update_clusters_by_analyzed_at_id(conn, now, cluster_id)
