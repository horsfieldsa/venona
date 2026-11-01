"""Brief generation, retrieval selection, and free re-scoring.

The retrieval rule is frozen because `mentioned_entity_ids` is validated
against exactly that set — if retrieval wobbles, the model's hints stop matching
and annotation quality degrades silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from venona.db import core as db
from venona.pipeline.brief import (
    NOISE_FLOOR,
    QUIET_DAY_FILL,
    QUIET_DAY_FLOOR,
    build_retrieval,
    build_user_content,
    reannotate,
)
from venona.services.rescore import rescore_all, rubric_fingerprint
from venona.settings import get_settings

#: A fixed clock. `build_retrieval` and friends take `now` explicitly, because
#: a wall-clock default inside a determinism-guarded package makes a replay
#: retrieve a different window than the recording did.
NOW = "2026-08-04T18:00:00.000Z"


def _cluster(
    conn: db.Conn,
    cluster_id: str,
    *,
    score: int,
    tier: str,
    title: str = "Headline",
    axes: dict | None = None,
) -> None:
    # A realistic body length: the retrieval floor rejects headline-only
    # clusters, so a fixture with a stub body would not be briefable.
    body = "The Army awarded a contract for command and control work. " * 20
    conn.execute(
        "INSERT INTO documents (id, url, url_key, title, text_nfc, text_chars, "
        "content_hash, cluster_id, fetched_at, created_at) VALUES (?,?,?,?,?,?,?,?,'n','n')",
        (
            f"doc:{cluster_id}",
            f"https://e.com/{cluster_id}",
            f"https://e.com/{cluster_id}",
            title,
            body,
            len(body),
            cluster_id,
            cluster_id,
        ),
    )
    db.upsert(
        conn,
        "clusters",
        {
            "id": cluster_id,
            "canonical_doc_id": f"doc:{cluster_id}",
            "title": title,
            "relevance_overall": score,
            "tier": tier,
            "relevance_note": "why it matters",
            "so_what": "consequence",
            "axes": db.jdump(axes or {}),
            "analyzed_at": "2026-08-04T12:00:00Z",
            "first_seen": "n",
            "last_seen": "n",
        },
        key=["id"],
    )


# ── retrieval ────────────────────────────────────────────────────────


def test_strong_days_use_only_above_background(conn: db.Conn) -> None:
    for i in range(6):
        _cluster(conn, f"cluster:s{i}", score=80 - i, tier="must_read")
    for i in range(4):
        _cluster(conn, f"cluster:b{i}", score=10, tier="background")

    retrieval = build_retrieval(conn, now=NOW)
    assert not retrieval.quiet_day
    assert all(c["tier"] != "background" for c in retrieval.clusters)


def test_quiet_day_is_flagged_and_topped_up(conn: db.Conn) -> None:
    """A day with nothing above background is a REAL outcome.

    Emitting an empty brief would be useless, and inflating background items to
    fill space destroys trust faster than anything else -- so we include the
    best of what there is and tell the model it was quiet.
    """
    for i in range(8):
        _cluster(conn, f"cluster:q{i}", score=30 - i, tier="background")

    retrieval = build_retrieval(conn, now=NOW)
    assert retrieval.quiet_day
    assert 0 < len(retrieval.clusters) <= QUIET_DAY_FILL

    content = build_user_content(conn, retrieval, date="2026-08-04", now=NOW)
    assert "nothing today scored above" in content
    assert "Do NOT inflate" in content


def test_a_thin_day_is_not_described_as_an_empty_one(conn: db.Conn) -> None:
    """The prompt must not assert something the retrieval contradicts.

    `quiet_day` fires on `len(strong) < QUIET_DAY_FLOOR`, but the note said
    "nothing today scored above the 'background' tier" whichever was true. On
    `run:06a8160f` three clusters cleared the bar -- one of them a `must_read`
    scoring 60 -- and the model was still told none had. It believed the prompt
    over the material it was handed and titled the brief "A thin day".
    """
    _cluster(conn, "cluster:strong", score=60, tier="must_read")
    for i in range(3):
        _cluster(conn, f"cluster:bg{i}", score=20 - i, tier="background")

    retrieval = build_retrieval(conn, now=NOW)
    assert retrieval.quiet_day, "one strong item is still below the floor"

    content = build_user_content(conn, retrieval, date="2026-08-04", now=NOW)
    assert "nothing today scored above" not in content
    assert "cleared the 'worth knowing' bar" in content
    assert "Do NOT inflate" in content, "the anti-padding instruction survives both wordings"


def test_the_retrieval_funnel_does_not_count_its_own_brief_twice(
    conn: db.Conn,
) -> None:
    """`_emit_metrics` runs after `_persist`, and has to.

    `report.blocks` and `report.spans` do not exist until `_persist` runs, so
    the funnel is measured with this brief's own `brief_clusters` rows already
    inserted. Without the exclusion each kept cluster is counted twice -- once
    as `kept`, once as an `already_briefed` drop -- which read
    5 + 105 + 2 + 5 = 117 against 112 offered on `run:06a8160f` and surfaced as
    `reconciled = 0.0`. The invariant is measurement, so it never raises; a test
    is the only thing that notices.
    """
    from venona.pipeline.brief import _retrieval_funnel

    conn.execute("DELETE FROM brief_clusters")
    conn.execute("DELETE FROM clusters")
    conn.execute("DELETE FROM documents")
    for i in range(3):
        _cluster(conn, f"cluster:f{i}", score=60, tier="must_read")

    db.upsert(
        conn,
        "briefs",
        {
            "id": "brief:today",
            "date": "2026-08-04",
            "title": "Today",
            "generated_at": "2026-08-04T12:00:00.000Z",
            "created_at": "2026-08-04T12:00:00.000Z",
        },
        key=["id"],
    )
    for rank, i in enumerate(range(3)):
        db.upsert(
            conn,
            "brief_clusters",
            {
                "brief_id": "brief:today",
                "cluster_id": f"cluster:f{i}",
                "rank": rank,
                "created_at": "n",
            },
            key=["brief_id", "cluster_id"],
        )

    cutoff = "2026-08-01T00:00:00Z"
    own = _retrieval_funnel(conn, cutoff=cutoff, kept=3, used=3, brief_id="brief:today")
    assert own.reconciles(), f"offered={own.offered} kept={own.kept} drops={own.drops}"
    assert "already_briefed" not in own.drops

    # A cluster briefed by SOMEONE ELSE is still a real drop -- the exclusion is
    # narrow, not a removal of the filter.
    other = _retrieval_funnel(conn, cutoff=cutoff, kept=0, used=0, brief_id="brief:different")
    assert other.drops.get("already_briefed") == 3

    # A REWRITE of `brief:today` releases the same three, so they are kept
    # rather than counted as dropped. Counting them here would report a rewrite
    # as having found nothing while it was writing from them.
    rewritten = _retrieval_funnel(
        conn, cutoff=cutoff, kept=3, used=3, brief_id=None, rewrite_of="brief:today"
    )
    assert "already_briefed" not in rewritten.drops
    assert rewritten.reconciles()


def _brief_row(conn: db.Conn, brief_id: str, *, at: str) -> None:
    db.upsert(
        conn,
        "briefs",
        {
            "id": brief_id,
            "date": at[:10],
            "title": brief_id,
            "generated_at": at,
            "created_at": at,
        },
        key=["id"],
    )


def _claim(conn: db.Conn, brief_id: str, *cluster_ids: str) -> None:
    for rank, cluster_id in enumerate(cluster_ids):
        db.upsert(
            conn,
            "brief_clusters",
            {"brief_id": brief_id, "cluster_id": cluster_id, "rank": rank, "created_at": "n"},
            key=["brief_id", "cluster_id"],
        )


def test_a_rewrite_retrieves_the_brief_it_is_rewriting(conn: db.Conn) -> None:
    """The whole reason `rewrite_of` exists.

    Retrieval excludes every cluster any brief has featured, so by the time the
    Rewrite button is pressable the brief on screen has claimed all of them: the
    run retrieved nothing, ended `succeeded_partial` with "nothing new since the
    last brief", and never reached the model. It could not have worked on any
    day, and `run:06a82809` is the one where someone noticed.
    """
    for i in range(3):
        _cluster(conn, f"cluster:r{i}", score=80 - i, tier="must_read")
    _brief_row(conn, "brief:yesterday", at="2026-08-15T12:00:00.000Z")
    _claim(conn, "brief:yesterday", "cluster:r0", "cluster:r1", "cluster:r2")

    assert build_retrieval(conn, now=NOW).clusters == [], (
        "the bug: a claimed window retrieves nothing"
    )

    again = build_retrieval(conn, now=NOW, rewrite_of="brief:yesterday")
    assert sorted(c["id"] for c in again.clusters) == ["cluster:r0", "cluster:r1", "cluster:r2"]


@pytest.mark.negative
def test_a_rewrite_exempts_ONE_brief_and_no_others(conn: db.Conn) -> None:
    """The scope is the whole difference from the carve-out this replaces.

    That one was `bc.brief_id IS NOT ?`, which with a single brief row is false
    for every row -- so the `NOT EXISTS` was vacuously true, nothing was ever
    excluded, and six briefs in one day served the same top stories. A cluster
    another brief has featured must stay excluded, INCLUDING one featured by
    both: releasing it would let a rewrite re-run someone else's story.
    """
    for name in ("mine", "theirs", "both"):
        _cluster(conn, f"cluster:{name}", score=70, tier="must_read")
    _brief_row(conn, "brief:mine", at="2026-08-15T12:00:00.000Z")
    _brief_row(conn, "brief:theirs", at="2026-08-14T12:00:00.000Z")
    _claim(conn, "brief:mine", "cluster:mine", "cluster:both")
    _claim(conn, "brief:theirs", "cluster:theirs", "cluster:both")

    got = {c["id"] for c in build_retrieval(conn, now=NOW, rewrite_of="brief:mine").clusters}
    assert got == {"cluster:mine"}, got


def test_no_rewrite_target_exempts_nothing(conn: db.Conn) -> None:
    """`None` is every path except a rewrite, and must behave exactly as before
    -- the daily run must not quietly start re-briefing yesterday."""
    _cluster(conn, "cluster:done", score=70, tier="must_read")
    _brief_row(conn, "brief:old", at="2026-08-15T12:00:00.000Z")
    _claim(conn, "brief:old", "cluster:done")

    assert build_retrieval(conn, now=NOW, rewrite_of=None).clusters == []


@pytest.mark.negative
def test_pure_noise_is_excluded_even_on_a_quiet_day(conn: db.Conn) -> None:
    _cluster(conn, "cluster:noise", score=NOISE_FLOOR - 1, tier="background")
    retrieval = build_retrieval(conn, now=NOW)
    assert all(c["id"] != "cluster:noise" for c in retrieval.clusters)


def test_quiet_day_threshold_is_the_documented_one(conn: db.Conn) -> None:
    for i in range(QUIET_DAY_FLOOR):
        _cluster(conn, f"cluster:t{i}", score=60, tier="worth_knowing")
    assert not build_retrieval(conn, now=NOW).quiet_day


def test_user_content_lists_entity_ids_the_model_may_use(conn: db.Conn) -> None:
    _cluster(conn, "cluster:e", score=70, tier="must_read")
    retrieval = build_retrieval(conn, now=NOW)
    content = build_user_content(conn, retrieval, date="2026-08-04", now=NOW)

    assert "prog:next-generation-command-and-control" in content
    assert "mentioned_entity_ids" in content
    assert "unresolved_mentions" in content
    # The annotator matches literal text, so the model must be told that.
    assert "EXACT names" in content


# ── free re-scoring ──────────────────────────────────────────────────


def test_reweighting_changes_scores_with_zero_model_calls(conn: db.Conn) -> None:
    """The reason the model is never asked for an overall score."""
    from venona.llm.schemas import axis_ids

    ids = axis_ids()
    axes = {ids[0]: {"score": 5, "why": "x"}, ids[1]: {"score": 0, "why": "y"}}
    _cluster(conn, "cluster:r", score=0, tier="background", axes=axes)

    report = rescore_all(conn)
    assert report.clusters >= 1

    row = conn.execute("SELECT relevance_overall FROM clusters WHERE id = 'cluster:r'").fetchone()
    assert row["relevance_overall"] > 0, "a maxed axis must contribute"


def test_rescore_is_idempotent(conn: db.Conn) -> None:
    from venona.llm.schemas import axis_ids

    axes = {a: {"score": 3, "why": "z"} for a in axis_ids()}
    _cluster(conn, "cluster:i", score=0, tier="background", axes=axes)

    rescore_all(conn)
    first = conn.execute("SELECT relevance_overall FROM clusters WHERE id='cluster:i'").fetchone()[
        "relevance_overall"
    ]

    second_report = rescore_all(conn)
    second = conn.execute("SELECT relevance_overall FROM clusters WHERE id='cluster:i'").fetchone()[
        "relevance_overall"
    ]

    assert first == second
    assert second_report.changed == 0


def test_missing_axis_is_counted_not_guessed(conn: db.Conn) -> None:
    """A cluster analysed before a rubric axis existed has no score for it. It
    contributes 0 and is COUNTED, so the UI can offer a re-analysis rather than
    silently pretending the number is complete."""
    _cluster(
        conn,
        "cluster:m",
        score=0,
        tier="background",
        axes={"product_overlap": {"score": 5, "why": "x"}},
    )
    report = rescore_all(conn)
    assert report.missing_axes >= 1


def test_rubric_fingerprint_ignores_weights(
    conn: db.Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A weight change must NOT invalidate stored scores -- applying it is free.
    A description change must, because the model answered a different question.

    Mutates a COPY. This used to write `profile/scoring_rubric.yaml` in the
    working tree three times -- through a CWD-relative path, restoring the
    original in a `finally` -- so an interrupt landing between the two writes
    left the user's real rubric containing "COMPLETELY DIFFERENT QUESTION:" in
    place of an axis description, which every subsequent analyze call would be
    sent. `config_service` already carries the same lesson for the ontology: a
    test must not be able to write to the working tree. `conftest.py` now fails
    any test that does.
    """
    import copy

    from venona.llm import schemas as schemas_module
    from venona.llm.schemas import load_rubric

    load_rubric.cache_clear()
    before = rubric_fingerprint()

    # `load_rubric` resolves `get_settings().profile_dir`, so redirect the
    # DIRECTORY rather than a path -- patching a path would leave the real
    # loader reading the real file, which is how the ontology version of this
    # bug survived its first fix.
    real = get_settings()
    scratch = copy.deepcopy(real)
    scratch.profile_dir = tmp_path / "profile"
    scratch.profile_dir.mkdir()
    path = scratch.profile_dir / "scoring_rubric.yaml"
    original = (real.profile_dir / "scoring_rubric.yaml").read_text(encoding="utf-8")
    monkeypatch.setattr(schemas_module, "get_settings", lambda: scratch)

    # The markers must exist in the CURRENT rubric or both replaces no-op and
    # the second assertion tests nothing -- so prove each replace changed the
    # text before asserting on its fingerprint.
    reweighted = original.replace("weight: 0.26", "weight: 0.30", 1).replace(
        "weight: 0.16", "weight: 0.12", 1
    )
    assert reweighted != original, "the weight markers drifted from the rubric"
    path.write_text(reweighted, encoding="utf-8")
    load_rubric.cache_clear()
    assert rubric_fingerprint() == before, "weights must not change the fingerprint"

    redescribed = original.replace("How central is this to", "COMPLETELY DIFFERENT QUESTION:", 1)
    assert redescribed != original, "the description marker drifted from the rubric"
    path.write_text(redescribed, encoding="utf-8")
    load_rubric.cache_clear()
    assert rubric_fingerprint() != before, "a description change must invalidate"

    load_rubric.cache_clear()


# ── re-annotation ────────────────────────────────────────────────────


def test_reannotate_picks_up_a_new_alias_for_free(conn: db.Conn) -> None:
    """The payoff of storing offsets rather than markup: improving the alias
    table retro-fixes every historical brief with zero model calls."""
    brief_id = "brief:2026-08-04"
    conn.execute("DELETE FROM brief_blocks WHERE brief_id = ?", (brief_id,))
    db.insert(
        conn,
        "brief_blocks",
        {
            "id": f"{brief_id}:bx",
            "brief_id": brief_id,
            "section": 1,
            "heading": "Test",
            "ordinal": 99,
            "type": "paragraph",
            "depth": 0,
            "text": "The Zephyr Overwatch effort continues.",
            "spans": db.jdump([]),
            "marks": db.jdump([]),
        },
    )

    assert reannotate(conn, brief_id)["spans_after"] == 0

    conn.execute(
        "INSERT INTO entities (id, type, name, canonical_key, status, first_seen, "
        "last_seen, created_at) VALUES ('prog:zephyr-overwatch','Program',"
        "'Zephyr Overwatch','zephyr overwatch','active','n','n','n')"
    )
    conn.execute(
        "INSERT INTO aliases (id, entity_id, surface, normalized, strictness, "
        "is_acronym, added_by, created_at) VALUES ('a1','prog:zephyr-overwatch',"
        "'Zephyr Overwatch','zephyr overwatch',3,0,'seed','n')"
    )

    after = reannotate(conn, brief_id)
    assert after["spans_after"] == 1, "the new alias must light up an existing brief"


@pytest.mark.negative
def test_headline_only_clusters_are_not_briefable(conn: db.Conn) -> None:
    """Caught in practice: a seed FIXTURE document (56 chars, score 88)
    dominated a real brief, and the model correctly reported it could not say
    anything from a headline. A cluster with no readable body has nothing to
    contribute beyond its title."""
    from venona.pipeline.brief import MIN_BRIEFABLE_CHARS

    conn.execute(
        "INSERT INTO documents (id, url, url_key, title, text_nfc, text_chars, "
        "content_hash, cluster_id, fetched_at, created_at) VALUES "
        "('doc:stub','https://e.com/stub','https://e.com/stub','Huge Headline',"
        "'tiny',5,'hstub','cluster:stub','n','n')"
    )
    db.upsert(
        conn,
        "clusters",
        {
            "id": "cluster:stub",
            "canonical_doc_id": "doc:stub",
            "title": "Huge Headline",
            "relevance_overall": 99,
            "tier": "must_read",
            "axes": db.jdump({}),
            "analyzed_at": "2026-08-04T12:00:00Z",
            "first_seen": "n",
            "last_seen": "n",
        },
        key=["id"],
    )

    retrieval = build_retrieval(conn, now=NOW)
    assert all(c["id"] != "cluster:stub" for c in retrieval.clusters), (
        f"a {5}-char document must not be briefed even at score 99 (floor is {MIN_BRIEFABLE_CHARS})"
    )


# ── recency: what makes it a DAILY brief ─────────────────────────────


def _recent(hours_ago: int) -> str:
    """`hours_ago` before NOW, not before the wall clock.

    The retrieval window is measured from whatever `now` the caller passes, so
    a fixture dated against the real clock and retrieved against a fixed one
    lands wherever the two happen to be relative to each other today."""
    from datetime import datetime, timedelta

    moment = datetime.strptime(NOW, "%Y-%m-%dT%H:%M:%S.%fZ") - timedelta(hours=hours_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dated_cluster(conn, cluster_id: str, *, score: int, published: str) -> None:
    _cluster(conn, cluster_id, score=score, tier="must_read")
    conn.execute("UPDATE clusters SET published_at = ? WHERE id = ?", (published, cluster_id))


def test_clusters_outside_the_lookback_window_are_excluded(conn) -> None:
    """Without this the brief is a greatest-hits list: ranking by score alone
    means yesterday's best story outranks today's news forever."""
    from venona.settings import get_settings

    settings = get_settings()
    inside = settings.pipeline.lookback_hours - 2
    outside = settings.pipeline.lookback_hours + 24

    _dated_cluster(conn, "cluster:fresh", score=60, published=_recent(inside))
    _dated_cluster(conn, "cluster:stale", score=95, published=_recent(outside))

    ids = {c["id"] for c in build_retrieval(conn, now=NOW).clusters}
    assert "cluster:fresh" in ids
    assert "cluster:stale" not in ids, "a 95-scoring item outside the window must not resurface"


def test_undated_clusters_are_still_eligible(conn) -> None:
    """A missing `published_at` is an honest unknown (see resolve_date), not a
    reason to silently drop real coverage from a feed with bad dates."""
    _cluster(conn, "cluster:undated", score=70, tier="must_read")
    conn.execute("UPDATE clusters SET published_at = NULL WHERE id = 'cluster:undated'")

    assert "cluster:undated" in {c["id"] for c in build_retrieval(conn, now=NOW).clusters}


def test_a_briefed_cluster_is_never_briefed_again(conn) -> None:
    """THE gate. Running the brief twice on the same day must not repeat."""
    _dated_cluster(conn, "cluster:once", score=80, published=_recent(2))

    first = {c["id"] for c in build_retrieval(conn, now=NOW).clusters}
    assert "cluster:once" in first

    conn.execute(
        "INSERT INTO brief_clusters (brief_id, cluster_id, rank, created_at) "
        "VALUES ('brief:2026-08-04', 'cluster:once', 1, 'n')"
    )

    second = {c["id"] for c in build_retrieval(conn, now=NOW).clusters}
    assert "cluster:once" not in second, "already featured; must not repeat"


def test_lookback_hours_is_read_from_settings(conn) -> None:
    """It was declared and read nowhere. Editing config/settings.yaml must
    actually change behaviour."""
    from copy import deepcopy

    from venona.settings import get_settings

    _dated_cluster(conn, "cluster:day-old", score=70, published=_recent(20))

    wide = deepcopy(get_settings())
    wide.pipeline.lookback_hours = 48
    assert "cluster:day-old" in {
        c["id"] for c in build_retrieval(conn, now=NOW, settings=wide).clusters
    }

    narrow = deepcopy(get_settings())
    narrow.pipeline.lookback_hours = 6
    assert "cluster:day-old" not in {
        c["id"] for c in build_retrieval(conn, now=NOW, settings=narrow).clusters
    }


def test_brief_top_clusters_is_read_from_settings(conn) -> None:
    from copy import deepcopy

    from venona.settings import get_settings

    for i in range(8):
        _dated_cluster(conn, f"cluster:m{i}", score=90 - i, published=_recent(1))

    capped = deepcopy(get_settings())
    capped.pipeline.brief_top_clusters = 3
    assert len(build_retrieval(conn, now=NOW, settings=capped).clusters) == 3


def test_acronym_gating_is_read_from_settings() -> None:
    """`acronym_min_sightings`, `acronym_min_articles` and
    `acronym_lookup_cap_per_run` were shadowed by module constants."""
    import inspect

    from venona.pipeline import acronyms

    source = inspect.getsource(acronyms.run_acronyms)
    assert "limits.acronym_min_sightings" in source
    assert "limits.acronym_min_articles" in source
    assert "limits.acronym_lookup_cap_per_run" in source
    for dead in ("MIN_LOOKUP_SIGHTINGS", "MIN_LOOKUP_ARTICLES", "MAX_LOOKUP_PER_RUN"):
        assert not hasattr(acronyms, dead), f"{dead} still shadows the config"


# ── what analyze already paid for ────────────────────────────────────


def test_key_facts_and_flags_reach_the_prompt(conn: db.Conn) -> None:
    """Migration 0004 captured both and nothing ever read them.

    `analyze` reads the whole article and pulls the specifics out; the brief
    prompt then got 4000 truncated characters and was expected to re-derive
    "$99.6M" from them. That is paying twice for the same work and getting the
    worse answer the second time.
    """
    _cluster(conn, "cluster:facts", score=80, tier="must_read")
    conn.execute(
        "UPDATE clusters SET key_facts = ?, flags = ? WHERE id = 'cluster:facts'",
        (
            db.jdump(["$99.6M award", "10 days in July 2026, ~10,000 participants"]),
            db.jdump({"press_release": True, "opinion": False}),
        ),
    )
    conn.commit()

    content = build_user_content(conn, build_retrieval(conn, now=NOW), date="2026-08-04", now=NOW)

    assert "$99.6M award" in content
    assert "~10,000 participants" in content
    # Only RAISED flags, and as a caveat rather than an instruction to drop it:
    # a press release announcing a real award is still news.
    assert "press_release" in content
    assert "opinion" not in content


def test_a_cluster_with_neither_still_renders(conn: db.Conn) -> None:
    """Both columns are NULL on every row predating migration 0004, and NULL
    means "not captured" -- which must not become an empty bullet."""
    _cluster(conn, "cluster:bare", score=80, tier="must_read")
    conn.commit()

    content = build_user_content(conn, build_retrieval(conn, now=NOW), date="2026-08-04", now=NOW)
    assert "key facts:" not in content
    assert "caveat:" not in content


# ── the item format: fact and judgement are separate FIELDS ──────────


def _persisted_brief(conn: db.Conn, data: dict, retrieval, *, date="2026-08-04", ids=None):
    """Persist one brief.

    `ids` defaults to a FRESH `SeededIdGen`, which mints the same id every call
    -- fine for a test that writes one brief, wrong for a test that writes two.
    `SeededIdGen` is a per-instance counter, so a caller writing several briefs
    must pass ONE generator, exactly as a real run shares one `RunContext`.
    """
    from venona.pipeline.brief import BriefReport, _persist
    from venona.pipeline.types import FrozenClock, RunContext, SeededIdGen

    report = BriefReport()
    ctx = RunContext(
        run_id="run:t",
        db=conn,
        llm=None,
        settings=get_settings(),
        clock=FrozenClock(),
        ids=ids or SeededIdGen(0),
    )
    brief_id = _persist(ctx, data=data, retrieval=retrieval, date=date, report=report)
    conn.commit()
    return brief_id, report


def _item_payload(cluster_id: str, **over) -> dict:
    base = {
        "cluster_id": cluster_id,
        "section": "COMMAND & CONTROL",
        "headline": "Army picks a vendor for the NGC2 common data layer",
        "state": "new",
        "state_note": "",
        "summary": "The Army selected a vendor. The award is worth $99.6M.",
        "why_it_matters": "It pushes inference to the edge.",
        "top_line": "",
    }
    base.update(over)
    return base


def test_summary_and_why_are_stored_as_separate_annotated_blocks(
    conn: db.Conn,
) -> None:
    """The whole point of the format.

    With one prose blob per section there was nowhere to put an opinion except
    inside the facts, which is why "summarise more, opine less" could never be
    won by prompting. Two fields makes it structural — and both are annotated,
    so acronyms and entity hovers work in the judgement as well as the facts.
    """
    _cluster(conn, "cluster:i1", score=80, tier="must_read")
    retrieval = build_retrieval(conn, now=NOW)
    brief_id, report = _persisted_brief(
        conn,
        {
            "headline": "A day",
            "items": [_item_payload(retrieval.clusters[0]["id"])],
            "mentioned_entity_ids": [],
            "unresolved_mentions": [],
        },
        retrieval,
    )

    roles = [
        r["role"]
        for r in conn.execute(
            "SELECT role FROM brief_blocks WHERE brief_id = ? ORDER BY ordinal", (brief_id,)
        )
    ]
    assert "summary" in roles and "why_it_matters" in roles
    assert report.items == 1

    blocks = conn.execute(
        "SELECT text, role FROM brief_blocks WHERE brief_id = ?", (brief_id,)
    ).fetchall()
    facts = next(b["text"] for b in blocks if b["role"] == "summary")
    why = next(b["text"] for b in blocks if b["role"] == "why_it_matters")
    assert "$99.6M" in facts
    assert "edge" in why
    assert "edge" not in facts, "judgement must not leak into the factual half"


@pytest.mark.negative
def test_an_item_for_an_unknown_cluster_is_dropped(conn: db.Conn) -> None:
    """An invented `cluster_id` would produce an item with no evidence and no
    attribution — silently, because every other field would look fine."""
    _cluster(conn, "cluster:i2", score=80, tier="must_read")
    retrieval = build_retrieval(conn, now=NOW)
    brief_id, report = _persisted_brief(
        conn,
        {
            "headline": "A day",
            "items": [_item_payload("cluster:does-not-exist")],
            "mentioned_entity_ids": [],
            "unresolved_mentions": [],
        },
        retrieval,
    )
    assert report.items == 0
    assert not conn.execute("SELECT 1 FROM brief_items WHERE brief_id = ?", (brief_id,)).fetchone()


def test_at_most_three_items_reach_the_top_block(conn: db.Conn) -> None:
    """A "top 3" of six is not a top 3, and the schema cannot express
    "at most three across the array"."""
    for i in range(5):
        _cluster(conn, f"cluster:t{i}", score=80 - i, tier="must_read")
    retrieval = build_retrieval(conn, now=NOW)
    items = [
        _item_payload(row["id"], top_line=f"hook {n}")
        for n, row in enumerate(retrieval.clusters[:5])
    ]
    brief_id, _ = _persisted_brief(
        conn,
        {
            "headline": "A day",
            "items": items,
            "mentioned_entity_ids": [],
            "unresolved_mentions": [],
        },
        retrieval,
    )
    tops = conn.execute(
        "SELECT top_rank FROM brief_items WHERE brief_id = ? AND top_rank IS NOT NULL",
        (brief_id,),
    ).fetchall()
    assert len(tops) == 3
    assert sorted(t["top_rank"] for t in tops) == [1, 2, 3]


def test_the_top_block_is_never_empty(conn: db.Conn) -> None:
    """The floor, which is the half that was missing.

    `top_line` is per-item and may be empty, so a model reading "the ones that
    would change what the reader does this week" as a quality bar can leave
    every one blank — and it did, on a real two-item day, which shipped a brief
    with no top block at all. The rail drops its contents list silently and the
    reader cannot tell that from a rendering bug.

    The fallback is the item's own headline, promoted. Nothing is invented.
    """
    for i in range(4):
        _cluster(conn, f"cluster:e{i}", score=80 - i, tier="must_read")
    retrieval = build_retrieval(conn, now=NOW)
    brief_id, _ = _persisted_brief(
        conn,
        {
            "headline": "A day",
            # Every one blank, which is what the model actually returned.
            "items": [_item_payload(row["id"]) for row in retrieval.clusters[:4]],
            "mentioned_entity_ids": [],
            "unresolved_mentions": [],
        },
        retrieval,
    )
    tops = conn.execute(
        "SELECT top_rank, top_line FROM brief_items "
        "WHERE brief_id = ? AND top_rank IS NOT NULL ORDER BY top_rank",
        (brief_id,),
    ).fetchall()
    assert [t["top_rank"] for t in tops] == [1, 2, 3]
    assert all(t["top_line"] for t in tops), "a rank with no line renders nothing"


def test_a_thin_brief_tops_every_item_it_has(conn: db.Conn) -> None:
    """`min(3, n)`, not 3. Two items cannot make a top 3, and padding one out of
    nothing would be the invention the fallback exists to avoid — the rail
    labels the block from the count it is given, so "Top 2" is the honest
    heading."""
    for i in range(2):
        _cluster(conn, f"cluster:s{i}", score=80 - i, tier="must_read")
    retrieval = build_retrieval(conn, now=NOW)
    brief_id, _ = _persisted_brief(
        conn,
        {
            "headline": "A thin day",
            "items": [_item_payload(row["id"]) for row in retrieval.clusters[:2]],
            "mentioned_entity_ids": [],
            "unresolved_mentions": [],
        },
        retrieval,
    )
    tops = conn.execute(
        "SELECT top_rank FROM brief_items WHERE brief_id = ? AND top_rank IS NOT NULL",
        (brief_id,),
    ).fetchall()
    assert sorted(t["top_rank"] for t in tops) == [1, 2]


def test_the_model_top_lines_win_over_the_headline_fallback(
    conn: db.Conn,
) -> None:
    """The fallback fills the gap; it does not compete for the slot. A model
    that named one item still gets that item's own sentence, and the other two
    slots come from the front of the editorial order."""
    for i in range(4):
        _cluster(conn, f"cluster:m{i}", score=80 - i, tier="must_read")
    retrieval = build_retrieval(conn, now=NOW)
    rows = retrieval.clusters[:4]
    items = [_item_payload(row["id"]) for row in rows]
    # The LAST item is the only one the model chose.
    items[3] = _item_payload(rows[3]["id"], top_line="the one that matters")
    brief_id, _ = _persisted_brief(
        conn,
        {
            "headline": "A day",
            "items": items,
            "mentioned_entity_ids": [],
            "unresolved_mentions": [],
        },
        retrieval,
    )
    lines = conn.execute(
        "SELECT top_line FROM brief_items WHERE brief_id = ? AND top_rank IS NOT NULL "
        "ORDER BY top_rank",
        (brief_id,),
    ).fetchall()
    assert len(lines) == 3
    assert "the one that matters" in [row["top_line"] for row in lines]


def test_a_pre_item_brief_still_renders(conn: db.Conn) -> None:
    """`BriefSection.blocks` is retained for exactly this.

    Every brief written before the item format stores narrative blocks with a
    NULL `item_id`. Serving only `items` would have emptied the whole archive
    the moment the format changed — silently, because the brief row, the title
    and the source list would all still be there.
    """
    from venona.services.brief_service import get_brief, list_briefs

    # BUILT HERE, not borrowed from the seed fixture. This used to read
    # `summaries[0]` and assert `items == 0`, which held only because
    # `fixtures/seed/brief.md` happened to declare no items -- so the test was
    # really asserting a property of the fixture, and it broke the day the fixture
    # started seeding the item format it should have been seeding all along. A test
    # for the legacy path has to construct a legacy brief.
    summaries, _ = list_briefs(conn)
    assert summaries, "the seed fixture must provide a brief"
    seeded = get_brief(conn, summaries[0].id)
    assert seeded is not None

    conn.execute("DELETE FROM brief_items WHERE brief_id = ?", (summaries[0].id,))
    conn.commit()

    brief = get_brief(conn, summaries[0].id)
    assert brief is not None
    narrative = sum(len(s.blocks) for s in brief.sections)
    assert narrative > 0, "pre-item narrative blocks vanished from the wire format"
    assert sum(len(s.items) for s in brief.sections) == 0


def test_a_brief_with_both_shapes_serves_both(conn: db.Conn) -> None:
    """The MIXED case, which the seed fixture is now an example of.

    Nothing forbids a brief holding items and legacy blocks at once, and after the
    fixture grew items that is exactly what the seeded one is. Worth pinning: the
    two are assembled by separate loops in `get_brief`, and an early `return` in
    either would drop the other half with the brief still rendering.
    """
    from venona.services.brief_service import get_brief, list_briefs

    summaries, _ = list_briefs(conn)
    brief = get_brief(conn, summaries[0].id)
    assert brief is not None
    assert sum(len(s.items) for s in brief.sections) > 0, "the fixture's items vanished"
    assert sum(len(s.blocks) for s in brief.sections) > 0, "the fixture's blocks vanished"
    assert brief.top, "three top lines are declared in fixtures/seed/brief.md"


# ── one day, several briefs ──────────────────────────────────────────


def test_two_briefs_in_one_day_are_two_rows(conn: db.Conn) -> None:
    """THE regression. `brief_id` was `brief:{date}`, so a second run on the same
    UTC day was not a second brief -- it was an UPSERT over the first followed by
    DELETEs of its blocks, items and clusters. The live database held ONE row for
    six brief-producing runs: five briefs destroyed, and five `runs.brief_id`
    values left pointing at a later run's content.
    """
    from venona.pipeline.types import SeededIdGen

    ids = SeededIdGen(0)  # one generator, as a real run has one RunContext

    _cluster(conn, "cluster:d1", score=80, tier="must_read")
    first_id, _ = _persisted_brief(
        conn,
        {
            "headline": "Morning",
            "items": [_item_payload("cluster:d1")],
            "mentioned_entity_ids": [],
            "unresolved_mentions": [],
        },
        build_retrieval(conn, now=NOW),
        ids=ids,
    )

    _cluster(conn, "cluster:d2", score=70, tier="must_read")
    second_id, _ = _persisted_brief(
        conn,
        {
            "headline": "Evening",
            "items": [_item_payload("cluster:d2")],
            "mentioned_entity_ids": [],
            "unresolved_mentions": [],
        },
        build_retrieval(conn, now=NOW),
        ids=ids,
    )

    assert first_id != second_id, "same-day briefs must not share an id"

    rows = conn.execute(
        "SELECT id, title FROM briefs WHERE date = '2026-08-04' ORDER BY generated_at"
    ).fetchall()
    titles = {r["title"] for r in rows}
    assert {"Morning", "Evening"} <= titles, "the first brief was overwritten"

    # The earlier brief keeps its own children, not the later one's.
    for brief_id, expected in ((first_id, "cluster:d1"), (second_id, "cluster:d2")):
        covered = {
            r["cluster_id"]
            for r in conn.execute(
                "SELECT cluster_id FROM brief_clusters WHERE brief_id = ?", (brief_id,)
            )
        }
        assert covered == {expected}
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM brief_blocks WHERE brief_id = ?", (brief_id,)
        ).fetchone()["n"], f"{brief_id} lost its blocks"


def test_the_second_brief_of_a_day_excludes_the_firsts_clusters(
    conn: db.Conn,
) -> None:
    """The vacuous-exclusion defect, pinned.

    Retrieval exempted the brief being written via `bc.brief_id IS NOT ?`. With
    one brief row in the table that matched EVERY row, so the `NOT EXISTS` was
    vacuously true and nothing was ever excluded -- `clusters_used` of
    12, 8, 11, 12, 12, 12 across six briefs in a day, the same stories each time.
    """
    _cluster(conn, "cluster:x1", score=80, tier="must_read")
    _persisted_brief(
        conn,
        {
            "headline": "First",
            "items": [_item_payload("cluster:x1")],
            "mentioned_entity_ids": [],
            "unresolved_mentions": [],
        },
        build_retrieval(conn, now=NOW),
    )

    assert "cluster:x1" not in {c["id"] for c in build_retrieval(conn, now=NOW).clusters}


def test_recent_threads_sees_an_earlier_brief_from_the_same_day(
    conn: db.Conn,
) -> None:
    """`_recent_threads` keyed on `b.date < ?` -- strictly EARLIER DATES -- so a
    second brief in a day was blind to the first and would re-explain a thread
    the reader was told about hours ago. `follow_up` is unanswerable without it.
    """
    from venona.pipeline.brief import _recent_threads

    _cluster(conn, "cluster:t1", score=80, tier="must_read")
    _persisted_brief(
        conn,
        {
            "headline": "First",
            "items": [_item_payload("cluster:t1", headline="Army picks NGC2 data layer vendor")],
            "mentioned_entity_ids": [],
            "unresolved_mentions": [],
        },
        build_retrieval(conn, now=NOW),
    )

    # FrozenClock's `now` is the same instant `_persist` stamped, so a brief
    # written moments ago must fall inside the window rather than on the wrong
    # side of a second-precision boundary.
    from venona.pipeline.types import FrozenClock

    lines = _recent_threads(conn, now=FrozenClock().now())
    assert any("Army picks NGC2 data layer vendor" in line for line in lines)


def test_the_empty_retrieval_message_distinguishes_nothing_new(
    conn: db.Conn,
) -> None:
    """ "nothing analyzed yet -- run `venona analyze-run` first" sent the operator
    to the wrong command when the truth was that a brief already covered
    everything recent. With one brief per run that is the ORDINARY outcome of a
    second run in a day, not an error worth misdiagnosing."""
    from venona.pipeline.brief import _empty_retrieval_reason

    # NOT "run analyze-run". The seeded fixture's documents are 37 to 56
    # characters against a 600-character floor, so analyze would decline every
    # one of them and the command would do nothing. Naming a length is the
    # honest answer here; naming the command was the same misdirection this
    # test exists to prevent, one case further along.
    assert "long enough" in _empty_retrieval_reason(conn, now=NOW)

    _cluster(conn, "cluster:m1", score=80, tier="must_read")

    _persisted_brief(
        conn,
        {
            "headline": "Only",
            "items": [_item_payload("cluster:m1")],
            "mentioned_entity_ids": [],
            "unresolved_mentions": [],
        },
        build_retrieval(conn, now=NOW),
    )

    # Nothing left to retrieve, but the material plainly exists.
    assert not build_retrieval(conn, now=NOW).clusters
    assert _empty_retrieval_reason(conn, now=NOW) == "nothing new since the last brief"


def _empty_the_window(conn: db.Conn) -> None:
    """Push every seeded cluster out of the retrieval window.

    The `conn` fixture arrives with a seeded corpus whose clusters are in
    window and unanalysed, which is a perfectly good "run analyze" state and
    the wrong starting point for the three cases below.
    """
    conn.execute("UPDATE clusters SET published_at = ?", ("2026-01-01T00:00:00Z",))
    conn.commit()


def test_a_window_of_stubs_does_not_read_as_nothing_analyzed(conn: db.Conn) -> None:
    """The production case, 2026-08-30.

    Two runs reported "nothing analyzed yet -- run `venona analyze-run` first"
    while the window held exactly two stories, of 592 and 558 characters,
    against a 600-character floor. Analyze had run and had correctly declined
    to pay to score something the brief would refuse; the pipeline was working
    perfectly and the message named the one command that could not help.

    Confirmed against production before this was written: with a 36-hour
    window the counts were `awaiting_analysis 0, briefable 0, in_window 2`.
    """
    from venona.pipeline.brief import _empty_retrieval_reason

    _empty_the_window(conn)
    _cluster(conn, "cluster:stub", score=80, tier="must_read")
    stub = "Too short to brief. "
    conn.execute(
        "UPDATE documents SET text_nfc = ?, text_chars = ? WHERE id = ?",
        (stub, len(stub), "doc:cluster:stub"),
    )
    conn.commit()

    reason = _empty_retrieval_reason(conn, now=NOW)
    assert "long enough" in reason, reason
    assert "not been read" not in reason, reason


def test_material_waiting_on_analyze_still_says_so(conn: db.Conn) -> None:
    """The one branch where unread material IS the answer, kept distinct.

    This used to assert the string `analyze-run`. The DISTINCTION is what these
    two tests protect -- a thin day of stubs and a window nothing has read yet
    are different facts and must not collapse into one sentence -- and naming a
    command was only ever how the distinction happened to be spelled. These
    sentences render on a run page, where the reader is not at a shell.
    """
    from venona.pipeline.brief import _empty_retrieval_reason

    _empty_the_window(conn)
    _cluster(conn, "cluster:fresh", score=80, tier="must_read")
    conn.execute(
        "UPDATE clusters SET analyzed_at = NULL, relevance_overall = NULL WHERE id = ?",
        ("cluster:fresh",),
    )
    conn.commit()

    reason = _empty_retrieval_reason(conn, now=NOW)
    assert "not been read" in reason, reason
    assert "long enough" not in reason, reason


def test_no_empty_reason_names_a_command(conn: db.Conn) -> None:
    """None of the four branches sends a reader to a shell.

    They render as run-page prose, and `venona analyze-run` sat in one of them
    for months because nothing checked. `test_copy_style` holds this generally
    now; this pins the four sentences that motivated it.
    """
    from venona.pipeline.brief import _empty_retrieval_reason

    _empty_the_window(conn)
    for setup in (
        lambda: None,
        lambda: _cluster(conn, "cluster:fresh", score=80, tier="must_read"),
    ):
        setup()
        conn.commit()
        assert "venona " not in _empty_retrieval_reason(conn, now=NOW)


def test_an_empty_window_says_so(conn: db.Conn) -> None:
    """Nothing published recently is a quiet day, not a broken pipeline."""
    from venona.pipeline.brief import _empty_retrieval_reason

    _empty_the_window(conn)

    reason = _empty_retrieval_reason(conn, now=NOW)
    assert "nothing published" in reason, reason


# ── the brief's entity set, for the graph ────────────────────────────
#
# `brief_entities` is a SECOND reader of `brief_blocks.spans`, which C3 names as
# the one place the brief-to-entity association lives. The first test below is
# what licenses it to exist: it pins the new reader against `get_brief`, so the
# partition rule cannot drift into two different answers to the same question.

SEED_BRIEF = "brief:2026-08-04"


def _merge(conn: db.Conn, from_id: str, to_id: str) -> None:
    db.insert(
        conn,
        "entity_mappings",
        {
            "from_id": from_id,
            "to_id": to_id,
            "merge_id": f"merge:{from_id}->{to_id}",
            "active": 1,
            "reason": "test",
            "created_at": "n",
        },
    )


def test_brief_entities_match_the_brief_payload(conn: db.Conn) -> None:
    """Same ids, same order, one query instead of a whole document build."""
    from venona.services.brief_service import brief_entities, get_brief

    result = brief_entities(conn, SEED_BRIEF)
    brief = get_brief(conn, SEED_BRIEF)
    assert result is not None and brief is not None
    # `named` is the raw reading; `get_brief` does not resolve merges either.
    assert result.named == brief.entities


def test_brief_entities_exclude_glossary_and_undefined_refs(conn: db.Conn) -> None:
    from venona.services.brief_service import brief_entities

    result = brief_entities(conn, SEED_BRIEF)
    assert result is not None
    assert result.named, "the seed brief should name some entities"
    assert not [eid for eid in result.named if eid.startswith("gloss:")]


def test_brief_entities_resolve_merges(conn: db.Conn) -> None:
    """A brief written before a merge names the loser, and the loser is not a
    node in Neo4j. Sending it to the graph would silently drop the entity."""
    from venona.services.brief_service import brief_entities

    before = brief_entities(conn, SEED_BRIEF)
    assert before is not None
    loser = before.named[0]

    conn.execute(
        "INSERT INTO entities (id, type, name, canonical_key, status, first_seen, "
        "last_seen, created_at) VALUES ('company:survivor','Company','Survivor',"
        "'survivor','active','n','n','n')"
    )
    _merge(conn, loser, "company:survivor")

    after = brief_entities(conn, SEED_BRIEF)
    assert after is not None
    assert loser in after.named, "the brief still SAYS what it said"
    assert loser not in after.resolved
    assert "company:survivor" in after.resolved


def test_a_merge_onto_an_already_named_entity_does_not_duplicate_it(
    conn: db.Conn,
) -> None:
    from venona.services.brief_service import brief_entities

    before = brief_entities(conn, SEED_BRIEF)
    assert before is not None
    loser, survivor = before.named[0], before.named[1]
    _merge(conn, loser, survivor)

    after = brief_entities(conn, SEED_BRIEF)
    assert after is not None
    assert len(after.resolved) == len(before.resolved) - 1
    assert after.resolved.count(survivor) == 1


def test_brief_entities_follow_a_merge_chain(conn: db.Conn) -> None:
    """A merged into B, later B merged into C. `resolve_mappings` walks to a
    fixed point and so must this."""
    from venona.services.brief_service import brief_entities

    before = brief_entities(conn, SEED_BRIEF)
    assert before is not None
    a = before.named[0]

    for eid in ("company:mid", "company:end"):
        conn.execute(
            "INSERT INTO entities (id, type, name, canonical_key, status, first_seen, "
            "last_seen, created_at) VALUES (?,'Company',?,?,'active','n','n','n')",
            (eid, eid, eid),
        )
    _merge(conn, a, "company:mid")
    _merge(conn, "company:mid", "company:end")

    after = brief_entities(conn, SEED_BRIEF)
    assert after is not None
    assert "company:end" in after.resolved
    assert "company:mid" not in after.resolved


def test_brief_entities_preserve_document_order(conn: db.Conn) -> None:
    """Document order is what makes the id list readable in a diff, and it is
    the order `get_brief` promises."""
    from venona.services.brief_service import brief_entities

    result = brief_entities(conn, SEED_BRIEF)
    assert result is not None

    order: list[str] = []
    seen: set[str] = set()
    for row in conn.execute(
        "SELECT spans FROM brief_blocks WHERE brief_id = ? ORDER BY section, ordinal",
        (SEED_BRIEF,),
    ):
        for span in db.jload(row["spans"], []):
            if span["ref"] in seen:
                continue
            seen.add(span["ref"])
            if span["kind"] == "entity":
                order.append(span["ref"])
    assert result.named == order


@pytest.mark.negative
def test_brief_entities_for_an_unknown_brief_is_none(conn: db.Conn) -> None:
    """`None`, not `[]`. A brief that does not exist and a brief that mentions
    nothing are different answers, and the router turns one of them into a 404."""
    from venona.services.brief_service import brief_entities

    assert brief_entities(conn, "brief:1999-01-01:nope") is None


# ── render order (the `rowid` -> `ord` port) ──────────────────────────


def test_items_render_in_editorial_order(conn: db.Conn) -> None:
    """⭐ The failure this cannot be allowed to have, because it renders fine.

    `brief_service` read render order as `ORDER BY i.rowid` -- insertion order,
    with no ordering column on the table. Postgres has no rowid, so the port added
    `brief_items.ord` and `_persist` writes the editorial position into it.

    Adding the column is not the same as writing it. If `_persist` stops writing
    `ord`, every row defaults to 0, the ORDER BY becomes a no-op, and the brief
    renders its items in whatever order the heap returns them -- correct-looking
    prose in the wrong sequence, which no other assertion in this file would see.

    The rows are INSERTED in reverse so heap order and editorial order disagree.
    Asserting against a database that happens to return insertion order would pass
    with no ORDER BY at all.
    """
    from venona.services import brief_service

    db.upsert(
        conn,
        "briefs",
        {
            "id": "brief:order",
            "date": "2026-08-17",
            "title": "Order",
            "generated_at": "2026-08-17T06:00:00.000Z",
            "created_at": "2026-08-17T06:00:00.000Z",
        },
        key=["id"],
    )
    headlines = ["first item", "second item", "third item"]
    for position, headline in reversed(list(enumerate(headlines, start=1))):
        db.upsert(
            conn,
            "brief_items",
            {
                "id": f"item:brief:order:i{position:02d}",
                "brief_id": "brief:order",
                "section": "COMMAND & CONTROL",
                "rank": position,
                "headline": headline,
                "state": "new",
                "ord": position,
                "created_at": "2026-08-17T06:00:00.000Z",
            },
            key=["id"],
        )

    brief = brief_service.get_brief(conn, "brief:order")
    rendered = [item.headline for section in brief.sections for item in section.items]
    assert rendered == headlines, (
        f"items must render in editorial order; got {rendered!r}. Is `_persist` writing `ord`?"
    )


def test_title_and_headlines_carry_annotated_runs(conn: db.Conn) -> None:
    """Titles are annotated at READ time against the current index, so the
    whole archive carries hoverable mentions without a stored span being
    rewritten. The runs must reassemble to the exact stored strings -- the
    same C3 invariant the body blocks hold -- and the seed title names NGC2,
    so an empty result would mean the annotation silently stopped running."""
    from venona.services.brief_service import get_brief

    brief = get_brief(conn, SEED_BRIEF)
    assert brief is not None

    assert "".join(run.t for run in brief.title_runs) == brief.title
    assert any(run.r for run in brief.title_runs), "no reference found in the seed title"

    for section in brief.sections:
        for item in section.items:
            assert "".join(run.t for run in item.headline_runs) == item.headline


def test_a_failed_persist_leaves_no_claim_on_its_clusters(
    conn: db.Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⭐ THE PHANTOM BRIEF, and the reason it is worth a transaction rather than
    a retry.

    `brief_clusters` is written before the items and blocks, and `build_retrieval`
    excludes any cluster some brief already claimed. So a process that dies
    between the two writes leaves a brief with no content that has nonetheless
    consumed its stories — and they are never offered to another brief again.
    Nothing reports it: the run is `failed`, which reads as "try again tomorrow",
    and tomorrow's brief is quietly missing a day of coverage.
    """
    from venona.pipeline import brief as brief_module

    _cluster(conn, "cluster:atomic", score=80, tier="must_read")
    retrieval = build_retrieval(conn, now=NOW)
    assert "cluster:atomic" in {c["id"] for c in retrieval.clusters}

    def _count(table: str) -> int:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608

    # Against a baseline, not zero: the fixture ships a seeded brief.
    before = {table: _count(table) for table in ("briefs", "brief_clusters")}
    original = brief_module.db.insert

    def die_writing_items(target, table, row):
        if table == "brief_items":
            raise RuntimeError("the process died mid-write")
        return original(target, table, row)

    monkeypatch.setattr(brief_module.db, "insert", die_writing_items)

    with pytest.raises(RuntimeError):
        _persisted_brief(
            conn,
            {
                "headline": "A day",
                "items": [_item_payload("cluster:atomic")],
                "mentioned_entity_ids": [],
                "unresolved_mentions": [],
            },
            retrieval,
        )

    monkeypatch.undo()
    assert _count("briefs") == before["briefs"], "a brief row survived a failed persist"
    assert _count("brief_clusters") == before["brief_clusters"], (
        "the brief kept its claim on clusters it never rendered"
    )
    assert "cluster:atomic" in {c["id"] for c in build_retrieval(conn, now=NOW).clusters}, (
        "the cluster is still claimed by a brief that does not exist"
    )
