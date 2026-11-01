"""The recency bound analyze spends against, and why it is the brief's own.

The defect these cover cost real money and produced a brief that apologised for
a day that was not quiet. On `run:06a8160f`: 112 clusters analysed at ~$0.117
each, of which **8** were inside the 36-hour window `brief.build_retrieval`
retrieves by. 104 analyze calls -- ~$12.19 and ~71 of the run's 93 minutes --
bought clusters that could not have reached a brief under any ranking.

`brief.py` already carried the measurement ("94% of the analyze spend could not
have reached a brief") and `run_source_stats.fresh`/`stale` existed to make it
visible. Nothing acted on either. These tests are what makes the two stages
agree, so the next person to widen one has to widen both.
"""

from __future__ import annotations

from venona.db import core as db
from venona.pipeline.analyze import BEGINNING_OF_TIME, pending_clusters
from venona.pipeline.brief import build_retrieval
from venona.pipeline.types import MIN_BRIEFABLE_CHARS, retrieval_window
from venona.settings import get_settings

NOW = "2026-08-16T12:00:00.000Z"


def _pending(
    conn: db.Conn,
    cluster_id: str,
    *,
    published_at: str | None,
    chars: int = 4000,
    analyzed: bool = False,
) -> None:
    """An unanalysed cluster with a body of a given length."""
    body = "x" * chars
    conn.execute(
        "INSERT INTO documents (id, url, url_key, title, text_nfc, text_chars, "
        "content_hash, cluster_id, published_at, fetched_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,'n','n')",
        (
            f"doc:{cluster_id}",
            f"https://e.com/{cluster_id}",
            f"https://e.com/{cluster_id}",
            "Headline",
            body,
            len(body),
            cluster_id,
            cluster_id,
            published_at,
        ),
    )
    db.upsert(
        conn,
        "clusters",
        {
            "id": cluster_id,
            "canonical_doc_id": f"doc:{cluster_id}",
            "title": "Headline",
            "published_at": published_at,
            "analyzed_at": "2026-08-16T00:00:00Z" if analyzed else None,
            "first_seen": "n",
            "last_seen": "n",
        },
        key=["id"],
    )


def _seed_a_brief(conn: db.Conn) -> None:
    """One brief row, so `retrieval_window` stops calling this a first run."""
    db.upsert(
        conn,
        "briefs",
        {
            "id": "brief:2026-08-15:prior",
            "date": "2026-08-15",
            "title": "Yesterday",
            "generated_at": "2026-08-15T12:00:00.000Z",
            "created_at": "2026-08-15T12:00:00.000Z",
        },
        key=["id"],
    )


# ── the window itself ────────────────────────────────────────────────


def test_a_first_run_widens_the_window(conn: db.Conn) -> None:
    """A reader with an empty database has no yesterday.

    At 36 hours a cold start briefs whatever the last day and a half happened to
    contain, which on the first real run was five items -- while a `must_read`
    scoring 76 and another scoring 60 sat just outside, analysed and paid for.
    """
    settings = get_settings()
    conn.execute("DELETE FROM briefs")
    _, hours = retrieval_window(conn, now_iso=NOW, settings=settings)
    assert hours == settings.pipeline.first_run_lookback_hours


def test_the_second_run_is_a_daily_brief_again(conn: db.Conn) -> None:
    """The widening is for the FIRST brief only. A permanently wide window is a
    greatest-hits list, which is the thing `lookback_hours` exists to prevent."""
    settings = get_settings()
    conn.execute("DELETE FROM briefs")
    _seed_a_brief(conn)
    _, hours = retrieval_window(conn, now_iso=NOW, settings=settings)
    assert hours == settings.pipeline.lookback_hours


def test_the_window_is_keyed_on_briefs_not_runs(conn: db.Conn) -> None:
    """A run that fetched and skipped the brief has not spent that budget.

    `--skip-brief` and a run that died in analyze both leave a `runs` row and no
    brief, and both should still get a first reader's wide first brief.
    """
    settings = get_settings()
    conn.execute("DELETE FROM briefs")
    db.upsert(
        conn,
        "runs",
        {
            "id": "run:earlier",
            "kind": "on_demand",
            "status": "succeeded",
            "started_at": NOW,
        },
        key=["id"],
    )
    _, hours = retrieval_window(conn, now_iso=NOW, settings=settings)
    assert hours == settings.pipeline.first_run_lookback_hours


# ── analyze applies it ───────────────────────────────────────────────


def test_analyze_refuses_to_score_what_the_brief_cannot_use(conn: db.Conn) -> None:
    """The $12 defect, in one assertion."""
    conn.execute("DELETE FROM briefs")
    _seed_a_brief(conn)
    _pending(conn, "cluster:fresh", published_at="2026-08-16T06:00:00Z")
    _pending(conn, "cluster:stale", published_at="2026-08-09T06:00:00Z")

    cutoff, _ = retrieval_window(conn, now_iso=NOW, settings=get_settings())
    ids = {r["cluster_id"] for r in pending_clusters(conn, cutoff=cutoff, now=NOW)}
    assert "cluster:fresh" in ids
    assert "cluster:stale" not in ids


def test_analyze_refuses_what_the_brief_will_call_too_short(conn: db.Conn) -> None:
    """`MIN_BRIEFABLE_CHARS` is a bound on SPEND, not only on presentation.

    One in-window cluster scored 31 at 548 characters, was analysed at full
    price, and was then dropped by the brief's `too_short` filter.
    """
    conn.execute("DELETE FROM briefs")
    _seed_a_brief(conn)
    _pending(
        conn, "cluster:stub", published_at="2026-08-16T06:00:00Z", chars=MIN_BRIEFABLE_CHARS - 1
    )

    cutoff, _ = retrieval_window(conn, now_iso=NOW, settings=get_settings())
    ids = {r["cluster_id"] for r in pending_clusters(conn, cutoff=cutoff, now=NOW)}
    assert "cluster:stub" not in ids


def test_undated_clusters_survive_the_window(conn: db.Conn) -> None:
    """The brief treats a missing `published_at` as ALWAYS in-window, so analyze
    must too. The predicate is copied from `build_retrieval` clause for clause;
    dropping the `IS NULL` half here would starve the brief of exactly the
    clusters it is most willing to take."""
    conn.execute("DELETE FROM briefs")
    _seed_a_brief(conn)
    _pending(conn, "cluster:undated", published_at=None)

    cutoff, _ = retrieval_window(conn, now_iso=NOW, settings=get_settings())
    ids = {r["cluster_id"] for r in pending_clusters(conn, cutoff=cutoff, now=NOW)}
    assert "cluster:undated" in ids


def test_undated_clusters_are_not_sorted_to_the_back(conn: db.Conn) -> None:
    """`COALESCE(published_at, '0000') DESC` sank them below every dated row.

    Measured: all six undated clusters on `run:06a8160f` were deferred at the
    call cap, and five of them cleared every other brief gate. The stage was
    systematically starving the brief of the rows it accepts unconditionally.
    """
    conn.execute("DELETE FROM briefs")
    _seed_a_brief(conn)
    _pending(conn, "cluster:undated", published_at=None)
    _pending(conn, "cluster:dated", published_at="2026-08-16T01:00:00Z")

    cutoff, _ = retrieval_window(conn, now_iso=NOW, settings=get_settings())
    order = [r["cluster_id"] for r in pending_clusters(conn, cutoff=cutoff, now=NOW)]
    assert order.index("cluster:undated") < order.index("cluster:dated")


def test_an_ingest_run_is_exempt_from_both_bounds(conn: db.Conn) -> None:
    """A reader clicked a button about ONE page.

    Refusing it for being old or short would make the button silently do
    nothing, which is worse than the spend it saves.
    """
    conn.execute("DELETE FROM briefs")
    _seed_a_brief(conn)
    _pending(conn, "cluster:old", published_at="2025-01-01T00:00:00Z", chars=100)

    rows = pending_clusters(conn, cluster_id="cluster:old", cutoff=None, now=NOW)
    assert [r["cluster_id"] for r in rows] == ["cluster:old"]


# ── the deep read: a window nobody named (epic Z, ADR 0228) ──────────


def test_a_deep_read_with_no_window_reads_what_the_brief_could_not(conn: db.Conn) -> None:
    """The whole point of the kind, and it shipped not doing it.

    "Everything waiting" sends no lookback, which meant no override, which meant
    the CONFIGURED window -- the brief's 36 hours, which the daily has already
    read. Measured on the first real deep read: `clusters_seen: 0`, the stage
    finished in under two seconds, and the 171-cluster backlog it exists to
    drain was untouched. Silent and total, because a successful run that read
    nothing looks exactly like a corpus with nothing left in it.
    """
    conn.execute("DELETE FROM briefs")
    _seed_a_brief(conn)
    _pending(conn, "cluster:backlog", published_at="2026-06-09T00:00:00Z")

    cutoff, _hours = retrieval_window(conn, now_iso=NOW, settings=get_settings())
    bounded = {r["cluster_id"] for r in pending_clusters(conn, cutoff=cutoff, now=NOW)}
    unbounded = {r["cluster_id"] for r in pending_clusters(conn, cutoff=BEGINNING_OF_TIME, now=NOW)}

    assert "cluster:backlog" not in bounded, "the brief's window must still exclude it"
    assert "cluster:backlog" in unbounded, "a deep read must reach the backlog"


def test_an_unbounded_read_still_refuses_a_stub(conn: db.Conn) -> None:
    """Dropping the DATE bound must not take the LENGTH one with it.

    Both lived in one `elif`, so passing no cutoff removed the length floor too
    and a deep read would have paid a model call per two-paragraph stub. Only
    `cluster_id` may suspend this, and the test above it says why.
    """
    conn.execute("DELETE FROM briefs")
    _seed_a_brief(conn)
    _pending(conn, "cluster:stub", published_at="2026-06-09T00:00:00Z", chars=40)
    _pending(conn, "cluster:real", published_at="2026-06-09T00:00:00Z", chars=MIN_BRIEFABLE_CHARS)

    reached = {r["cluster_id"] for r in pending_clusters(conn, cutoff=BEGINNING_OF_TIME, now=NOW)}
    assert "cluster:real" in reached
    assert "cluster:stub" not in reached


# ── the two stages must not drift ────────────────────────────────────


def test_analyze_and_the_brief_agree_on_the_window(conn: db.Conn) -> None:
    """The regression guard that matters.

    Both stages resolve the bound through `retrieval_window`, so this asserts
    the property rather than a number: every cluster the brief retrieves must be
    one analyze was willing to pay for. They disagreed for the life of the
    project -- the brief enforced `lookback_hours`, analyze had no date
    predicate at all -- and nothing failed.
    """
    conn.execute("DELETE FROM briefs")
    _seed_a_brief(conn)
    for hours_ago, cid in ((2, "a"), (20, "b"), (40, "c"), (200, "d")):
        stamp = f"2026-08-{16 - hours_ago // 24:02d}T{(12 - hours_ago % 24) % 24:02d}:00:00Z"
        _pending(conn, f"cluster:{cid}", published_at=stamp)

    cutoff, _ = retrieval_window(conn, now_iso=NOW, settings=get_settings())
    analyzable = {r["cluster_id"] for r in pending_clusters(conn, cutoff=cutoff, now=NOW)}

    # Mark them analysed and scored, then ask the brief what it would take.
    for cid in analyzable:
        conn.execute(
            "UPDATE clusters SET analyzed_at=?, relevance_overall=50, tier='must_read' WHERE id=?",
            (NOW, cid),
        )
    retrieved = {r["id"] for r in build_retrieval(conn, now=NOW).clusters}

    assert retrieved <= analyzable, "the brief retrieved a cluster analyze would not score"


# ── one document's failure is not the run's ──────────────────────────


def _ctx(conn: db.Conn):
    from venona.pipeline.types import FrozenClock, RunContext, SeededIdGen

    return RunContext(
        run_id="run:t",
        db=conn,
        llm=None,
        settings=get_settings(),
        clock=FrozenClock(),
        ids=SeededIdGen(0),
    )


async def test_a_terminal_failure_on_one_cluster_does_not_abort_the_stage(
    conn: db.Conn, monkeypatch
) -> None:
    """⭐ The stage-wide abort. `asyncio.gather` without `return_exceptions`
    propagates the FIRST exception out of the whole batch, so one bad document
    ends analyze — and `brief`, `curate` and the projection never run. It also
    cancels its siblings, whose paid calls are already in flight, so their spend
    is never ledgered.

    The envelope-error path already degrades per cluster. A raised exception has
    to take the same route.
    """
    from venona.llm.executor import LlmError
    from venona.pipeline import analyze

    recent = "2026-08-16T09:00:00Z"
    _pending(conn, "cluster:good", published_at=recent)
    _pending(conn, "cluster:bad", published_at=recent)

    seen: list[str] = []

    async def one_of_them_explodes(**kwargs):
        seen.append(kwargs["subject"])
        if kwargs["subject"] == "cluster:bad":
            raise LlmError("claude exited 1: the model went away")
        return _declined_outcome()

    monkeypatch.setattr(analyze, "analyze_document", one_of_them_explodes)

    # Returns rather than raising: that IS the fix. Before it, this call ended
    # the stage and every stage after it.
    report = await analyze.run_analyze(_ctx(conn), limit=10)

    assert set(seen) == {"cluster:good", "cluster:bad"}, (
        f"a sibling was cancelled instead of being allowed to finish: {seen}"
    )
    assert report.clusters_seen == 2
    assert any("went away" in w for w in report.warnings), (
        f"the raised failure was not reported on the run: {report.warnings}"
    )


async def test_one_outage_is_one_message_and_names_no_cluster(conn, monkeypatch) -> None:
    """THE PRODUCTION RUN OF 2026-09-15.

    An expired credential failed every article in the window identically and
    each one warned, so `/admin/runs/:id` carried sixty near-identical
    paragraphs keyed by `cluster:<hex>` -- a word this repo uses about itself,
    attached to a thing the reader cannot act on.

    `test_copy_style` bans the vocabulary and could never have caught this: it
    walks `ast.Constant`, and the id arrived as an interpolation. So the check
    has to run the composition rather than read it.
    """
    from venona.llm.executor import LlmError
    from venona.pipeline import analyze

    recent = "2026-08-16T09:00:00Z"
    for n in range(4):
        _pending(conn, f"cluster:{n:024x}", published_at=recent)

    async def every_one_fails(**kwargs):
        raise LlmError("the model service could not authenticate")

    monkeypatch.setattr(analyze, "analyze_document", every_one_fails)
    ctx = _ctx(conn)
    report = await analyze.run_analyze(ctx, limit=10)

    assert report.clusters_skipped == 4
    texts = [m.text for m in ctx.warnings]
    assert len(texts) == 1, f"one outage wrote {len(texts)} messages: {texts}"
    assert "on 4 articles" in texts[0], f"the message does not say how many: {texts[0]}"
    assert "cluster:" not in texts[0], f"an internal id reached the reader: {texts[0]}"


def _declined_outcome():
    """A well-formed 'the call failed' outcome: the shape the loop already knows
    how to degrade past, so the test isolates the RAISED path beside it."""
    from types import SimpleNamespace

    return SimpleNamespace(
        calls=1,
        cost_usd=0.0,
        ok=False,
        result=SimpleNamespace(refused=False, error="declined", data=None),
    )


# ── one cluster's write set is atomic ────────────────────────────────


def test_a_death_before_the_stamp_leaves_no_assertions_behind(conn: db.Conn, monkeypatch) -> None:
    """⭐ `analyzed_at` is the idempotency key, so it must land with the
    assertions it vouches for.

    A crash in between leaves the assertions written and the cluster still
    pending, and the next run writes a second copy of every one of them.
    Assertions are append-only by invariant 1 with a fresh uuid7 each, so
    nothing downstream notices -- it silently doubles the evidence behind every
    count an entity is promoted and ranked on.
    """
    import pytest
    from venona.ontology import get_ontology
    from venona.pipeline import analyze as analyze_mod
    from venona.pipeline.analyze import AnalyzeReport, persist_cluster
    from venona.pipeline.resolve import Resolver
    from venona.pipeline.types import FrozenClock, RunContext, SeededIdGen

    _pending(conn, "cluster:atomic", published_at=NOW)
    settings = get_settings()
    ctx = RunContext(
        run_id="run:atomic",
        db=conn,
        llm=None,
        settings=settings,
        clock=FrozenClock(),
        ids=SeededIdGen(0),
    )
    # The type comes from the live ontology rather than a literal: the entity
    # type set is per domain (epic B), and a hardcoded name resolves to nothing
    # on an instance that does not declare it -- which would make this test
    # pass by writing no assertions at all.
    entity_type = get_ontology().type_names[0]
    data = {
        "entities": [
            {
                "surface": "Aardvark Systems",
                "type": entity_type,
                "context_sentence": "Aardvark Systems announced a thing.",
            }
        ],
        "relations": [],
        "relevance": {"axes": {}},
    }
    kwargs = dict(
        resolver=Resolver(conn, settings=settings, lexicon={}),
        ontology=get_ontology(),
        cluster_id="cluster:atomic",
        article_id="doc:cluster:atomic",
        data=data,
        report=AnalyzeReport(),
    )

    def _assertions() -> int:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM assertions WHERE cluster_id = 'cluster:atomic'"
        ).fetchone()["n"]

    # THE GUARD AGAINST A VACUOUS TEST. If this data produced no assertions,
    # everything below would pass without a transaction anywhere near it.
    persist_cluster(ctx, **{**kwargs, "cluster_id": "cluster:atomic"})
    written = _assertions()
    assert written, "the fixture must actually write an assertion, or the test proves nothing"
    conn.execute("DELETE FROM assertions WHERE cluster_id = 'cluster:atomic'")
    conn.execute("UPDATE clusters SET analyzed_at = NULL WHERE id = 'cluster:atomic'")
    before = _assertions()

    def die(*args, **kw):
        raise RuntimeError("the process was killed before the stamp")

    monkeypatch.setattr(analyze_mod, "_store_scores", die)
    with pytest.raises(RuntimeError, match="killed"):
        persist_cluster(ctx, **kwargs)

    assert _assertions() == before, "the assertions must have gone with the stamp that never landed"

    pending = [r["cluster_id"] for r in pending_clusters(conn, now=NOW)]
    assert "cluster:atomic" in pending, (
        "the cluster must still be offered, so the retry writes its assertions once, not twice"
    )
