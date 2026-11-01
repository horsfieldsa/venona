"""The recency floor on discovered pages, and why RSS is exempt from it.

The sweep prompt has always ASKED for recent results and nothing enforced it.
On `run:06a8160f` the sweeps returned 47 documents: 4 were under 36 hours old,
6 were undated, and 15 were more than a month old -- the oldest a PR Newswire
release from 313 days earlier, which got a body fetch, a cluster and a place in
the analyze queue.

The reason a bound does not blunt a sweep is in the same run's numbers. Split
the 47 by whether the domain is one we already subscribe to:

    already in our RSS list    7 docs   0 fresh   p50 409h   0 briefed
    a domain RSS does not have 40 docs  4 fresh              2 briefed

Every re-find of our own feeds was stale, *because* RSS had already delivered
the fresh ones from those domains. The two sweep items that reached the brief
came from militarnyi.com and navalnews.com and were both under a day old. So
source coverage and time depth are orthogonal, and bounding time is what forces
a sweep to reach for a source we do not already have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from venona.db import core as db
from venona.llm.executor import LlmResult, Usage
from venona.pipeline.fetch import run_fetch
from venona.pipeline.types import FrozenClock, RunContext
from venona.settings import get_settings

# `FrozenClock` starts at 2026-08-04T12:00:00Z; every age below is relative to it.
FRESH = "Sun, 03 Aug 2026 18:00:00 GMT"  # 18h
STALE = "Fri, 01 May 2026 12:00:00 GMT"  # ~95 days

BODY = (
    "<html><body><p>"
    + ("The Army awarded a command and control contract. " * 60)
    + "</p></body></html>"
)


@dataclass
class StubExecutor:
    candidates: list[dict[str, Any]] = field(default_factory=list)

    async def call(self, task: str, **kwargs) -> LlmResult:
        return LlmResult(
            data={"theme": "ngc2", "candidates": self.candidates},
            usage=Usage(),
            cost_usd=0.01,
        )


class FakeResponse:
    def __init__(self, text: str, last_modified: str | None) -> None:
        self.status = 200
        self.text = text
        self.headers = {"last-modified": last_modified} if last_modified else {}

    @property
    def ok(self) -> bool:
        return True

    @property
    def not_modified(self) -> bool:
        return False


class FakeHttp:
    """Serves a body per URL, with the Last-Modified that dates it.

    `resolve_date`'s ladder is feed > parsed > header > unknown, and a header
    date is the cheapest way to give a discovered page a real one -- which is
    the point: the date has to come out of the FETCH, because `discovery.py`
    throws the model's guess away on purpose.
    """

    def __init__(self, dates: dict[str, str | None]) -> None:
        self.dates = dates
        self.calls: list[str] = []

    async def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(url)
        return FakeResponse(BODY, self.dates.get(url))


def _config_dir(tmp_path: Path, kind: str) -> Path:
    """A config dir holding exactly one source of the given kind."""
    url = "\n    url: https://feed.example.com/rss" if kind == "rss" else ""
    (tmp_path / "sources.yaml").write_text(
        "defaults:\n"
        "  max_items: 10\n"
        "  fetch_body: true\n"
        "sources:\n"
        f"  - id: probe\n    name: Probe\n    kind: {kind}\n"
        f"    tags: [ngc2]{url}\n"
    )
    return tmp_path


def _run(conn: db.Conn, tmp_path: Path, *, kind: str, dates: dict[str, str | None]):
    import asyncio

    settings = get_settings().model_copy(
        update={"config_dir": _config_dir(tmp_path, kind), "offline": True}
    )
    candidates = [{"url": u, "title": f"Story {i}"} for i, u in enumerate(dates)]
    ctx = RunContext(
        run_id="run:recency-test",
        db=conn,
        llm=StubExecutor(candidates=candidates),
        settings=settings,
        clock=FrozenClock(),
        http=FakeHttp(dates),
    )
    return asyncio.run(run_fetch(ctx)), ctx


# ── discovery is bounded ─────────────────────────────────────────────


def test_a_fresh_discovered_page_is_kept(conn: db.Conn, tmp_path: Path) -> None:
    report, _ = _run(conn, tmp_path, kind="discovery", dates={"https://new.example.com/a": FRESH})
    assert report.documents_new == 1


@pytest.mark.negative
def test_a_stale_discovered_page_is_dropped(conn: db.Conn, tmp_path: Path) -> None:
    """The 313-day-old press release, in one assertion."""
    report, _ = _run(conn, tmp_path, kind="discovery", dates={"https://old.example.com/a": STALE})
    assert report.documents_new == 0
    assert report.documents_skipped == 1
    row = conn.execute("SELECT COUNT(*) AS n FROM documents WHERE source_id = 'probe'").fetchone()
    assert row["n"] == 0, "a dropped page must not reach the analyze queue"


@pytest.mark.negative
def test_an_undated_discovered_page_is_dropped(conn: db.Conn, tmp_path: Path) -> None:
    """Undated is the WIDEST state here, not a neutral one.

    `brief.build_retrieval` treats a missing `published_at` as always in-window,
    so an undated discovery result skips the freshness gate entirely. For an RSS
    feed that is an honest unknown worth keeping; for a page a search engine
    surfaced and we then could not date, it is the silent way to fill a brief
    with anything. Six of the 47 sweep documents landed here.
    """
    report, _ = _run(conn, tmp_path, kind="discovery", dates={"https://nodate.example.com/a": None})
    assert report.documents_new == 0
    assert report.documents_skipped == 1


def test_the_body_is_still_fetched_before_the_drop(conn: db.Conn, tmp_path: Path) -> None:
    """The floor cannot move earlier than this, and the cost is acceptable.

    A discovered candidate has no trustworthy date until the page is extracted,
    so one HTTP fetch is unavoidable. What the drop saves is the ~$0.117 analyze
    call behind it, which is where the money actually was.
    """
    _, ctx = _run(conn, tmp_path, kind="discovery", dates={"https://old.example.com/a": STALE})
    assert ctx.http.calls == ["https://old.example.com/a"]


# ── RSS is not ───────────────────────────────────────────────────────


def test_a_stale_rss_article_is_not_dropped_here(conn: db.Conn, tmp_path: Path) -> None:
    """A feed we subscribed to on purpose publishing last week is a different
    proposition from a search reaching back for it. RSS staleness is bounded at
    ANALYZE, by the brief's own window, so that the funnel reports it as
    `outside_window` rather than losing it at fetch.
    """
    import asyncio

    feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>F</title>'
        "<item><title>An older story</title>"
        "<link>https://feed.example.com/story</link>"
        "<pubDate>Fri, 01 May 2026 12:00:00 GMT</pubDate></item>"
        "</channel></rss>"
    )

    class FeedHttp(FakeHttp):
        async def get(self, url: str, **kwargs) -> FakeResponse:
            self.calls.append(url)
            if url.endswith("/rss"):
                return FakeResponse(feed, None)
            return FakeResponse(BODY, STALE)

    settings = get_settings().model_copy(
        update={"config_dir": _config_dir(tmp_path, "rss"), "offline": True}
    )
    ctx = RunContext(
        run_id="run:recency-rss",
        db=conn,
        llm=StubExecutor(),
        settings=settings,
        clock=FrozenClock(),
        http=FeedHttp({}),
    )
    report = asyncio.run(run_fetch(ctx))

    assert report.documents_new == 1, "a stale RSS article survives fetch"
