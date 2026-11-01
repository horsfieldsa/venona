"""Discovery adapter: the source that goes looking, rather than being told.

RSS answers "what did these nine outlets publish". Discovery answers "what
happened that they did not carry" -- a DIU solicitation, a protest docket, a
vendor announcement, a service memo. It was the second half of the day-one
requirement and only RSS shipped, so it is the one real functional gap.

Three properties make it safe to bolt onto the existing fetch path:

* **It returns URLs, never content.** WebSearch/WebFetch give the model a
  sub-model summary, not page text. Anything it asserted about an article would
  be a summary of a summary, ungroundable by construction (invariant 3). The
  ordinary fetch path fetches, extracts and normalises each candidate, so a
  discovered URL is indistinguishable downstream from an RSS one -- including
  dedup, so a story that also arrived via RSS becomes a sighting.

* **Themes rotate off `ctx.rng`,** which is seeded offline. Rotating off the
  date instead would rekey the LLM cache every single day and make the recorded
  fixtures unreplayable within 24 hours.

* **`user_content` carries no timestamp.** Same reason: the cache key hashes it.

Failure semantics differ from RSS deliberately -- see `run_fetch`. Zero
candidates is `ok=True`: a quiet day is not a broken source, whereas an RSS feed
that parses to zero items has usually moved.
"""

from __future__ import annotations

from venona.llm.prompts import discovery_system_prompt
from venona.llm.runner import call_and_record
from venona.llm.schemas import discovery_schema
from venona.sources.base import RawItem, SourceResult, SourceSpec

# Above this many tags, a source is a grab-bag rather than a focused area and
# its themes are sampled so one sweep does not spread its tool budget across
# everything. At or below it, ALL tags are used: when a source IS one sweep
# area, dropping two thirds of its named programs is just a worse sweep.
FOCUSED_SWEEP_MAX = 10
THEMES_PER_SWEEP = 3


def _themes(spec: SourceSpec, rng) -> list[str]:
    """The search targets for this sweep.

    Declared in `config/sources.yaml` as `tags:`, so retargeting a sweep is a
    config change -- contract C9's whole point.
    """
    pool = [t for t in spec.tags if t not in {"discovery", "government", "trade_press"}]
    if not pool:
        return []
    if len(pool) <= FOCUSED_SWEEP_MAX:
        return sorted(pool)
    return sorted(rng.sample(pool, THEMES_PER_SWEEP))


async def collect_discovery(
    spec: SourceSpec, ctx, *, budget=None, prompt: str | None = None
) -> SourceResult:
    """One open-ended sweep. Returns candidate URLs as `RawItem`s.

    Never raises for a model-level failure: `run_fetch` distinguishes "this
    source is broken" (a strike) from "the model call failed" (a warning), and
    the difference is carried in `SourceResult.error` with `ok` still True.

    `prompt` replaces the themed question with a caller-supplied one, which is
    how `pipeline/research.py` asks "what else has been written about
    Fincantieri?" instead of "what happened in undersea autonomy?". Everything
    after the call -- URL validation, dedup, `RawItem` shape, the
    model-failure-is-not-a-source-failure rule -- is identical, and duplicating
    it for a second caller is how the two quietly diverge.
    """
    themes = _themes(spec, ctx.rng)
    focus = (
        f"Focus this sweep on: {', '.join(themes)}.\n\n"
        if themes
        else "Choose two or three standing themes to sweep.\n\n"
    )

    # Search engines rank stale results highly without a date to anchor on, and
    # our first live sweep duly returned a 2023 article. Offline this is omitted
    # so the cache key stays stable; live it is included, because a RECORDED
    # discovery sweep is worthless on replay anyway -- the entire value of the
    # stage is freshness. See docs/adr/.
    #
    # State the bound as a BOUND, and say what happens to a result that misses
    # it. "Prefer the last 24-72 hours" does not hold -- a sweep returned a PR
    # Newswire release from ten months earlier -- because the model is being
    # asked to spend its searches well, not to be polite. The
    # enforcement is `run_fetch`'s `discovery_stale` drop, which needs no
    # cooperation; this only stops us paying to fetch what will be dropped.
    max_age_h = ctx.settings.pipeline.discovery_max_age_hours
    dateline = (
        ""
        if ctx.settings.offline
        else (
            f"Today is {ctx.clock.now()[:10]}. Include the date in your searches so "
            f"results are current. ONLY return pages published in the last {max_age_h} "
            "hours -- anything older is discarded before it is read, so it is a wasted "
            "slot. Returning three recent items beats returning eight where five are "
            "older than that. If a theme has nothing that recent, return nothing for "
            "it; a quiet sweep is a real answer.\n\n"
        )
    )

    result = await call_and_record(
        ctx,
        "discovery",
        user_content=prompt
        if prompt is not None
        else (
            f"{dateline}{focus}"
            "Find specific, attributable developments -- an award, a solicitation, a "
            "protest, an architecture decision, a fielding milestone. Return the URL "
            "of each source page. Do not summarise; the pipeline reads each page."
        ),
        schema=discovery_schema(),
        system_prompt=discovery_system_prompt(),
        model=spec.model or ctx.settings.llm.discover_model,
        effort=ctx.settings.llm.discover_effort,
        # dontAsk DENIES these while still exiting 0 with is_error: false.
        # `harness/claude_code.py` passes --allowedTools and hard-fails on
        # permission_denials.
        tools=("WebSearch", "WebFetch"),
        # No literal: `timeout_for` resolves this task's ceiling, so the
        # instance's `llm.task_timeout_seconds` reaches it. A literal here wins
        # over that setting, which is how the one search task whose latency
        # runs to five minutes was pinned to a ceiling nobody could raise.
        budget=budget,
        # THE ONE THING THIS CALLER CAN SAY that most cannot: the branch below
        # distinguishes "the search did not happen" from "the search found
        # nothing", and reports the source as fine either way. So a provider's
        # bad minute costs this sweep one search out of a hundred and fifty
        # rather than the whole run.
        degrade_when_spent=True,
    )

    if not result.ok:
        # A model failure is not evidence the SOURCE is dead. Reporting it as a
        # source failure would walk discovery toward `auto_disabled` after three
        # offline runs with a missing fixture -- a one-way door, since nothing
        # resets the counter but a success.
        return SourceResult(
            source_id=spec.id,
            ok=True,
            error=f"discovery call failed: {result.error or 'no data'}",
        )

    data = result.data or {}
    items: list[RawItem] = []
    seen: set[str] = set()

    for candidate in data.get("candidates", [])[: spec.max_items]:
        url = (candidate.get("url") or "").strip()
        title = (candidate.get("title") or "").strip()
        if not url or not title or not url.startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)

        items.append(
            RawItem(
                source_id=spec.id,
                url=url,
                title=title,
                publisher=(candidate.get("publisher") or "").strip() or spec.publisher,
                # True only when the value came from the MODEL. `spec.publisher`
                # is configured and keeps the top rung; a sweep has no publisher
                # of its own, so in practice that is `None` and this is True
                # whenever the field is filled at all.
                publisher_asserted=bool((candidate.get("publisher") or "").strip()),
                # NOT `published_hint`. A model's date guess is worse than the
                # feed dates we already refuse to trust; `build_document`
                # derives the real one from the fetched page.
                published_at=None,
                date_confidence="unknown",
                summary=None,
                extra={
                    "why": candidate.get("why"),
                    "theme": data.get("theme"),
                    "published_hint": candidate.get("published_hint"),
                },
            )
        )

    return SourceResult(source_id=spec.id, items=items, ok=True)
