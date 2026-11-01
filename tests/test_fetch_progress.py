"""`stage_progress` from the fetch stage actually advances.

WHAT SHIPPED, AND WHY NOTHING SAW IT. The Runs spine read `Fetch 0/17` for the
whole stage. `17` was the SOURCE count, emitted once with `current=0` in front of
a single `asyncio.gather` over every feed -- so the display sat on zero for the
entire network-bound phase, which is the part that takes the minutes. Then the
only advancing emit sat at the FOOT of the document loop, behind
`if index % 10 == 0`, and three `continue` statements jumped over it. On a second
run over the same feeds nearly every item is an already-known `url_key`, so item
10, 20 and 30 each took a `continue` and the counter emitted nothing at all.

Every existing fetch test asserted on the returned `FetchReport`, which was
correct throughout -- the counters were right and the display was blind. So these
assert on the EVENTS, which is the thing a reader actually looks at, and the
duplicate-run case below is the one the old code could not report.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from venona.db import core as db
from venona.pipeline.fetch import run_fetch
from venona.pipeline.types import FrozenClock, RunContext
from venona.settings import get_settings

BODY = (
    "<html><body><article><p>"
    + ("The Army awarded a command and control contract for the data layer. " * 40)
    + "</p></article></body></html>"
)


def _feed(n: int) -> str:
    items = "".join(
        f"<item><title>Story {i} about the common data layer</title>"
        f"<link>https://example.test/story-{i}</link>"
        f"<pubDate>Mon, 04 Aug 2026 09:00:00 GMT</pubDate></item>"
        for i in range(n)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.status = 200
        self.text = text
        self.headers: dict[str, str] = {}

    @property
    def ok(self) -> bool:
        return True

    @property
    def not_modified(self) -> bool:
        return False


class FakeHttp:
    """Feed URLs get the feed, everything else gets an article body."""

    def __init__(self, feeds: dict[str, str]) -> None:
        self.feeds = feeds

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(self.feeds.get(url, BODY))


def _config_dir(tmp_path: Path, sources: int) -> Path:
    lines = [
        "defaults:\n",
        "  max_items: 50\n",
        "  fetch_body: true\n",
        "sources:\n",
    ]
    for i in range(sources):
        lines.append(
            f"  - id: probe{i}\n    name: Probe {i}\n    kind: rss\n"
            f"    tags: [ngc2]\n    url: https://feed{i}.example.test/rss\n"
        )
    (tmp_path / "sources.yaml").write_text("".join(lines))
    return tmp_path


def _run(conn: db.Conn, tmp_path: Path, *, sources: int, items: int) -> list[dict[str, Any]]:
    """Run fetch once and return the `stage_progress` payloads it emitted."""
    settings = get_settings().model_copy(
        update={"config_dir": _config_dir(tmp_path, sources), "offline": True}
    )
    feeds = {f"https://feed{i}.example.test/rss": _feed(items) for i in range(sources)}

    events: list[dict[str, Any]] = []

    async def emit(type_: str, stage: str | None, data: dict[str, Any]) -> None:
        if type_ == "stage_progress":
            events.append(data)

    ctx = RunContext(
        run_id="run:fetch-progress",
        db=conn,
        llm=None,
        settings=settings,
        clock=FrozenClock(),
        http=FakeHttp(feeds),
        _emit=emit,
    )
    asyncio.run(run_fetch(ctx))
    return events


def _phase(events: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    return [e for e in events if e.get("phase") == phase]


# ── the collection phase ─────────────────────────────────────────────


def test_the_source_counter_reaches_the_source_count(conn: db.Conn, tmp_path: Path) -> None:
    """The `0/17` case. Four feeds, so four completions plus the priming zero."""
    sources = _phase(_run(conn, tmp_path, sources=4, items=2), "sources")

    assert [e["current"] for e in sources] == [0, 1, 2, 3, 4]
    assert {e["total"] for e in sources} == {4}


# ── the document phase ───────────────────────────────────────────────


def test_the_document_counter_reaches_the_total(conn: db.Conn, tmp_path: Path) -> None:
    """13 documents, so the decade gate alone would stop at 10 and the final
    partial decade -- the last three -- would never be reported."""
    docs = _phase(_run(conn, tmp_path, sources=1, items=13), "documents")
    currents = [e["current"] for e in docs]

    assert currents == sorted(currents), "a counter that goes backwards is worse than a stuck one"
    assert currents[-1] == 13, f"never reached the total: {currents}"
    assert docs[-1]["total"] == 13


def test_a_run_of_pure_duplicates_still_advances(conn: db.Conn, tmp_path: Path) -> None:
    """THE REGRESSION. The second run over the same feed takes the
    `duplicate_url_key` `continue` on every single item, which is what used to
    jump over the emit entirely. A reader watching this stage is asking how far
    along it is; a dropped item is still an item read.
    """
    first = _phase(_run(conn, tmp_path, sources=1, items=13), "documents")
    assert first[-1]["current"] == 13, "precondition: the first run stored the documents"

    second = _phase(_run(conn, tmp_path, sources=1, items=13), "documents")

    assert second[-1]["current"] == 13, f"stuck on a duplicate-only run: {second}"
    # `kept` is how "read 13, kept 0" stays tellable from "read 0".
    assert second[-1]["kept"] == 0


def test_the_two_phases_are_distinguishable(conn: db.Conn, tmp_path: Path) -> None:
    """Both counters ride one `total`/`current` pair, and the denominator changes
    meaning halfway through -- 4 sources, then 8 documents. Without `phase` a
    client cannot tell a reset from a jump."""
    events = _run(conn, tmp_path, sources=4, items=2)

    assert {e.get("phase") for e in events} == {"sources", "documents"}
    assert _phase(events, "sources")[-1]["total"] == 4
    assert _phase(events, "documents")[-1]["total"] == 8
