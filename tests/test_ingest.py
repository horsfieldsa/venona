"""Ingesting one article, named by URL.

The extension's toolbar button. Everything after the fetch is the ordinary
pipeline, so what is worth pinning here is the seam: that the URL is checked
before a run row exists, that exactly ONE cluster gets analysed, and that a page
which is not an article is refused rather than stubbed.
"""

from __future__ import annotations

import pytest
from venona.db import core as db
from venona.pipeline.analyze import pending_clusters
from venona.pipeline.ingest import IngestFailed, _title_from, ingest_url
from venona.pipeline.types import FrozenClock, RunContext, SeededIdGen
from venona.services import run_types
from venona.settings import get_settings

pytestmark = pytest.mark.anyio


ARTICLE = """
<html><head>
  <title>Army picks MOSA for CJADC2 | Breaking Defense</title>
  <meta property="og:title" content="Army picks MOSA for CJADC2" />
</head><body><article>
  <p>The Army selected a Modular Open Systems Approach for its CJADC2 effort,
     officials said on Tuesday, in a decision that Lockheed Martin and Anduril
     had both lobbied for over the preceding months of source selection.</p>
  <p>DARPA is separately funding work on DDIL operations, which the service
     expects to inform the next tranche of the programme as it moves toward a
     production decision later in the fiscal year.</p>
</article></body></html>
"""


class FakeResponse:
    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self.text = text
        self.headers: dict[str, str] = {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class FakeHttp:
    """`HttpClient` is only testable because of its `transport=` seam, but this
    path needs no transport at all -- `ingest_url` asks `ctx.http` for one page
    and does nothing else with the network."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[str] = []

    async def get(self, url: str):
        self.calls.append(url)
        return self.response


def _ctx(conn: db.Conn, http: FakeHttp) -> RunContext:
    return RunContext(
        run_id="run:ingest-test",
        db=conn,
        llm=None,
        settings=get_settings(),
        clock=FrozenClock(),
        ids=SeededIdGen(0),
        http=http,
    )


# ── the run type ────────────────────────────────────────────────────


def test_ingest_is_a_research_lane_run() -> None:
    """It writes `documents` and `clusters`, which is exactly what the single
    research slot guards. Light lane would let it race a pipeline run."""
    spec = run_types.RUN_TYPES["ingest"]
    assert spec.lane == run_types.RESEARCH
    assert spec.queued_slots == 1
    # ONE stage, and the router passes exactly this name to `launch`. C11 makes
    # `stages` a claim about what the run EMITS, and the two-stage version was
    # emitting none of them -- see `test_a_completed_ingest_records_its_result`,
    # which is the assertion that would have caught it.
    assert spec.stages == ("ingest",)


def test_type_for_never_raises_on_ingest() -> None:
    assert run_types.lane_for("ingest") == run_types.RESEARCH


# ── analysing exactly one cluster ───────────────────────────────────


def _second_pending_cluster(conn: db.Conn) -> str:
    """A backlog to be ignored. The seed carries one cluster, and the property
    under test only exists when there are at least two."""
    doc = conn.execute("SELECT * FROM documents LIMIT 1").fetchone()
    conn.execute(
        """
        INSERT INTO documents (id, url, url_key, title, text_nfc, text_chars,
                               content_hash, cluster_id, fetched_at, created_at)
        VALUES ('doc:backlog', 'https://x.test/backlog', 'x.test/backlog', 'Backlog',
                ?, ?, 'hash:backlog', 'cluster:backlog', '2026-01-01T00:00:00Z',
                '2026-01-01T00:00:00Z')
        """,
        (doc["text_nfc"], len(doc["text_nfc"])),
    )
    conn.execute(
        """
        INSERT INTO clusters (id, canonical_doc_id, title, published_at, analyzed_at,
                              first_seen, last_seen)
        VALUES ('cluster:backlog', 'doc:backlog', 'Backlog', '2026-01-01T00:00:00Z', NULL,
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.commit()
    return "cluster:backlog"


def test_pending_clusters_can_be_narrowed_to_one(conn: db.Conn) -> None:
    """Without this an ingest would analyse the whole unanalysed backlog, on the
    model's meter, because someone clicked a button about one page."""
    conn.execute("UPDATE clusters SET analyzed_at = NULL")
    conn.commit()
    backlog = _second_pending_cluster(conn)

    everything = [row["cluster_id"] for row in pending_clusters(conn)]
    assert len(everything) > 1
    assert backlog in everything

    target = next(cid for cid in everything if cid != backlog)
    narrowed = pending_clusters(conn, cluster_id=target)
    assert [row["cluster_id"] for row in narrowed] == [target]
    assert backlog not in [row["cluster_id"] for row in narrowed]


def test_narrowing_to_an_analysed_cluster_returns_nothing(conn: db.Conn) -> None:
    """ "Already analysed" must stay free. Re-analysing on every re-ingest is the
    expensive direction."""
    conn.execute("UPDATE clusters SET analyzed_at = NULL")
    conn.commit()
    target = pending_clusters(conn)[0]["cluster_id"]

    conn.execute("UPDATE clusters SET analyzed_at = '2026-01-01T00:00:00Z' WHERE id = ?", (target,))
    conn.commit()
    assert pending_clusters(conn, cluster_id=target) == []


# ── titles and publishers ───────────────────────────────────────────


def test_og_title_beats_the_title_tag() -> None:
    """A `<title>` is usually the headline plus the masthead, and every article
    ending in the same suffix makes unrelated stories look near-identical."""
    assert _title_from(ARTICLE, "https://example.com/x") == "Army picks MOSA for CJADC2"


def test_a_page_with_no_title_falls_back_to_the_slug() -> None:
    title = _title_from("<html><body>nothing</body></html>", "https://x.com/army-picks-mosa/")
    assert title == "army picks mosa"


# There is no `_publisher_from` here any more, and its test went with it.
# Deriving the host locally short-circuited `resolve_publisher` at its FIRST
# rung with the value that function's LAST rung exists to produce -- so the one
# path that always holds real fetched article HTML was the only path that never
# read the page's own masthead. It was also weaker than the rung it pre-empted:
# it stripped `www.` but neither userinfo nor the port. The behaviour it pinned
# is now `normalize.resolve_publisher`'s, tested in `test_sources.py`, and what
# is worth pinning HERE is that this path reaches it -- below.


# ── the guard ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "https://user:pass@example.com/x",
        "https://example.com:22/x",
    ],
)
async def test_blocked_urls_are_refused_before_a_run_exists(api_client, url: str) -> None:
    """400 with a sentence, not a run that fails thirty seconds later for
    reasons the caller has to go and read."""
    res = await api_client.post("/api/ingest", json={"url": url})
    assert res.status_code in (400, 422), res.text


async def test_a_busy_research_lane_refuses_with_409(api_client) -> None:
    """C11: the research lane REFUSES, it does not queue.

    This router was the one research-lane endpoint with no `except LaneBusy`,
    so a routine refusal reached the `Exception` backstop and came back as an
    opaque 500 with a stack trace behind it. The extension's badge showed a bare
    cross, and the reader was told the API was broken when the real answer was
    "a nightly run is in flight". `runs.py` and `briefs.py` have always
    converted it; only this one did not.
    """
    from venona.services import run_service

    await run_service.registry.submit("run:pipeline-in-flight", "on_demand")
    try:
        res = await api_client.post("/api/ingest", json={"url": "https://example.com/story"})
    finally:
        await run_service.registry.release("run:pipeline-in-flight")

    assert res.status_code == 409, res.text
    # A sentence naming the blocker, not `internal_error`.
    assert "internal" not in res.text.lower()


async def test_a_completed_ingest_records_its_result(api_client, monkeypatch) -> None:
    """`launch` must be given a stage, or the entire result is thrown away.

    `_runner` gates `record_stage`, `stage_finished` and the counter flush
    behind `if stage:`. The router built a full `data` dict -- url, document id,
    title, entity counts, warnings -- and passed no stage, so none of it was
    ever written and `/runs/{id}` rendered an empty panel for every ingest,
    success or failure. Nothing asserted the stage row existed, which is why the
    declaration in `run_types` could claim two stages while zero were emitted.
    """
    from venona.services import run_launcher

    seen: dict[str, object] = {}
    real = run_launcher.launch

    async def spy(request, **kwargs):
        seen.update(kwargs)
        return {"run_id": "run:x", "status": "running", "lane": run_types.RESEARCH}

    monkeypatch.setattr(run_launcher, "launch", spy)
    res = await api_client.post("/api/ingest", json={"url": "https://example.com/story"})

    assert res.status_code == 202, res.text
    assert seen["stage"] == "ingest", "no stage means `outcome.data` is computed and discarded"
    assert seen["stage"] in run_types.RUN_TYPES["ingest"].stages, (
        "the stage the router opens must be one the run type declares"
    )
    assert real is not spy  # guard against the monkeypatch silently not applying


# ── ingest_url ──────────────────────────────────────────────────────


async def test_a_page_that_is_not_an_article_is_refused(conn: db.Conn) -> None:
    """`build_document` returns None rather than a stub when there is not enough
    text. A paywall interstitial analysed as an article produces confident
    nonsense."""
    http = FakeHttp(FakeResponse(200, "<html><body><p>Subscribe to read.</p></body></html>"))
    with pytest.raises(IngestFailed, match="not enough article text"):
        await ingest_url(_ctx(conn, http), "https://example.com/paywalled")


async def test_a_dead_page_is_refused(conn: db.Conn) -> None:
    http = FakeHttp(FakeResponse(404, ""))
    with pytest.raises(IngestFailed, match="404"):
        await ingest_url(_ctx(conn, http), "https://example.com/gone")


async def test_an_article_becomes_a_document(conn: db.Conn, monkeypatch) -> None:
    """The fetch-and-store half, with analyze stubbed out -- it is the ordinary
    stage and costs a model call."""
    from venona.pipeline import ingest as ingest_mod

    class FakeAnalysis:
        clusters_analyzed = 1
        entities_minted = 2
        entities_resolved = 3
        cost_usd = 0.0
        warnings: list[str] = []

    async def fake_analyze(ctx, *, only_cluster=None, **kw):
        assert only_cluster is not None, "an ingest must never analyse the whole backlog"
        return FakeAnalysis()

    monkeypatch.setattr(ingest_mod, "run_analyze", fake_analyze)

    http = FakeHttp(FakeResponse(200, ARTICLE))
    report = await ingest_url(_ctx(conn, http), "https://breakingdefense.com/2026/08/mosa/")

    assert report.is_new
    assert report.title == "Army picks MOSA for CJADC2"
    assert report.document_id
    assert report.cluster_id
    assert report.analyzed

    row = conn.execute(
        "SELECT source_id, publisher, site_domain, text_nfc FROM documents WHERE id = ?",
        (report.document_id,),
    ).fetchone()
    assert row["source_id"] == "manual", "not a sources.yaml id -- it has no feed or schedule"
    # No `og:site_name` on this page, so the byline falls all the way to the
    # domain. That it is DERIVED and not asserted is the point.
    assert row["publisher"] == "breakingdefense.com"
    assert row["site_domain"] == "breakingdefense.com", "promotion needs two distinct publishers"
    assert "Modular Open Systems Approach" in row["text_nfc"]


async def test_ingest_reads_the_page_masthead(conn: db.Conn, monkeypatch) -> None:
    """The manual path reaches `resolve_publisher`'s page rung.

    It could not: `ingest_url` passed a locally derived host as `publisher`,
    which is non-empty for any page that just answered 200, so the ladder
    returned at rung 1 every time. The Add-URL box and the extension wrote
    `breakingdefense.com` while the same article arriving via a sweep got
    `Breaking Defense` -- two bylines for one document, decided by which door it
    came through, and this is the door a human is watching.
    """
    from venona.pipeline import ingest as ingest_mod

    async def fake_analyze(ctx, **kw):
        class R:
            clusters_analyzed = entities_minted = entities_resolved = 0
            cost_usd = 0.0
            warnings: list[str] = []

        return R()

    monkeypatch.setattr(ingest_mod, "run_analyze", fake_analyze)

    page = ARTICLE.replace(
        "<title>", '<meta property="og:site_name" content="Breaking Defense" /><title>'
    )
    http = FakeHttp(FakeResponse(200, page))
    report = await ingest_url(_ctx(conn, http), "https://breakingdefense.com/2026/08/mosa-2/")

    publisher = conn.execute(
        "SELECT publisher FROM documents WHERE id = ?", (report.document_id,)
    ).fetchone()["publisher"]
    assert publisher == "Breaking Defense"


async def test_ingesting_the_same_url_twice_is_not_a_new_document(
    conn: db.Conn, monkeypatch
) -> None:
    from venona.pipeline import ingest as ingest_mod

    class FakeAnalysis:
        clusters_analyzed = 0
        entities_minted = 0
        entities_resolved = 0
        cost_usd = 0.0
        warnings: list[str] = []

    async def fake_analyze(ctx, *, only_cluster=None, **kw):
        return FakeAnalysis()

    monkeypatch.setattr(ingest_mod, "run_analyze", fake_analyze)

    url = "https://breakingdefense.com/2026/08/mosa/"
    http = FakeHttp(FakeResponse(200, ARTICLE))
    first = await ingest_url(_ctx(conn, http), url)
    second = await ingest_url(_ctx(conn, http), url)

    assert first.is_new
    assert not second.is_new
    assert "already in the corpus" in second.warnings
    assert first.document_id == second.document_id
