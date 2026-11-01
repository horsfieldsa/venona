"""Brief generation: scored clusters become written prose with live annotations.

The division of labour is invariant 2, and it is the whole reason this works:
the model writes prose and names WHICH entities it discussed; code decides WHERE
those names fall in the text. Offsets are never requested, so they are never
wrong.

`mentioned_entity_ids` is a hint channel. It is validated against the retrieval
set and a hallucinated id is silently dropped, costing nothing -- while a
correct one raises that entity to annotation tier A, where its weaker aliases
become matchable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from venona import timeutil
from venona.db import core as db
from venona.db.repositories import pipeline_brief as repo
from venona.llm import budget as budget_mod
from venona.llm.executor import LlmError, PermissionDenied
from venona.llm.prompts import brief_system_prompt, load_profile
from venona.llm.runner import call_and_record
from venona.llm.schemas import brief_schema, load_brief_sections, section_names
from venona.pipeline.metrics import Funnel
from venona.pipeline.types import (
    MIN_BRIEFABLE_CHARS,
    RunContext,
    retrieval_window,
)
from venona.services import alias_index
from venona.services.alias_index import build_index
from venona.services.annotate import annotate_blocks, markdown_to_blocks
from venona.settings import Settings, get_settings

# Retrieval SIZE limits live in `config/settings.yaml`
# (`pipeline.brief_top_clusters`, `brief_max_entities`, `lookback_hours`) so
# they are tunable without a code change. Do not shadow them with module
# constants here: editing the config would silently do nothing.
# A day with nothing above `background` is a real outcome, not a failure, so the
# brief carries the best of what there is and is told the day was quiet -- ADR
# 0023. An empty brief would read as a fault instead.
QUIET_DAY_FLOOR = 4
QUIET_DAY_FILL = 6
NOISE_FLOOR = 5
# `MIN_BRIEFABLE_CHARS` lives in `pipeline.types` because analyze applies the
# same bound, and a document this stage will refuse is one analyze must not pay
# to score (ADR 0024). Re-exported here because tests and `services/` import it
# from this module. See there for the measurement.


@dataclass
class BriefReport:
    brief_id: str | None = None
    clusters_used: int = 0
    quiet_day: bool = False
    items: int = 0
    sections: int = 0
    blocks: int = 0
    spans: int = 0
    entities_linked: int = 0
    unresolved: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    #: The run tried to write a brief and could not. A DEGRADATION.
    error: str | None = None
    #: There was nothing to write about. NOT a degradation, and separate from
    #: `error` for exactly that reason: one field carrying both made the
    #: orchestrator warn on every value of it, so a quiet night reported
    #: `degraded` against `docs/RUNBOOK.md`'s own `succeeded` + `nothing_to_do`.
    #: ADR 0183. Distinct from `quiet_day`, which means "few strong clusters,
    #: brief still written".
    empty_reason: str | None = None


def _cutoff_ms(now_iso: str, lookback_hours: int) -> str:
    """`timeutil.cutoff_seconds` at millisecond precision, for comparing against
    `generated_at`.

    That helper emits whole seconds, but the clock writes milliseconds. These
    columns are compared as TEXT, and `'…00.500Z' < '…00Z'` because `.` (0x2E)
    sorts below `Z` (0x5A) -- so a second-precision bound silently drops
    anything generated inside its own boundary second. Only `published_at`
    (whole seconds) may use the seconds helper; `generated_at` must use this.
    """
    moment = datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    stamp = moment - timedelta(hours=lookback_hours)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class Retrieval:
    clusters: list[db.Row]
    entity_ids: list[str]
    quiet_day: bool
    #: How many clusters passed retrieval BEFORE the ranking cut. `clusters` is
    #: what survived both; the difference is what was outranked rather than
    #: filtered, and telling those apart is the difference between "the window
    #: is too narrow" and "the feeds are too old".
    eligible: int = 0
    #: The window this retrieval actually ran with, carried rather than
    #: recomputed. `_emit_metrics` runs after `_persist`, and `retrieval_window`
    #: reads `COUNT(*) FROM briefs` to decide whether this is a first run -- so
    #: re-resolving it there would answer "not a first run" for the very brief
    #: that was one, and measure the funnel against the wrong cutoff.
    cutoff: str = ""
    window_hours: int = 0


def build_retrieval(
    conn: db.Conn,
    *,
    now: str,
    settings: Settings | None = None,
    rewrite_of: str | None = None,
) -> Retrieval:
    """Choose what the brief is allowed to be about.

    Frozen because `mentioned_entity_ids` is validated against exactly this set:
    if the retrieval rule wobbles, the model's hints stop matching and
    annotation quality silently degrades.

    TWO filters make this a *daily* brief rather than a greatest-hits list, and
    both are needed (ADR 0035): **recency**, or yesterday's best story outranks
    today's news forever, and **not already briefed**, or a same-day re-run
    repeats every item. Exclusion alone reaches back weeks for unbriefed filler
    after a quiet stretch.

    `rewrite_of` is the ONE exemption, named rather than implied: it releases
    the clusters of that SINGLE brief, so `POST /briefs/rewrite` can re-brief a
    window a brief has already claimed. The clause is `bc.brief_id <> ?` inside
    the `NOT EXISTS`, so a cluster any OTHER brief featured stays excluded --
    including one featured by both -- and `None` exempts nothing, which is every
    path except a rewrite. The scope is the load-bearing part: a BLANKET
    self-exclusion makes the `NOT EXISTS` vacuously true, which is how six
    briefs in one day came to serve the same top stories. ADR 0121.

    A rewrite ADDS a brief; it does not replace one. Ids are unique per
    generation and same-day briefs are already separate archive rows (C3), so
    the brief being rewritten stays in the archive and nothing is deleted.
    `venona briefs delete` then re-run is the way to genuinely release a
    brief's clusters to every future run.

    Undated clusters are included: a missing `published_at` is an honest
    unknown (see `normalize.resolve_date`), and dropping them would silently
    lose real coverage from feeds with bad dates.

    The window comes from `retrieval_window`, which `analyze.pending_clusters`
    also uses -- so a cluster this stage will refuse for being stale is one
    analyze never paid to score. It also widens on a FIRST brief; see there.
    """
    settings = settings or get_settings()
    limits = settings.pipeline
    cutoff, window_hours = retrieval_window(conn, now_iso=now, settings=settings)

    ranked = repo.select_clusters(
        conn, MIN_BRIEFABLE_CHARS, cutoff, rewrite_of, rewrite_of
    ).fetchall()

    strong = [r for r in ranked if r["tier"] != "background"][: limits.brief_top_clusters]
    quiet = len(strong) < QUIET_DAY_FLOOR

    if quiet:
        # Top up with the best of the rest, above pure noise.
        filler = [
            r
            for r in ranked
            if r["tier"] == "background" and (r["relevance_overall"] or 0) >= NOISE_FLOOR
        ]
        seen = {r["id"] for r in strong}
        for row in filler:
            if len(strong) >= QUIET_DAY_FILL:
                break
            if row["id"] not in seen:
                strong.append(row)

    entity_ids: list[str] = []
    if strong:
        rows = repo.active_entities_in_clusters(conn, [r["id"] for r in strong])
        entity_ids = [r["id"] for r in rows]

    # Top up with the highest-relevance active entities so the model can refer
    # to well-known programs even when today's articles did not name them.
    max_entities = limits.brief_max_entities
    if len(entity_ids) < max_entities:
        existing = set(entity_ids)
        for row in repo.select_entities(conn, max_entities):
            if row["id"] not in existing:
                entity_ids.append(row["id"])
                existing.add(row["id"])
            if len(entity_ids) >= max_entities:
                break

    return Retrieval(
        clusters=strong,
        entity_ids=entity_ids[:max_entities],
        quiet_day=quiet,
        eligible=len(ranked),
        cutoff=cutoff,
        window_hours=window_hours,
    )


def _empty_retrieval_reason(conn: db.Conn, *, now: str, settings: Settings | None = None) -> str:
    """Why there was nothing to write about, in terms that suggest what to do.

    `build_retrieval` without the already-briefed filter, asked four ways
    because four different situations produce an empty brief and only one of
    them is a problem the operator can act on:

      * briefable material in the window that nothing has analysed -- the ONLY
        case where `analyze-run` is the answer
      * material that cleared every bar, so a brief already covers it, which is
        the ORDINARY outcome of a second run in a day
      * material in the window, none of it long enough -- a thin day of
        headline stubs, and nothing to be done about it
      * nothing in the window -- a quiet day

    Every count is scoped to the WINDOW. "Has anything ever been analysed" is
    the wrong question: a corpus full of last month's analysed material says
    nothing about whether there is work to do today.

    Measured on production, 2026-08-30: two runs reported "nothing analyzed yet"
    while the window held exactly two stories, of 592 and 558 characters against
    a 600-character floor. Analyze had run and had correctly declined to pay to
    score something the brief would refuse. The pipeline was working perfectly
    and the message named the one command that could not help, which is the
    failure the four-way split was already written to prevent.

    The distinction is what matters and is what the tests pin. None of the four
    names a command any more: these render on a run page, and a reader is not
    at a shell.
    """
    settings = settings or get_settings()
    cutoff, hours = retrieval_window(conn, now_iso=now, settings=settings)
    row = repo.select_window_shape(
        conn, cutoff, MIN_BRIEFABLE_CHARS, cutoff, MIN_BRIEFABLE_CHARS, cutoff
    ).fetchone()

    if row["awaiting_analysis"]:
        return "some articles from this window have not been read yet"
    if row["briefable"]:
        return "nothing new since the last brief"
    if row["in_window"]:
        return f"nothing long enough to write about in the last {hours} hours"
    return f"nothing published in the last {hours} hours"


RECENT_THREAD_DAYS = 7
RECENT_THREAD_LIMIT = 25


def _recent_threads(conn: db.Conn, *, now: str) -> list[str]:
    """What the reader has already been told, so this brief can say what CHANGED.

    Without this every item is written as though it were the first time, and a
    brief that re-explains a running thread from the start every morning makes
    the reader do the diffing. `follow_up` and its `state_note` are only
    answerable against this list.

    Keyed on `generated_at`, not `date`. A `b.date < ?` bound means strictly
    EARLIER DATES, so a second brief in a day could not see the first and would
    re-explain a thread the reader was told about hours ago. There is no upper
    bound because this runs before `_persist`, so every row in `briefs` is
    already a predecessor.

    Headlines only -- the model does not need the earlier bodies, and including
    them would push today's article text out of the useful part of the
    window.
    """
    rows = repo.select_brief_items(
        conn, _cutoff_ms(now, RECENT_THREAD_DAYS * 24), RECENT_THREAD_LIMIT
    ).fetchall()
    if not rows:
        return []

    lines = [
        "## Already briefed in the last week",
        "",
        "If an item below continues one of these, mark it `follow_up` and put "
        "what is NEW in `state_note`. Do not re-explain the thread.",
        "",
    ]
    # Time, not just date: several briefs can share a day, and "you said this at
    # 08:12 today" is a different instruction from "you said this on Tuesday".
    lines += [
        f"- {r['date']} {(r['generated_at'] or '')[11:16]} [{r['section']}] {r['headline']}"
        for r in rows
    ]
    lines.append("")
    return lines


def build_user_content(conn: db.Conn, retrieval: Retrieval, *, date: str, now: str) -> str:
    """The per-run half of the prompt: today's material, and the entity list the
    model may refer to."""
    lines = [f"DATE: {date}", ""]

    if retrieval.quiet_day:
        # Two messages, because `quiet_day` fires on `len(strong) <
        # QUIET_DAY_FLOOR` and NOT on "nothing cleared background". Told the
        # second when the first was true, the model wrote what it was told and
        # titled a brief with a `must_read` in it "A thin day": a prompt that
        # misdescribes the material produces a brief that misdescribes the day.
        # ADR 0109.
        strong_count = sum(1 for r in retrieval.clusters if r["tier"] != "background")
        if strong_count:
            lines += [
                f"NOTE: only {strong_count} item(s) today cleared the 'worth knowing' bar "
                "for this reader; the rest are background.",
                "Lead with those and keep the brief short. That is a real outcome.",
                "Do NOT inflate the significance of the background items to fill space.",
                "",
            ]
        else:
            lines += [
                "NOTE: nothing today scored above the 'background' tier for this reader.",
                "That is a real outcome. Say so plainly and keep the brief short.",
                "Do NOT inflate the significance of these items to fill space.",
                "",
            ]

    lines += _recent_threads(conn, now=now)

    lines.append("## Today's items, highest relevance first")
    lines.append("")
    lines.append(
        "Write ONE item per entry below and return its `cluster_id` verbatim. "
        "Do not merge two entries, do not invent one, and do not restate the "
        "publisher or date -- those are attached from the record."
    )
    lines.append("")

    for index, row in enumerate(retrieval.clusters, start=1):
        lines.append(f"### [{index}] {row['doc_title'] or row['title']}")
        lines.append(f"cluster_id: {row['id']}")
        lines.append(
            f"score {row['relevance_overall']}/100 ({row['tier']}) · "
            f"{row['publisher'] or '?'} · {row['doc_published'] or '?'}"
        )
        if row["relevance_note"]:
            lines.append(f"why it matters: {row['relevance_note']}")
        if row["so_what"]:
            lines.append(f"so what: {row['so_what']}")

        # `analyze` already read the whole article and pulled the specifics out;
        # making the brief model re-derive "$99.6M" from 4000 truncated
        # characters is paying twice for the same work and getting the worse
        # answer the second time.
        key_facts = db.jload(row["key_facts"], []) or []
        if key_facts:
            lines.append("key facts:")
            lines += [f"  - {fact}" for fact in key_facts]

        # Character, not content. `analyze` judged this per article against the
        # research prompt's "Do not return" list; passing the judgement forward
        # lets the brief attribute rather than assert. Rendered as a caveat
        # rather than filtered in `build_retrieval` on purpose -- a press release
        # announcing a real award is still news, and silently dropping every
        # flagged cluster is how a quiet day becomes an empty one.
        flags = db.jload(row["flags"], {}) or {}
        raised = sorted(name for name, value in flags.items() if value)
        if raised:
            lines.append(f"caveat: {', '.join(raised)} -- attribute, do not assert")

        facts = repo.select_assertions_by_cluster_id(conn, row["id"]).fetchall()
        if facts:
            lines.append("supporting statements:")
            lines += [f"  - {f['evidence_quote'][:280]}" for f in facts]

        doc = repo.select_documents_by_id(conn, row["canonical_doc_id"]).fetchone()
        if doc:
            lines.append(f"article text:\n{doc['text_nfc'][:4000]}")
        lines.append("")

    lines += ["## Sections you may file under", ""]
    for section in load_brief_sections():
        covers = f" -- {section['covers']}" if section["covers"] else ""
        lines.append(f"- {section['name']}{covers}")
    lines.append("")

    lines += ["## Entities you may refer to", ""]
    if retrieval.entity_ids:
        for row in repo.entities_for_prompt(conn, list(retrieval.entity_ids)):
            lines.append(f"- {row['id']} — {row['name']} ({row['type']})")
    lines += [
        "",
        "Use these EXACT names in your prose; the annotator matches literal text,",
        "so a paraphrased name will not link. Put the ids you actually discussed",
        "in `mentioned_entity_ids`. Anything you write about that is NOT in this",
        "list goes in `unresolved_mentions`.",
    ]

    return "\n".join(lines)


def _emit_metrics(
    ctx: RunContext,
    *,
    retrieval: Retrieval,
    report: BriefReport,
    now: str,
    rewrite_of: str | None = None,
) -> None:
    """Called on EVERY exit from `run_brief`, including the failures.

    A brief that produced nothing is precisely the run whose retrieval numbers
    someone wants -- "nothing new since the last brief" and "everything was
    outside the window" look identical from the outside and have opposite fixes.
    """
    limits = ctx.settings.pipeline
    funnel = _retrieval_funnel(
        ctx.db,
        cutoff=retrieval.cutoff or timeutil.cutoff_seconds(now, limits.lookback_hours),
        kept=retrieval.eligible,
        used=len(retrieval.clusters),
        brief_id=report.brief_id,
        rewrite_of=rewrite_of,
    )
    funnel.emit(ctx)
    ctx.metric("brief", "lookback_hours", retrieval.window_hours or limits.lookback_hours, unit="h")
    ctx.metric("brief", "top_clusters_cap", limits.brief_top_clusters)
    ctx.metric("brief", "quiet_day", 1.0 if report.quiet_day else 0.0, unit="bool")
    ctx.metric("brief", "entities_offered", len(retrieval.entity_ids))
    ctx.metric("brief", "blocks", report.blocks)
    ctx.metric("brief", "spans", report.spans)


def _retrieval_funnel(
    conn: db.Conn,
    *,
    cutoff: str,
    kept: int,
    used: int,
    brief_id: str | None = None,
    rewrite_of: str | None = None,
) -> Funnel:
    """Where the analysed corpus went before the brief ever saw it.

    THIS IS THE EXPENSIVE ONE TO NOT KNOW. `build_retrieval` applies four
    filters in one `WHERE`, so a cluster that cost a paid analyze call and then
    failed one of them is indistinguishable from a cluster that was never
    fetched. Measured 2026-08-16: 203 of 216 documents were outside
    `lookback_hours` -- 94% of the analyze spend could not have reached a brief
    under any ranking. ADR 0105.

    The clauses are applied CUMULATIVELY, in the order the real query would
    reject them, so each count is "how many died HERE" rather than "how many
    match this predicate" -- otherwise a stale AND already-briefed cluster is
    counted twice and the funnel does not reconcile.

    `brief_id` excludes the brief being written, and that exclusion is load-
    bearing rather than defensive. Retrieval runs BEFORE `_persist` and so
    cannot see its own rows; the funnel runs AFTER it, because `report.blocks`
    and `report.spans` do not exist until then. Without the exclusion every kept
    cluster is counted twice -- once as `kept`, once as an `already_briefed`
    drop -- which on 2026-08-16 read 5 + 105 + 2 + 5 = 117 against 112 offered
    on `run:06a8160f`, and surfaced as `reconciled = 0.0`.
    """
    stages: list[tuple[str, str, tuple]] = [
        ("unscored", "c.relevance_overall IS NULL", ()),
        ("too_short", "d.text_chars < ?", (MIN_BRIEFABLE_CHARS,)),
        ("outside_window", "c.published_at IS NOT NULL AND c.published_at < ?", (cutoff,)),
        (
            "already_briefed",
            # Two exemptions, and they are different claims. `brief_id` is the
            # brief being WRITTEN, which retrieval could not see and this can
            # (see the docstring). `rewrite_of` is the brief being REWRITTEN,
            # whose clusters retrieval deliberately released -- counting them as
            # dropped here would report a rewrite as having found nothing while
            # it was writing from them, and the funnel would not reconcile.
            "EXISTS (SELECT 1 FROM brief_clusters bc WHERE bc.cluster_id = c.id "
            "AND (?::text IS NULL OR bc.brief_id <> ?) AND (?::text IS NULL OR bc.brief_id <> ?))",
            (brief_id, brief_id, rewrite_of, rewrite_of),
        ),
    ]
    offered = repo.count_briefable(conn)
    funnel = Funnel("brief", offered=offered)

    survived: list[str] = []
    params: list = []
    for reason, clause, clause_params in stages:
        narrowed = [f"NOT ({c})" for c in survived] + [f"({clause})"]
        funnel.drop(
            reason,
            repo.count_briefable(conn, " AND ".join(narrowed), (*params, *clause_params)),
        )
        survived.append(clause)
        params.extend(clause_params)

    # What survived retrieval but lost the ranking cut (`brief_top_clusters`,
    # or the background tier on a non-quiet day).
    funnel.drop("outranked", max(0, kept - used))
    funnel.keep(used)
    return funnel


async def run_brief(
    ctx: RunContext,
    *,
    date: str | None = None,
    budget: budget_mod.Budget | None = None,
    rewrite_of: str | None = None,
) -> BriefReport:
    """`rewrite_of` names the brief this run is rewriting, and does exactly one
    thing: releases that brief's clusters back to retrieval. It ADDS a brief and
    deletes nothing -- see `build_retrieval` for why it is scoped to one id."""
    conn = ctx.db
    settings = ctx.settings
    report = BriefReport()

    now = ctx.clock.now()
    date = date or now[:10]
    retrieval = build_retrieval(conn, settings=settings, now=now, rewrite_of=rewrite_of)
    report.quiet_day = retrieval.quiet_day
    report.clusters_used = len(retrieval.clusters)

    if not retrieval.clusters:
        # An empty brief is usually not a fault, and which of the several
        # harmless reasons it is decides whether an operator has anything to do.
        # `_empty_retrieval_reason` owns that distinction; a single sentence
        # here is what sent someone to `analyze-run` while the pipeline was
        # working perfectly.
        report.empty_reason = _empty_retrieval_reason(conn, settings=settings, now=now)
        _emit_metrics(ctx, retrieval=retrieval, report=report, now=now, rewrite_of=rewrite_of)
        return report

    profile = load_profile(settings)
    # A failed brief is a MISSING BRIEF, not a failed run. Letting the error
    # unwind skips `curate` and the Neo4j projection that run after this stage,
    # so one unreadable envelope also costs a run its promotions -- measured
    # 2026-08-06 at 288 resolved entities unprojected. `BriefReport.error` is
    # the degradation path the orchestrator already knows how to report; this
    # keeps the exception on it. `PermissionDenied` still propagates -- a denied
    # tool is never a result (see `call_and_record`).
    try:
        result = await call_and_record(
            ctx,
            "brief",
            user_content=build_user_content(conn, retrieval, date=date, now=now),
            schema=brief_schema(),
            system_prompt=brief_system_prompt(profile),
            model=settings.llm.brief_model,
            effort=settings.llm.brief_effort,
            timeout=600,
            budget=budget,
            # The ONLY exempt call site. A run that gathered and scored all day
            # and then skipped the brief because it was one call over is the
            # worst degradation available -- the brief IS the product. Retries
            # are still bounded; see `call_and_record`.
            exempt=True,
        )
    except PermissionDenied:
        raise
    except (LlmError, budget_mod.BudgetExceeded) as exc:
        # `BudgetExceeded` for the same reason, and on its own instructions:
        # "callers must degrade to succeeded_partial, never to failed".
        report.error = f"{type(exc).__name__}: {exc}"
        _emit_metrics(ctx, retrieval=retrieval, report=report, now=now, rewrite_of=rewrite_of)
        return report

    report.cost_usd = result.cost_usd

    if not result.ok:
        report.error = result.error or "brief generation refused"
        _emit_metrics(ctx, retrieval=retrieval, report=report, now=now, rewrite_of=rewrite_of)
        return report

    report.brief_id = _persist(
        ctx, data=result.data or {}, retrieval=retrieval, date=date, report=report
    )
    # After `_persist`, deliberately: `report.blocks` and `report.spans` do not
    # exist until it runs. `_retrieval_funnel` excludes the brief being written,
    # which is what stops the funnel counting it twice.
    _emit_metrics(ctx, retrieval=retrieval, report=report, now=now, rewrite_of=rewrite_of)
    return report


def _persist(
    ctx: RunContext,
    *,
    data: dict,
    retrieval: Retrieval,
    date: str,
    report: BriefReport,
) -> str:
    """Write the brief, all of it or none of it.

    THE HALF-WRITTEN BRIEF IS PERMANENT, which is what makes this worth a
    transaction rather than a retry. `brief_clusters` is written before the items
    and blocks, and every later brief excludes a cluster that any brief already
    claimed (`already_briefed`, the `NOT EXISTS` filter above). So a process that
    dies between the two leaves a brief with no content that has nonetheless
    consumed its clusters -- and the stories in them are never offered to another
    brief again. Nothing reports it: the run is `failed`, which reads as "try
    again tomorrow", and tomorrow's brief is quietly missing a day of coverage.

    On an autocommit connection each of these statements commits on its own, so
    the atomicity has to be asked for. `_emit_metrics` stays OUTSIDE and after,
    where it reads a brief that is committed rather than one that may yet vanish.
    """
    with db.transaction(ctx.db, what="brief_persist"):
        return _write_brief(ctx, data=data, retrieval=retrieval, date=date, report=report)


def _write_brief(
    ctx: RunContext,
    *,
    data: dict,
    retrieval: Retrieval,
    date: str,
    report: BriefReport,
) -> str:
    conn = ctx.db
    now = ctx.clock.now()
    # Unique per GENERATION, not per day (C3). A date-only key made the primary
    # key a calendar collision: six runs in one day left one row, five briefs
    # destroyed, and five `runs.brief_id` values pointing at a later run's
    # content. ADR 0080.
    brief_id = f"brief:{date}:{ctx.ids.uuid7()}"

    allowed = set(retrieval.entity_ids)
    mentioned = [i for i in (data.get("mentioned_entity_ids") or []) if i in allowed]
    report.entities_linked = len(mentioned)
    report.unresolved = [str(m) for m in (data.get("unresolved_mentions") or [])][:20]

    source_ids = [r["canonical_doc_id"] for r in retrieval.clusters if r["canonical_doc_id"]]

    db.upsert(
        conn,
        "briefs",
        {
            "id": brief_id,
            "date": date,
            "run_id": ctx.run_id,
            "title": data.get("headline") or f"Brief for {date}",
            "dek": None,
            "generated_at": now,
            "model": ctx.settings.llm.brief_model,
            "unresolved_mentions": db.jdump(report.unresolved),
            "source_ids": db.jdump(source_ids),
            "created_at": now,
        },
        key=["id"],
    )
    # A fresh id normally has no children, so these three clear nothing. They
    # stay because `SeededIdGen` (offline) is a per-instance counter: two
    # `venona brief` PROCESSES under VENONA_OFFLINE=1 mint the same id, and
    # `brief_blocks.id`/`brief_items.id` are PRIMARY KEYs whose per-brief
    # suffixes restart at `b1`/`i01`. Without these deletes that path raises
    # IntegrityError from BELOW the `try` in `run_brief` -- unwinding a run that
    # has already paid for the Opus call, which is the exact failure the comment
    # above that `try` exists to prevent.
    repo.delete_brief_blocks_by_brief_id(conn, brief_id)
    repo.delete_brief_items_by_brief_id(conn, brief_id)

    # What this brief covered. Every LATER brief excludes these, which is half of
    # what makes this a daily brief rather than a greatest-hits list; releasing
    # them again is `venona briefs delete`, never a side effect of writing.
    repo.delete_brief_clusters_by_brief_id(conn, brief_id)
    for rank, row in enumerate(retrieval.clusters, start=1):
        db.insert(
            conn,
            "brief_clusters",
            {
                "brief_id": brief_id,
                "cluster_id": row["id"],
                "rank": rank,
                "created_at": now,
            },
        )

    index = build_index(conn)
    # What the model said this brief is about, else the retrieval set.
    named = frozenset(mentioned) or frozenset(retrieval.entity_ids)
    # Plus the single-source entities these clusters assert. The prompt list is
    # right to exclude them and the annotation tier was wrong to: they are
    # underlined as uncorroborated, not asserted as facts. See the repository.
    provisional = frozenset(
        r["id"]
        for r in repo.provisional_entities_in_clusters(conn, [c["id"] for c in retrieval.clusters])
    )
    tier_a = named | provisional

    # Only clusters we actually retrieved may become items. A `cluster_id` the
    # model invented or carried over from an earlier prompt would produce an
    # item with no evidence and no attribution -- silently, because every other
    # field would look fine.
    known = {row["id"] for row in retrieval.clusters}
    section_order = {name: i for i, name in enumerate(section_names())}

    items = [
        item
        for item in (data.get("items") or [])
        if isinstance(item, dict) and item.get("cluster_id") in known
    ]
    # Stable, editorial order: sections as declared in the profile, then the
    # model's own ordering within each (it was told to rank by consequence).
    items.sort(key=lambda i: section_order.get(i.get("section", ""), 99))

    # ── context for ambiguous acronyms ───────────────────────────────
    #
    # An acronym with two grounded readings is a question the letters cannot
    # answer, so the index enters it as a placeholder and the annotator asks
    # the surrounding prose. The unit of "surrounding" is the brief: assembled
    # once here rather than per block, both because the evidence may be
    # paragraphs away from the mention and because `senses` is a query.
    brief_senses = alias_index.senses(conn)
    brief_context = "\n".join(
        part
        for item in items
        for part in (
            (item.get("headline") or ""),
            (item.get("summary") or ""),
            (item.get("why_it_matters") or ""),
            (item.get("top_line") or ""),
        )
        if part
    )
    # The same unit, for the same reason, for generated acronyms: an alias
    # nobody has written down may only match where the brief names the entity
    # some other way. `strict_case` is off here, as everywhere in a brief.
    brief_present = alias_index.refs_present(index, brief_context, tier_a=tier_a)

    # ── the top block ────────────────────────────────────────────────
    #
    # Exactly `min(3, len(items))` top lines, whatever the model returned.
    #
    # The CAP is editorial -- a "top 3" of six items is not a top 3 -- and the
    # schema cannot express "at most three across the array". The FLOOR matters
    # as much: `top_line` is per-item and may be empty, and a model reading the
    # prompt's three as a quality bar left both items of a two-item brief empty,
    # so it shipped with no top block at all and the rail silently dropped its
    # contents list. The prompt asks for the same number; this makes it true
    # whichever way a model reads it. The fallback line is the item's own
    # HEADLINE, promoted rather than invented -- the prompt already requires a
    # headline to be the one sentence that could not sit above a different
    # article, which is what a top line is. ADR 0120.
    wanted = min(3, len(items))
    top_lines: dict[int, str] = {}

    # The model's own choices first, in document order, up to the cap.
    for position, item in enumerate(items, start=1):
        if len(top_lines) >= wanted:
            break
        line = (item.get("top_line") or "").strip()
        if line:
            top_lines[position] = line

    # Then fill from the front of the editorial order, so the promoted items are
    # the ones the brief already leads with.
    for position, item in enumerate(items, start=1):
        if len(top_lines) >= wanted:
            break
        if position in top_lines:
            continue
        headline = (item.get("headline") or "").strip()
        if headline:
            top_lines[position] = headline

    # 1..3 in document order, which is also the order the rail reads them in.
    top_ranks = {position: rank for rank, position in enumerate(sorted(top_lines), start=1)}

    ordinal = 0
    per_section: dict[str, int] = {}

    for position, item in enumerate(items, start=1):
        section = item.get("section") or section_names()[0]
        per_section[section] = per_section.get(section, 0) + 1
        item_id = f"{brief_id}:i{position:02d}"

        top_line = top_lines.get(position, "")
        top_rank = top_ranks.get(position)

        db.insert(
            conn,
            "brief_items",
            {
                "id": item_id,
                "brief_id": brief_id,
                "section": section,
                "rank": per_section[section],
                "cluster_id": item.get("cluster_id"),
                "headline": (item.get("headline") or "").strip(),
                "state": item.get("state") or "new",
                "state_note": (item.get("state_note") or "").strip() or None,
                "top_rank": top_rank,
                "top_line": top_line or None,
                # THE RENDER ORDER OF THE BRIEF, and the reason the column exists.
                # Postgres has no rowid, so insertion order is not recoverable,
                # and `rank` cannot stand in because it restarts per section.
                # Without this the brief renders in whatever order the heap hands
                # back, and nothing reports it.
                "ord": position,
                "created_at": now,
            },
        )
        report.items += 1

        # Both halves are annotated identically, so acronym definitions and
        # entity hover cards work in the judgement exactly as in the facts.
        for role in ("summary", "why_it_matters"):
            text = (item.get(role) or "").strip()
            if not text:
                continue
            blocks = annotate_blocks(
                markdown_to_blocks(text, section=position, heading=None),
                index,
                tier_a=tier_a,
                # Context for ambiguous acronyms is the WHOLE brief, not this
                # paragraph. An acronym spelled out once in the lead and used
                # bare for the rest of the page is how people write; resolving
                # per-block would see only the bare uses and decline them all.
                senses=brief_senses,
                context=brief_context,
                present_refs=brief_present,
            )
            for block in blocks:
                ordinal += 1
                block.ordinal = ordinal
                block.id = f"b{ordinal}"
                report.spans += len(block.spans)
                db.insert(
                    conn,
                    "brief_blocks",
                    {
                        "id": f"{brief_id}:{block.id}",
                        "brief_id": brief_id,
                        "section": position,
                        "heading": None,
                        "ordinal": ordinal,
                        "type": block.type,
                        "depth": block.depth,
                        "text": block.text,
                        "spans": db.jdump(block.spans),
                        "marks": db.jdump([m.as_dict() for m in block.marks]),
                        "item_id": item_id,
                        "role": role,
                    },
                )

    report.sections = len(per_section)
    report.blocks = ordinal
    return brief_id


def reannotate(conn: db.Conn, brief_id: str) -> dict[str, int]:
    """Re-resolve spans against the CURRENT alias index. Zero model calls.

    This is the payoff of storing offsets rather than markup: improving the
    alias table retro-fixes every historical brief for free.
    """
    from venona.services.annotate import Block, Mark

    index = build_index(conn)
    rows = repo.select_brief_blocks_by_brief_id(conn, brief_id).fetchall()

    # Active entities, PLUS the ones this brief itself named.
    #
    # `tier_a` gates strictness-1 aliases, and generation builds it from the
    # brief's own retrieval set (see the `tier_a` in `_write_brief`).
    # `active` alone is not enough: the index carries provisional entities, and
    # those are exactly the ones a re-annotation exists to pick up. Without the
    # union this pass re-resolves a brief against the current index and still
    # drops every single-source entity in it -- `Govini`, `FUSE Inc.`,
    # `Northcom` -- which is the docstring's promise failing on the one path
    # that makes it.
    #
    # Sourced from the brief's own DOCUMENTS, not from its stored spans.
    # `brief_service.brief_entities` reads the spans, which is circular here: an
    # entity that was never annotated has no span, so it could never be added by
    # the very pass meant to add it. This is the same join `define.targets()`
    # uses to decide what a brief is about.
    #
    # Resolved through `entity_mappings`, because `assertions` stores the id the
    # model was given and never rewrites it (invariant 1 -- merges are mapping
    # rows, not UPDATEs). The index resolves its refs to the SURVIVOR, so a raw
    # loser id here would match nothing and the merged entity would stay gated
    # out -- measured 2026-08-16, 224 of 2312 distinct raw ids in the live
    # corpus are merge losers, and `Govini`/`FUSE Inc.` above are exactly that
    # case. `graph/projector` bridges the same column the same way.
    from venona.graph.projector import resolve_mappings  # noqa: PLC0415

    mappings = resolve_mappings(conn)
    tier_a = frozenset(r["id"] for r in repo.select_entities_2(conn)) | frozenset(
        mappings.get(r["id"], r["id"]) for r in repo.select_brief_items_by_brief_id(conn, brief_id)
    )

    # Context for ambiguous acronyms is the whole brief, assembled from the
    # blocks themselves. Without it this is the one path that never applies
    # sense resolution, so a surface a curator had just settled stays unmarked
    # in every brief already written.
    senses = alias_index.senses(conn)
    context = "\n".join(r["text"] or "" for r in rows)
    present = alias_index.refs_present(index, context, tier_a=tier_a)

    before = after = 0
    for row in rows:
        block = Block(
            id=row["id"],
            type=row["type"],
            depth=row["depth"] or 0,
            text=row["text"],
            marks=[Mark(**m) for m in db.jload(row["marks"], [])],
            spans=db.jload(row["spans"], []),
        )
        before += len(block.spans)
        annotate_blocks(
            [block],
            index,
            tier_a=tier_a,
            senses=senses,
            context=context,
            present_refs=present,
        )
        after += len(block.spans)
        repo.update_brief_blocks_by_spans_id(conn, db.jdump(block.spans), row["id"])

    return {"blocks": len(rows), "spans_before": before, "spans_after": after}
