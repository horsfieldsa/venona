"""Ingest one article, named by URL.

The reader is on a page the nightly run did not pick up -- a source not in
`sources.yaml`, or a story that came out an hour ago -- and wants it in the
corpus. Everything after the fetch is the ordinary pipeline: the same
`build_document`, the same dedup, the same analyze stage with the same
resolver, so a manually ingested article is indistinguishable from a
nightly one once it lands. There is no second code path for entities.

WHY THIS IS RESEARCH LANE. It writes `documents` and `clusters`, which is
precisely the hazard the single research slot exists to guard: two pipelines
writing those tables with independent budgets, and the second one to finish
deciding what the corpus looks like. So an ingest is refused while a research
run is in flight (409), rather than queued behind it. That is the C11 rule for
the lane, not a decision taken here.

WHY IT ANALYSES EXACTLY ONE CLUSTER. `pending_clusters` answers "what has not
been analysed", which after a partial run can be dozens. A reader clicking
"ingest this article" is asking about one page, and spending the model's budget
on an unrelated backlog because of that click would be a surprise of the
expensive kind. Hence `only_cluster`.

ON SSRF. `urlguard.check_static` here catches the scheme, port and userinfo
tricks that are parsing questions; the DNS half happens inside `ctx.http`,
which resolves, vets and PINS every hop (`sources/urlguard.py` explains the
two-layer split). Note this URL comes from the user's own address bar rather
than from a model's search results, which is a materially better provenance
than the case urlguard was written for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from venona.db import core as db
from venona.db.repositories import pipeline_ingest as repo
from venona.pipeline import dedup, normalize
from venona.pipeline.analyze import run_analyze
from venona.pipeline.types import RunContext
from venona.sources import urlguard

#: `documents.source_id` for anything that arrived this way. Not a
#: `sources.yaml` id on purpose -- it has no feed, no schedule and no adapter,
#: and putting it in that file would imply all three.
MANUAL_SOURCE_ID = "manual"

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG_TITLE = re.compile(
    r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"'](.*?)[\"']", re.IGNORECASE
)


@dataclass
class IngestReport:
    url: str
    document_id: str | None = None
    cluster_id: str | None = None
    title: str | None = None
    is_new: bool = False
    analyzed: bool = False
    entities_minted: int = 0
    entities_resolved: int = 0
    cost_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _title_from(html: str | None, url: str) -> str:
    """og:title first -- a `<title>` is usually the headline plus the masthead,
    and "… | Breaking Defense" in every title makes two unrelated articles look
    like near-duplicates to a human reading the queue."""
    for pattern in (_OG_TITLE, _TITLE):
        if not html:
            break
        found = pattern.search(html)
        if found:
            text = re.sub(r"\s+", " ", found.group(1)).strip()
            if text:
                return text[:300]
    # Better than "Untitled": the slug is usually the headline with hyphens.
    tail = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return tail.replace("-", " ").replace("_", " ").strip() or url


async def ingest_url(ctx: RunContext, url: str) -> IngestReport:
    """Fetch, store and analyse a single article."""
    report = IngestReport(url=url)

    # Every one of these has to land in TWO places. `report.warnings` is what
    # the router puts in the run's stage counts; `ctx` is what `finish_run`
    # reads to write the run's own warning list. Appending only to the report
    # meant a reader looking at `/runs/{id}` saw an ingest with no warnings at
    # all, however much had gone sideways.
    #
    # `warn` is a DEGRADATION and sets `ctx.degraded`; `note` is an observation.
    # Being already in the corpus is not a degradation -- nothing failed, there
    # was simply nothing left to do.
    def warn(message: str) -> None:
        ctx.warn(message)
        report.warnings.append(message)

    def note(message: str) -> None:
        ctx.note(message)
        report.warnings.append(message)

    # Raises BlockedUrl, which the router turns into a 400 with the reason in
    # it. Failing at submit time with a sentence beats failing at fetch time
    # with a timeout.
    urlguard.check_static(url)

    conn: db.Conn = ctx.db
    # kwargs, not a dict: `RunContext.emit` takes `**data`, unlike
    # `LaunchContext.emit`, which takes the dict. The two look alike at a call
    # site and are not.
    await ctx.emit("stage_progress", "fetch", url=url, current=0, total=1)

    response = await ctx.http.get(url)
    if not response.ok:
        raise IngestFailed(f"the page answered {response.status}")
    html = response.text

    document = normalize.build_document(
        url=url,
        title=_title_from(html, url),
        html=html,
        text=None,
        source_id=MANUAL_SOURCE_ID,
        # NOTHING arrived with this: there is no feed entry and no model answer,
        # only a URL someone pasted. Passing the host here -- which is what this
        # did -- short-circuited `resolve_publisher` at its first rung with the
        # value its LAST rung exists to produce, so the one path that always
        # holds real fetched article HTML was the one path that never read the
        # page's own masthead. The Add-URL box and the extension wrote
        # `navalnews.com` while the same article arriving via a sweep got
        # `Naval News`: two bylines for one document, decided by which door it
        # came through, and the manual one is the door a human is watching.
        #
        # The local helper it replaced was also weaker than the rung it
        # pre-empted: it stripped `www.` but neither userinfo nor the port.
        publisher=None,
        feed_date=None,
        header_date=response.headers.get("last-modified"),
        summary=None,
    )
    if document is None:
        # `build_document` returns None rather than a stub when there is not
        # enough text to be an article -- a paywall interstitial, a video page,
        # a landing page. Analysing one of those produces confident nonsense.
        raise IngestFailed("there is not enough article text on that page to analyse")

    outcome = dedup.take(ctx, document)
    # ONE cluster: the one this article landed in. `classify` already named it,
    # and a cluster's canonical document depends only on its own members, so
    # re-deriving the rest of the corpus to add a single article is work whose
    # cost grows daily while the article stays one article.
    dedup.refresh_clusters(conn, now=ctx.clock.now(), cluster_ids=[outcome.cluster_id])

    report.document_id = document.id
    report.title = document.title
    report.is_new = outcome.is_new
    if outcome.is_new:
        # `run_types` declares `documents_new` in this kind's summary, and
        # nothing on this path had ever incremented it -- so the one number an
        # ingest most obviously reports was structurally always zero.
        await ctx.counter("documents_new")
    else:
        note("already in the corpus")

    row = repo.select_documents_by_id(conn, document.id).fetchone()
    report.cluster_id = row["cluster_id"] if row else None
    if report.cluster_id is None:
        warn("stored, but it landed in no cluster to analyse")
        return report

    analysis = await run_analyze(ctx, only_cluster=report.cluster_id)
    report.analyzed = analysis.clusters_analyzed > 0
    report.entities_minted = analysis.entities_minted
    report.entities_resolved = analysis.entities_resolved
    report.cost_usd = analysis.cost_usd
    for message in analysis.warnings:
        warn(message)
    if not report.analyzed and not analysis.warnings:
        # The usual reason: this cluster was already analysed on a previous run,
        # so `pending_clusters` correctly declined to pay for it twice.
        note("already analysed")

    return report


class IngestFailed(RuntimeError):
    """The page cannot become a document. Carries a sentence for the reader."""
