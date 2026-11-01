"""Corroborate entities that are blocking relationships.

The graph is sparse for a reason that is not weak extraction. Measured 2026-08-06 on the
live corpus: Postgres holds **468 distinct relationship triples** and Neo4j shows
**97**, because `projector.py` projects only `status='active'` entities and an
edge whose endpoint has no node is dropped by the Cypher `MATCH`. So 79% of what
the system already knows about how things relate is invisible, held back by the
corroboration gate.

Promotion needs two articles from two distinct PUBLISHERS, and it is entirely
passive -- it happens only if some future article we happen to ingest mentions
the entity again. Of the 435 provisional entities that are relation endpoints,
**423 sit at exactly one article**. Nothing seeks the second. Two entities make
the point: `govt:government-accountability-office` has 24 articles from one
publisher (gao.gov) and can never
self-corroborate, and Sikorsky -- blocking five relationships -- was mentioned
once.

**This module does not extract relationships.** It finds DOCUMENTS about stuck
entities and hands them to the existing pipeline. Relations still arrive the only
way they ever have: the model reading real article text, with a verbatim evidence
quote and an ontology check. No new trust surface, and invariant 3 is untouched.

That works because analyze picks work up by STATE, not by run id --
`pending_clusters` is `WHERE analyzed_at IS NULL`. A job that produces URLs gets
extraction, promotion, acronym grounding and the graph for free:

    rank stuck entities -> targeted sweep -> candidate URLs
      -> fetch/extract/normalize/dedup -> analyze -> promote -> project

Nothing needs re-analysing to benefit. The assertions are already on disk; the
moment an endpoint earns `active`, the next projection picks its edges up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from venona.db import core as db
from venona.db.repositories import pipeline_research as repo
from venona.sources.base import SourceSpec

# The synthetic source every researched document is attributed to.
#
# `documents.source_id` is a foreign key, so researched documents need a row in
# `sources` -- but this must NOT go in `config/sources.yaml`. A config source is
# fetched every run and carries a strike counter; a one-off query that returns
# nothing is not a dead feed, and three of them would walk it to
# `auto_disabled`, a one-way door nothing resets but a success.
RESEARCH_SOURCE_ID = "research"


def coverage_outcome(promoted: int, *, degraded: bool) -> str | None:
    """What a corroboration sweep found, for both of its launchers.

    `delivered` only when a name actually gained its second publisher. A
    degraded run that promoted nothing has no answer at all: the reading that
    would have found one did not finish, and "no coverage" there reports our
    quota as a fact about the press -- 309 articles fetched and unread on
    2026-10-22, filed as nothing found.
    """
    if promoted:
        return "delivered"
    return None if degraded else "nothing_found"


@dataclass
class StuckEntity:
    entity_id: str
    name: str
    entity_type: str
    status: str
    blocked: int
    articles: int
    publishers: int
    #: Edges that become visible the moment THIS name is confirmed, because
    #: their other end already is. What the ranking is on, and the honest answer
    #: to "what will this sweep buy". Defaulted so a caller building one of
    #: these by hand -- the tests do -- need not know the ranking's arithmetic.
    ready: int = 0


@dataclass
class ResearchReport:
    targeted: list[StuckEntity] = field(default_factory=list)
    candidates: int = 0
    documents_new: int = 0
    documents_duplicate: int = 0
    sweeps: int = 0
    cost_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)
    #: Observations that are NOT degradations. Drained through `ctx.note`.
    notices: list[str] = field(default_factory=list)

    @property
    def blocked_total(self) -> int:
        return sum(e.blocked for e in self.targeted)

    @property
    def ready_total(self) -> int:
        """Edges one confirmation away across everything this sweep targeted.

        Reported separately from `blocked_total` because they answer different
        questions: that one is how much of the graph these names are sitting on,
        this one is how much of it could appear if the sweep succeeds. The gap
        between them is edges whose other end is unconfirmed too, and no single
        sweep can reach those.
        """
        return sum(e.ready for e in self.targeted)

    def as_dict(self) -> dict:
        return {
            "targeted": len(self.targeted),
            "blocked_relationships": self.blocked_total,
            "ready_relationships": self.ready_total,
            "candidates": self.candidates,
            "documents_new": self.documents_new,
            "documents_duplicate": self.documents_duplicate,
            "sweeps": self.sweeps,
        }


def stuck_entities(
    conn: db.Conn, *, limit: int = 10, entity_ids: list[str] | None = None
) -> list[StuckEntity]:
    """Uncorroborated entities, ranked by how many relationships they block.

    Ranking by blocked triples rather than by mention count is the whole point:
    an entity mentioned twice that sits on no relationship costs the graph
    nothing, while Fincantieri -- mentioned once -- is holding seven edges out of
    the projection.

    `dormant` is included deliberately. An entity blocking five relationships is
    a research target, not a retired one, and the 30-day dormancy rule would
    otherwise quietly remove exactly the entities most worth chasing.
    """

    return [
        StuckEntity(
            entity_id=row["id"],
            name=row["name"],
            entity_type=row["type"],
            status=row["status"],
            blocked=row["blocked"],
            ready=row["ready"] or 0,
            articles=row["articles"] or 0,
            publishers=row["publishers"] or 0,
        )
        for row in repo.stuck_entities(conn, entity_ids=list(entity_ids or []), limit=limit)
    ]


def ensure_source(conn: db.Conn, *, now: str) -> None:
    """Register the synthetic source once. See `RESEARCH_SOURCE_ID`."""
    db.upsert(
        conn,
        "sources",
        {
            "id": RESEARCH_SOURCE_ID,
            "name": "Targeted corroboration research",
            "kind": "discovery",
            "url": None,
            "publisher": None,
            "enabled": 1,
            "tags": db.jdump(["research"]),
            "created_at": now,
        },
        key=["id"],
    )
    repo.insert_source_health(conn, RESEARCH_SOURCE_ID)


def _spec(model: str | None, max_items: int) -> SourceSpec:
    """An in-memory spec, never loaded from `sources.yaml`.

    Building it here rather than adding a config entry keeps the registry, the
    strike counter and `sync_sources` out of the picture entirely -- see
    `RESEARCH_SOURCE_ID` for why that matters.
    """
    return SourceSpec(
        id=RESEARCH_SOURCE_ID,
        name="Targeted corroboration research",
        kind="discovery",
        model=model,
        max_items=max_items,
        fetch_body=True,
        tags=[],
    )


def sweep_prompt(targets: list[StuckEntity], *, dateline: str) -> str:
    """What to search for.

    Names, not themes. The standing sweeps in `sources.yaml` ask "what happened
    in undersea autonomy?"; this asks "what else has been written about
    Fincantieri?" -- a different question with a different answer, and the reason
    a themed sweep never corroborates these entities no matter how often it runs.

    The instruction to prefer a DIFFERENT outlet is load-bearing. Promotion
    counts distinct `site_domain`, so three more articles from the publisher we
    already have corroborates nothing.
    """
    lines = [
        dateline,
        "Find published articles about each of the following, from news outlets, "
        "trade press, government releases or company announcements.",
        "",
        "Each of these has been reported ONCE, by a single outlet. What is needed "
        "is independent coverage -- so strongly prefer a DIFFERENT publisher to "
        "the one listed, and prefer substantive reporting over listings or "
        "directory entries.",
        "",
    ]
    for target in targets:
        lines.append(f"- {target.name} ({target.entity_type})")
    lines += [
        "",
        "Return the URL of each source page. Do not summarise; the pipeline "
        "fetches and reads each page itself.",
    ]
    return "\n".join(lines)


async def run_research(
    ctx,
    *,
    entity_ids: list[str] | None = None,
    limit: int = 10,
    per_sweep: int | None = None,
    max_sweeps: int | None = None,
    budget=None,
) -> ResearchReport:
    """Sweep for coverage of stuck entities and ingest what comes back.

    Ingest only. Promotion happens where it always has, in
    `promote_provisionals`, off evidence that reached the database through the
    ordinary path -- this function never touches `entities.status`.
    """
    from venona.pipeline import dedup, normalize
    from venona.pipeline.fetch import fetch_body
    from venona.sources.discovery import collect_discovery

    conn = ctx.db
    limits = ctx.settings.pipeline
    per_sweep = per_sweep or limits.research_entities_per_sweep
    max_sweeps = max_sweeps or limits.research_max_sweeps

    report = ResearchReport()
    report.targeted = stuck_entities(conn, limit=limit, entity_ids=entity_ids)
    if not report.targeted:
        # A NOTICE: every relationship already has corroboration, so there was
        # nothing to look for. Nothing degraded.
        report.notices.append("every relationship already has a second source")
        return report

    ensure_source(conn, now=ctx.clock.now())
    spec = _spec(ctx.settings.llm.research_model, per_sweep * 4)

    dateline = "" if ctx.settings.offline else f"Today is {ctx.clock.now()[:10]}.\n\n"

    # Batched, because one call covering ten names costs the same as one
    # covering one. The cap is on SWEEPS, not entities, for that reason.
    batches = [
        report.targeted[i : i + per_sweep] for i in range(0, len(report.targeted), per_sweep)
    ][:max_sweeps]

    collected = []
    for batch in batches:
        ctx.check_cancel()
        result = await collect_discovery(
            spec, ctx, budget=budget, prompt=sweep_prompt(batch, dateline=dateline)
        )
        report.sweeps += 1
        if result.error:
            report.warnings.append(result.error)
        collected.extend(result.items)

    report.candidates = len(collected)
    if not collected:
        report.warnings.append("no candidate URLs returned")
        return report

    # The ordinary ingest path, deliberately. A researched document is
    # indistinguishable downstream from an RSS one -- same extraction, same
    # dedup, same analyze -- which is what makes this safe to add.
    known_url_keys = {row["url_key"] for row in repo.select_documents(conn)}
    http = ctx.http

    for item in collected:
        ctx.check_cancel()
        url_key = normalize.url_key_for(item.url)
        if url_key in known_url_keys:
            # Already have it. Corroboration needs a NEW article, so this is a
            # miss, not a win -- and a common one when the model finds the same
            # piece we already ingested.
            report.documents_duplicate += 1
            continue

        html, header_date, _why_not = await fetch_body(item, http)
        document = normalize.build_document(
            url=item.url,
            title=item.title,
            html=html,
            text=None,
            source_id=RESEARCH_SOURCE_ID,
            publisher=item.publisher,
            publisher_asserted=item.publisher_asserted,
            feed_date=None,
            header_date=header_date,
            summary=None,
        )
        if document is None:
            report.documents_duplicate += 1
            continue

        outcome = dedup.take(ctx, document)
        if outcome.is_new:
            report.documents_new += 1
            known_url_keys.add(url_key)
            await ctx.counter("documents_new")
        else:
            report.documents_duplicate += 1
        await ctx.counter("documents_fetched")

    dedup.refresh_clusters(conn, now=ctx.clock.now(), source_order={RESEARCH_SOURCE_ID: 0})

    for warning in report.warnings:
        ctx.warn(warning)
    for notice in report.notices:
        ctx.note(notice)
    return report
