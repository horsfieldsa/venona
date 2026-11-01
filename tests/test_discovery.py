"""The discovery adapter and the strike semantics that keep it alive.

Discovery is the source that goes looking rather than being told, and it is the
only source that spends model calls. Both of those change what a "failure"
means, which is what most of this file is about.

Everything here is offline: a stub executor, no network, no spend.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from venona.db import core as db
from venona.llm.executor import LlmResult, PermissionDenied, Usage
from venona.pipeline import fetch
from venona.sources import registry
from venona.sources.base import SourceSpec
from venona.sources.discovery import collect_discovery
from venona.sources.registry import SourceConfigError

REPO_ROOT = Path(__file__).resolve().parents[1]

SPEC = SourceSpec(
    id="discovery",
    name="Open-ended discovery",
    kind="discovery",
    tags=["ngc2", "titan", "diu", "competitors", "acquisition"],
    max_items=12,
)


@dataclass
class StubExecutor:
    """Returns whatever it is handed. No subprocess, no `claude` binary."""

    data: dict[str, Any] | None = None
    error: str | None = None
    raises: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def call(self, task: str, **kwargs) -> LlmResult:
        self.calls.append({"task": task, **kwargs})
        if self.raises:
            raise self.raises
        return LlmResult(data=self.data, usage=Usage(), error=self.error, cost_usd=0.02)


@dataclass
class StubCounters:
    cost_usd: float = 0.0


class StubCtx:
    """The slice of `RunContext` discovery actually touches."""

    def __init__(self, conn: db.Conn, llm, settings, *, seed: int = 0) -> None:
        self.db = conn
        self.llm = llm
        self.settings = settings
        self.rng = random.Random(seed)
        self.run_id = "run:test"
        self.ids = type("I", (), {"uuid7": staticmethod(lambda: "call:test")})()
        self.clock = type("C", (), {"now": staticmethod(lambda: "2026-08-05T00:00:00.000Z")})()
        self.counters = StubCounters()

    async def counter(self, *args, **kwargs) -> None:
        return None


def _ctx(conn, settings, **kw):
    return StubCtx(conn, StubExecutor(**kw), settings)


# ── the spec (C9) ────────────────────────────────────────────────────


def test_discovery_needs_no_url() -> None:
    """It searches; there is no endpoint to declare."""
    assert SPEC.url is None


@pytest.mark.negative
def test_an_rss_source_without_a_url_fails_at_load() -> None:
    """A missing url must be a startup failure, not a request to `None`
    halfway through a run."""
    with pytest.raises(ValueError, match="needs a url"):
        SourceSpec(id="broken", name="Broken", kind="rss")


@pytest.mark.negative
def test_the_scraper_kind_is_gone(tmp_path: Path) -> None:
    """Every site that motivated it is a JS-rendered SPA serving no content
    links. Advertising a kind the code cannot serve reads as "this will work"
    and fails at fetch time instead of at startup."""
    (tmp_path / "sources.yaml").write_text(
        "sources:\n  - {id: x, name: X, kind: scraper, url: 'https://e.com'}\n"
    )
    with pytest.raises(SourceConfigError):
        registry.load_sources(tmp_path)


def test_the_shipped_config_declares_a_discovery_source() -> None:
    """The day-one requirement was research, not just feed-reading."""
    specs = registry.load_sources(REPO_ROOT / "config")
    discovery = [s for s in specs if s.kind == "discovery"]
    assert discovery, "config/sources.yaml declares no discovery source"
    assert discovery[0].tags, "a discovery source with no tags has no themes to rotate"


# ── theme rotation ───────────────────────────────────────────────────


def test_a_focused_sweep_uses_every_one_of_its_targets(conn: db.Conn) -> None:
    """A source that IS one sweep area must search all of its named programs.

    Sampling 3 of N is right for a grab-bag source and wrong here: dropping two
    thirds of `xluuv, orca, lusv, musv, ghost_shark...` is simply a worse sweep,
    and it is why undersea had zero coverage.
    """
    from venona.sources.discovery import _themes

    assert _themes(SPEC, random.Random(0)) == sorted(SPEC.tags)


def test_a_grab_bag_source_still_samples_deterministically(conn: db.Conn) -> None:
    """Above the focused threshold, themes are sampled off `ctx.rng` — seeded
    offline, so the LLM cache key stays stable. Drawing from the date or an
    unseeded rng would rekey every fixture."""
    from venona.sources.discovery import FOCUSED_SWEEP_MAX, THEMES_PER_SWEEP, _themes

    wide = SPEC.model_copy(update={"tags": [f"theme_{i}" for i in range(FOCUSED_SWEEP_MAX + 5)]})

    a = _themes(wide, random.Random(0))
    b = _themes(wide, random.Random(0))
    c = _themes(wide, random.Random(7))

    assert a == b, "same seed must give the same themes"
    assert len(a) == THEMES_PER_SWEEP
    assert a != c, "different seeds should actually rotate"


def test_the_prompt_carries_no_timestamp_offline(conn: db.Conn) -> None:
    """OFFLINE the cache key must stay stable, so no date may appear.

    This is the narrow form of a rule that used to apply always. It still
    applies where it matters — a date offline would rekey every fixture at
    midnight and no recorded sweep would ever replay.
    """
    import asyncio

    from venona.settings import get_settings

    settings = get_settings().model_copy(update={"offline": True})
    ctx = _ctx(conn, settings, data={"theme": "ngc2", "candidates": []})
    asyncio.run(collect_discovery(SPEC, ctx))

    (call,) = ctx.llm.calls
    assert "2026" not in call["user_content"]


def test_the_prompt_carries_a_date_when_live(conn: db.Conn) -> None:
    """LIVE the date is included on purpose.

    Search engines rank stale pages highly without one — the first live sweep
    returned a 2023 article. A recorded discovery sweep is worthless on replay
    anyway, because the whole value of the stage is freshness, so the cache-key
    argument that governs offline does not bind here. See docs/adr/.
    """
    import asyncio

    from venona.settings import get_settings

    settings = get_settings().model_copy(update={"offline": False})
    ctx = _ctx(conn, settings, data={"theme": "ngc2", "candidates": []})
    asyncio.run(collect_discovery(SPEC, ctx))

    (call,) = ctx.llm.calls
    assert "2026-08-05" in call["user_content"], "the clock date must anchor the search"
    # The bound is quoted FROM CONFIG, not written into the prompt as a literal.
    # It used to read "prefer the last 24-72 hours" while `run_fetch` enforced
    # nothing, and a sweep duly returned a ten-month-old press release. The two
    # numbers disagreeing is worse than either being wrong, because the model is
    # then asked for one window and judged against another.
    assert f"{settings.pipeline.discovery_max_age_hours} hours" in call["user_content"]
    assert "discarded" in call["user_content"], "say what happens to a stale result"


def test_the_model_is_a_per_source_decision(conn: db.Conn) -> None:
    """Depth costs money and which areas deserve opus is a judgement about the
    reader's thesis, not about code."""
    import asyncio

    from venona.settings import get_settings

    settings = get_settings()
    focused = SPEC.model_copy(update={"model": "opus"})
    ctx = _ctx(conn, settings, data={"theme": "ngc2", "candidates": []})
    asyncio.run(collect_discovery(focused, ctx))
    assert ctx.llm.calls[0]["model"] == "opus"

    ctx2 = _ctx(conn, settings, data={"theme": "ngc2", "candidates": []})
    asyncio.run(collect_discovery(SPEC.model_copy(update={"model": None}), ctx2))
    assert ctx2.llm.calls[0]["model"] == settings.llm.discover_model


# ── what it returns ──────────────────────────────────────────────────


def test_candidates_become_raw_items(conn: db.Conn) -> None:
    import asyncio

    from venona.settings import get_settings

    ctx = _ctx(
        conn,
        get_settings(),
        data={
            "theme": "ngc2",
            "candidates": [
                {"url": "https://diu.mil/x", "title": "A solicitation", "why": "edge"},
                {"url": "https://diu.mil/x", "title": "Duplicate", "why": "edge"},
                {"url": "not-a-url", "title": "Bad", "why": "edge"},
                {"url": "https://army.mil/y", "title": "", "why": "edge"},
            ],
        },
    )
    result = asyncio.run(collect_discovery(SPEC, ctx))

    assert result.ok
    assert [i.url for i in result.items] == ["https://diu.mil/x"]


@pytest.mark.negative
def test_a_model_supplied_date_is_never_stored(conn: db.Conn) -> None:
    """`published_hint` is advisory. Feeds already lie about dates; a model's
    guess is worse, and every recency ranking downstream depends on this."""
    import asyncio

    from venona.settings import get_settings

    ctx = _ctx(
        conn,
        get_settings(),
        data={
            "theme": "ngc2",
            "candidates": [
                {
                    "url": "https://diu.mil/x",
                    "title": "A solicitation",
                    "why": "edge",
                    "published_hint": "2026-08-01",
                }
            ],
        },
    )
    (item,) = asyncio.run(collect_discovery(SPEC, ctx)).items

    assert item.published_at is None
    assert item.date_confidence == "unknown"
    # Kept for inspection, but out of the field that ranking reads.
    assert item.extra["published_hint"] == "2026-08-01"


# ── strike semantics (A4) ────────────────────────────────────────────


def test_a_model_failure_is_not_a_source_failure(conn: db.Conn) -> None:
    """`auto_disabled` is set by `record_failure` and reset by nothing but a
    success — and an auto-disabled source is excluded from the run that would
    produce one. Three offline runs with a missing fixture would have closed
    that door permanently."""
    import asyncio

    from venona.settings import get_settings

    ctx = _ctx(conn, get_settings(), error="no fixture for cache key abc123")
    result = asyncio.run(collect_discovery(SPEC, ctx))

    assert result.ok is True, "a failed model call must not be reported as a dead source"
    assert result.error and "discovery call failed" in result.error
    assert result.items == []


def test_zero_candidates_is_success(conn: db.Conn) -> None:
    """A quiet day is not a broken source. This is where discovery differs from
    RSS on purpose: an empty feed parse usually means the feed moved."""
    import asyncio

    from venona.settings import get_settings

    ctx = _ctx(conn, get_settings(), data={"theme": "ngc2", "candidates": []})
    result = asyncio.run(collect_discovery(SPEC, ctx))

    assert result.ok is True
    assert result.error is None
    assert result.items == []


@pytest.mark.negative
def test_an_ok_result_carrying_an_error_takes_no_strike(conn: db.Conn) -> None:
    """The fetch loop's half of the same contract."""
    import inspect

    source = inspect.getsource(fetch.run_fetch)
    ok_with_error = source.index("if result.ok and result.error:")
    strike = source.index("if not result.ok:")

    assert ok_with_error < strike, "the no-strike branch must be reached first"


@pytest.mark.negative
def test_a_denied_tool_is_re_raised_rather_than_becoming_a_strike() -> None:
    """`asyncio.gather(return_exceptions=True)` is what makes one dead feed
    survivable — and would otherwise turn contract C2's hard failure into a
    warning string plus a strike."""
    import inspect

    source = inspect.getsource(fetch.run_fetch)
    assert "PermissionDenied" in source
    guard = source.index("isinstance(result, PermissionDenied")
    record = source.index("registry.record_failure")
    assert guard < record


def test_the_strike_counter_can_be_cleared(conn: db.Conn) -> None:
    """`venona sources --enable` is the way out. Without it, recovery means
    hand-editing SQLite."""
    specs = [SPEC]
    registry.sync_sources(conn, specs, now="2026-08-05T00:00:00.000Z")
    conn.execute(
        "UPDATE source_health SET consecutive_failures = 3, auto_disabled = 1 "
        "WHERE source_id = 'discovery'"
    )
    assert registry.active_specs(conn, specs) == []

    conn.execute(
        "UPDATE source_health SET auto_disabled = 0, consecutive_failures = 0 "
        "WHERE source_id = 'discovery'"
    )
    assert registry.active_specs(conn, specs) == [SPEC]


@pytest.mark.negative
def test_permission_denied_propagates_out_of_the_adapter(conn: db.Conn) -> None:
    import asyncio

    from venona.settings import get_settings

    ctx = _ctx(conn, get_settings(), raises=PermissionDenied("claude denied tools [WebSearch]"))
    with pytest.raises(PermissionDenied):
        asyncio.run(collect_discovery(SPEC, ctx))
