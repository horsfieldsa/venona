"""Personal relevance adjusts outcomes, offline and for free.

The feature's whole claim (ADR 0158): a reader's emphasis and follows change
what leads their reading, with zero model calls, because everything personal
is pure Python over the axis scores analyze already stored. These tests hold
that claim end to end at the service seam -- reorder on an emphasis edit,
surface-and-group on a follow, fall back on rubric drift -- against the real
repo rubric, so a rubric edit that silently breaks personalization fails
here first.
"""

from __future__ import annotations

from venona.db import core as db
from venona.db.repositories import relevance as repo
from venona.llm.schemas import axis_weights, tiers
from venona.pipeline.types import MIN_BRIEFABLE_CHARS
from venona.services import personal_brief, user_service
from venona.services import personal_relevance as pr
from venona.settings import get_settings

NOW = "2026-08-25T12:00:00.000Z"
RECENT = "2026-08-25T09:00:00Z"
READER = "user:reader-test"


def _reader(conn: db.Conn) -> None:
    user_service.create(
        conn, user_id=READER, email="reader-test@example.com", role="reader", now=NOW
    )


def _cluster(conn: db.Conn, cid: str, *, axes: dict[str, int], title: str = "Headline") -> None:
    body = "The service awarded new work on the program this week. " * (
        MIN_BRIEFABLE_CHARS // 50 + 2
    )
    conn.execute(
        "INSERT INTO documents (id, url, url_key, title, text_nfc, text_chars, "
        "content_hash, cluster_id, published_at, fetched_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,'n','n')",
        (
            f"doc:{cid}",
            f"https://e.com/{cid}",
            f"https://e.com/{cid}",
            title,
            body,
            len(body),
            cid,
            cid,
            RECENT,
        ),
    )
    db.upsert(
        conn,
        "clusters",
        {
            "id": cid,
            "canonical_doc_id": f"doc:{cid}",
            "title": title,
            "published_at": RECENT,
            "relevance_overall": 50,
            "tier": "must_read",
            "relevance_note": "why it matters",
            "so_what": "consequence",
            "axes": db.jdump({k: {"score": v, "why": "w"} for k, v in axes.items()}),
            "analyzed_at": NOW,
            "first_seen": "n",
            "last_seen": "n",
        },
        key=["id"],
    )


def _order(payload: dict) -> list[str]:
    return [item["cluster_id"] for item in payload["items"]]


def test_an_emphasis_edit_reorders_the_reading(conn: db.Conn) -> None:
    """The core adjustment claim: the same two stored clusters swap places
    when the reader raises one kind of story and quiets the other."""
    _reader(conn)
    _cluster(conn, "cluster:money", axes={"budget_procurement": 5})
    _cluster(conn, "cluster:tech", axes={"technology_signal": 5})
    settings = get_settings()

    untuned = personal_brief.for_you(conn, user_id=READER, settings=settings, now_iso=NOW)
    # Instance weights rank the technology axis above money, so the untuned
    # reading leads with the tech story.
    assert _order(untuned)[0] == "cluster:tech"
    assert untuned["tailored"] is False

    repo.upsert_settings(
        conn,
        user_id=READER,
        emphasis=db.jdump({"budget_procurement": "more", "technology_signal": "less"}),
        now=NOW,
    )
    tuned = personal_brief.for_you(conn, user_id=READER, settings=settings, now_iso=NOW)
    assert _order(tuned)[0] == "cluster:money"
    assert tuned["tailored"] is True
    # The word on the card moves with the reader, not just the order.
    by_id = {item["cluster_id"]: item for item in tuned["items"]}
    assert by_id["cluster:money"]["overall"] > by_id["cluster:tech"]["overall"]


def test_a_followed_name_tops_the_reading_whatever_it_scores(conn: db.Conn) -> None:
    """Follows GROUP the reading rather than boosting the number: a story
    that scores below the noise floor still surfaces, first, because the
    reader asked for that name by hand."""
    _reader(conn)
    _cluster(conn, "cluster:big", axes={"technology_signal": 5})
    _cluster(conn, "cluster:quiet", axes={})
    conn.execute(
        "INSERT INTO entities (id, type, name, canonical_key, status, relevance, "
        "mention_count, source_count, first_seen, last_seen, created_at) "
        "VALUES ('org:niche', 'Company', 'Niche Systems', 'niche systems', "
        "'active', 0, 1, 1, 'n', 'n', 'n')"
    )
    conn.execute(
        "INSERT INTO assertions (id, run_id, article_id, cluster_id, kind, "
        "entity_id_raw, evidence_quote, confidence, method, created_at) "
        "VALUES ('assert:1', 'run:t', 'doc:cluster:quiet', 'cluster:quiet', "
        "'entity', 'org:niche', 'quote', 0.9, 'model', 'n')"
    )
    repo.add_follow(conn, user_id=READER, entity_id="org:niche", now=NOW)

    payload = personal_brief.for_you(conn, user_id=READER, settings=get_settings(), now_iso=NOW)
    assert _order(payload)[0] == "cluster:quiet"
    assert payload["items"][0]["follows"] == ["Niche Systems"]


def test_weights_renormalize_and_cuts_scale() -> None:
    """Derived weights always sum to 1.0 (the 0-100 scale survives any
    emphasis), and the tier cuts track what a story can actually reach --
    ADR 0047's unreachable-cut bug, prevented structurally."""
    base = axis_weights()
    tuned = pr.derive_weights({"budget_procurement": "more"})
    assert abs(sum(tuned.values()) - 1.0) < 1e-9
    assert tuned["budget_procurement"] > base["budget_procurement"]

    # Untouched emphasis is exactly the instance: same weights, same cuts.
    assert pr.derive_weights({}) == base
    assert pr.scaled_cuts(base) == tiers()

    # Concentrating emphasis raises what one story can reach, so the cuts
    # rise with it rather than letting everything read as must-read.
    concentrated = pr.derive_weights(
        {axis: ("more" if axis == "technology_signal" else "less") for axis in base}
    )
    assert pr.scaled_cuts(concentrated)["must_read"] > tiers()["must_read"]


def test_rubric_drift_falls_back_to_instance_defaults(conn: db.Conn) -> None:
    """An emphasis stored against an axis the rubric dropped must not be
    silently renormalized into something the reader never chose."""
    assert pr.is_drifted({"axis_that_left": "more"}) is True
    _reader(conn)
    repo.upsert_settings(
        conn, user_id=READER, emphasis=db.jdump({"axis_that_left": "more"}), now=NOW
    )
    state = personal_brief.reader_state(conn, user_id=READER)
    assert state["drifted"] is True
    assert state["weights"] == axis_weights()


def test_inputs_hash_moves_with_the_inputs() -> None:
    """The staleness key: an emphasis or follow edit regenerates the personal
    brief at most once, an unchanged revisit never pays again."""
    base = pr.inputs_hash({}, [], "profile-a")
    assert pr.inputs_hash({}, [], "profile-a") == base
    assert pr.inputs_hash({"technology_signal": "more"}, [], "profile-a") != base
    assert pr.inputs_hash({}, ["org:x"], "profile-a") != base
    assert pr.inputs_hash({}, [], "profile-b") != base


def test_emphasis_validation_speaks_to_a_reader() -> None:
    assert pr.emphasis_problem({"technology_signal": "more"}) is None
    assert "not something Venona scores" in (pr.emphasis_problem({"nope": "more"}) or "")
    assert "more, normal or less" in (pr.emphasis_problem({"technology_signal": "loud"}) or "")


# ── the auto-generation trigger ──────────────────────────────────────────
#
# `auto_due` is the one place the app spends without being asked, so these
# read as a truth table rather than as scenarios: every clause is a brake, and
# a test per brake is what stops a later edit from quietly removing one. The
# expensive direction is a false TRUE, so each case below starts from a
# reader who WOULD fire and takes exactly one thing away.


def _tailored_reader_with_reading(conn: db.Conn) -> None:
    """A reader who is one shared brief away from being auto-due."""
    _reader(conn)
    _cluster(conn, "cluster:money", axes={"budget_procurement": 5})
    repo.upsert_settings(
        conn, user_id=READER, emphasis=db.jdump({"budget_procurement": "more"}), now=NOW
    )


def _ms(stamp: str) -> str:
    """Second-precision fixture stamps in the millisecond format production
    writes. The comparison is lexicographic, and `Z` sorts AFTER `.`, so a
    document stamped `11:00:00Z` reads as newer than a brief stamped
    `11:00:00.500Z` -- an assertion about formatting, dressed as one about
    currency."""
    return stamp if "." in stamp else stamp.replace("Z", ".000Z")


def _shared_brief(conn: db.Conn, *, generated_at: str, brief_id: str = "brief:one") -> None:
    generated_at = _ms(generated_at)
    db.upsert(
        conn,
        "briefs",
        {
            "id": brief_id,
            "date": generated_at[:10],
            "title": "Daily",
            "generated_at": generated_at,
            "created_at": generated_at,
        },
        key=["id"],
    )


def _personal_brief(conn: db.Conn, *, generated_at: str, date: str | None = None) -> None:
    date = date or generated_at[:10]
    generated_at = _ms(generated_at)
    db.upsert(
        conn,
        "personal_briefs",
        {
            "id": f"pbrief:{generated_at}",
            "user_id": READER,
            "date": date,
            "inputs_hash": "whatever",
            "title": "Yours",
            "generated_at": generated_at,
            "created_at": generated_at,
        },
        key=["id"],
    )


def _run_row(conn: db.Conn, *, run_id: str, started_at: str, user_id: str = READER) -> None:
    started_at = _ms(started_at)
    db.upsert(
        conn,
        "runs",
        {
            "id": run_id,
            "kind": "personal_brief",
            "status": "failed",
            "params": db.jdump({"user_id": user_id}),
            "cost_usd": 0.0,
            "started_at": started_at,
        },
        key=["id"],
    )


def _due(conn: db.Conn, **kwargs) -> bool:
    return personal_brief.for_you(
        conn, user_id=READER, settings=get_settings(), now_iso=NOW, **kwargs
    )["auto_due"]


def test_auto_due_when_a_tailored_reader_has_new_reading(conn: db.Conn) -> None:
    """The case the feature exists for: reading landed, this reader has no
    brief for today, so opening the page writes one without being asked."""
    _tailored_reader_with_reading(conn)
    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z")
    assert _due(conn) is True


def test_auto_due_is_false_for_an_untailored_reader(conn: db.Conn) -> None:
    """No emphasis is no instruction to write from, and the derived list is
    already that reader's whole personal surface."""
    _reader(conn)
    _cluster(conn, "cluster:money", axes={"budget_procurement": 5})
    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z")
    assert _due(conn) is False


def test_auto_due_is_false_once_this_editions_document_exists(conn: db.Conn) -> None:
    """A document written AFTER the newest shared brief covers it, and there
    is nothing to add."""
    _tailored_reader_with_reading(conn)
    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z")
    _personal_brief(conn, generated_at="2026-08-25T11:30:00Z")
    assert _due(conn) is False


def test_auto_due_fires_when_a_newer_edition_lands_on_a_day_already_written(
    conn: db.Conn,
) -> None:
    """THE DEFECT THIS TABLE EXISTS FOR, taken from production.

    A reader who opens the app in the evening Pacific is past UTC midnight, so
    their document is stamped with the coming day and written from the reading
    that is already there. The daily then lands at 13:25 with a day of new
    reading in it. Keyed to the date, that document was "already written" and
    the reader waited another eleven hours; keyed to the edition, the newer
    reading is what decides. Delete `_covers_latest_edition` and this fails.
    """
    _tailored_reader_with_reading(conn)
    _shared_brief(conn, generated_at="2026-08-24T13:25:00Z")
    _personal_brief(conn, generated_at="2026-08-25T00:05:00Z")
    assert _due(conn) is False

    _shared_brief(conn, generated_at="2026-08-25T11:25:00Z", brief_id="brief:two")
    payload = personal_brief.for_you(conn, user_id=READER, settings=get_settings(), now_iso=NOW)
    assert payload["auto_due"] is True
    # The reader still sees the document they have while the new one is
    # written; it is behind the reading, not absent.
    assert payload["brief"] is not None
    assert payload["outdated"] is True


def test_auto_due_tries_at_most_once_per_edition_even_when_it_failed(conn: db.Conn) -> None:
    """THE COST CLAUSE. `count_personal_brief_runs` counts runs, not
    documents, so a model returning nothing usable still counts. Without this
    a quietly failing model would spend the reader's whole daily allowance
    across three page loads and leave them nothing to retry with.

    Per edition rather than per day: a day was the period that made the
    reader's brief trail the shared one by half a day, and the reason for the
    brake was never the calendar."""
    _tailored_reader_with_reading(conn)
    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z")
    assert _due(conn) is True

    # A run that started against THIS edition and wrote no document: the reader
    # still has rewrites left, and auto still refuses to spend another.
    _run_row(conn, run_id="run:failed", started_at="2026-08-25T11:05:00Z")
    payload = personal_brief.for_you(conn, user_id=READER, settings=get_settings(), now_iso=NOW)
    assert payload["auto_due"] is False
    assert payload["brief"] is None
    assert payload["generations_left"] > 0


def test_a_run_that_failed_against_older_reading_does_not_brake_this_edition(
    conn: db.Conn,
) -> None:
    """The converse of the cost clause, and what keeps the brake from becoming
    the daily one again by the back door: an attempt made before this reading
    landed has not been made against it."""
    _tailored_reader_with_reading(conn)
    _shared_brief(conn, generated_at="2026-08-25T02:00:00Z")
    _run_row(conn, run_id="run:failed-early", started_at="2026-08-25T02:05:00Z")
    assert _due(conn) is False

    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z", brief_id="brief:two")
    assert _due(conn) is True


def test_auto_due_is_false_at_the_daily_cap(conn: db.Conn) -> None:
    """The client re-checks whenever new reading lands, so an `auto_due` that
    outlived the cap would POST into the rate limiter on every check, forever,
    and the refusal is swallowed by design."""
    _tailored_reader_with_reading(conn)
    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z")
    for n in range(get_settings().llm.personal_briefs_per_day):
        _run_row(conn, run_id=f"run:used-{n}", started_at=f"2026-08-25T0{n}:00:00Z")
    payload = personal_brief.for_you(conn, user_id=READER, settings=get_settings(), now_iso=NOW)
    assert payload["generations_left"] == 0
    assert payload["auto_due"] is False


def test_auto_due_ignores_another_readers_runs(conn: db.Conn) -> None:
    """The cap query matches `params` by a quoted id. A different reader's run
    started today must not count against this one."""
    _tailored_reader_with_reading(conn)
    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z")
    user_service.create(
        conn, user_id="user:other", email="other@example.com", role="reader", now=NOW
    )
    _run_row(conn, run_id="run:theirs", started_at="2026-08-25T11:05:00Z", user_id="user:other")
    assert _due(conn) is True


def test_auto_due_is_false_with_nothing_in_the_window(conn: db.Conn) -> None:
    _reader(conn)
    repo.upsert_settings(
        conn, user_id=READER, emphasis=db.jdump({"budget_procurement": "more"}), now=NOW
    )
    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z")
    assert _due(conn) is False


def test_auto_due_is_false_on_an_instance_that_has_never_run(conn: db.Conn) -> None:
    """No shared brief is no reading to re-roll, whatever the window holds.

    The seed ships one brief, so an empty `briefs` has to be arranged rather
    than assumed -- and it is worth arranging, because this is the state a
    fresh install sits in until its first daily lands, and the reader reaching
    it is the one being welcomed.
    """
    _tailored_reader_with_reading(conn)
    conn.execute("DELETE FROM brief_clusters")
    conn.execute("DELETE FROM briefs")
    assert _due(conn) is False


def test_auto_due_is_false_at_midnight_without_new_reading(conn: db.Conn) -> None:
    """THE MIDNIGHT CLAUSE. A new UTC date is not new reading: the window holds
    exactly what yesterday's brief was written from, so firing would buy a
    near-duplicate. Delete the `generated_at` comparison and this is the test
    that fails."""
    _tailored_reader_with_reading(conn)
    _shared_brief(conn, generated_at="2026-08-24T11:00:00Z")
    _personal_brief(conn, generated_at="2026-08-24T11:30:00Z", date="2026-08-24")
    # It is now the 25th and this reader's document was written on the 24th.
    # Nothing has been read since, so it still must not spend -- and the
    # document is still theirs to read.
    payload = personal_brief.for_you(conn, user_id=READER, settings=get_settings(), now_iso=NOW)
    assert payload["auto_due"] is False
    assert payload["brief"] is not None
    assert payload["outdated"] is False

    # The daily lands. Now there is something to write about.
    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z", brief_id="brief:two")
    assert _due(conn) is True


def test_auto_due_is_false_while_one_is_already_in_flight(conn: db.Conn) -> None:
    """`generating` comes from the launcher's own coalescing lookup, so "is one
    running" and "would a POST start a new one" cannot disagree."""
    _tailored_reader_with_reading(conn)
    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z")

    class _Registry:
        def __init__(self, hit: object | None) -> None:
            self.hit = hit
            self.asked: list[tuple[str, dict]] = []

        def duplicate_of(self, kind: str, params: dict) -> object | None:
            self.asked.append((kind, params))
            return self.hit

    idle = _Registry(None)
    assert _due(conn, registry=idle) is True
    # Asked about THIS reader's own run, by the same key the launcher folds on.
    assert idle.asked == [("personal_brief", {"user_id": READER})]

    busy = _Registry(object())
    payload = personal_brief.for_you(
        conn, user_id=READER, settings=get_settings(), now_iso=NOW, registry=busy
    )
    assert payload["generating"] is True
    assert payload["auto_due"] is False


def test_covers_latest_edition_is_the_one_definition_of_current() -> None:
    """The predicate the whole lane turns on, including the run's own no-op
    check, which is the third caller and the one that told a reader their
    brief was already written while it sat half a day behind the reading.

    A reader with no document is never covered, and an instance with no shared
    brief has nothing for theirs to fall behind."""
    covers = personal_brief._covers_latest_edition
    shared = "2026-08-25T11:00:00.000Z"
    assert covers(None, latest_shared=shared) is False
    assert covers({"generated_at": "2026-08-25T10:00:00.000Z"}, latest_shared=None) is True
    assert covers({"generated_at": "2026-08-25T11:30:00.000Z"}, latest_shared=shared) is True
    assert covers({"generated_at": "2026-08-25T10:30:00.000Z"}, latest_shared=shared) is False
    # The comparison is strict, so an exact tie is not covered. A document is
    # written after the reading it derives from, never in the same
    # millisecond, so the tie is degenerate; strict keeps the honest direction
    # for the one that is not, which is a document written just BEFORE.
    assert covers({"generated_at": shared}, latest_shared=shared) is False


def test_outdated_is_the_reading_and_stale_is_the_reader(conn: db.Conn) -> None:
    """Two different questions, so two different fields. `stale` is "you moved
    what matters since this was written" and offers a rewrite; `outdated` is
    "reading has landed that this was written before" and is what auto acts
    on. One flag for both would make a reader who edited their emphasis look
    like new reading, and spend."""
    _tailored_reader_with_reading(conn)
    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z")
    _personal_brief(conn, generated_at="2026-08-25T11:30:00Z")
    payload = personal_brief.for_you(conn, user_id=READER, settings=get_settings(), now_iso=NOW)
    # The fixture's `inputs_hash` is not the reader's, so this document is
    # stale by inputs while covering the newest reading.
    assert payload["stale"] is True
    assert payload["outdated"] is False

    _shared_brief(conn, generated_at="2026-08-25T11:45:00Z", brief_id="brief:two")
    payload = personal_brief.for_you(conn, user_id=READER, settings=get_settings(), now_iso=NOW)
    assert payload["outdated"] is True


def test_generating_is_false_without_a_registry(conn: db.Conn) -> None:
    """Every offline caller builds this without a running API. False is the
    safe direction: it never claims a run is in flight when none is."""
    _tailored_reader_with_reading(conn)
    _shared_brief(conn, generated_at="2026-08-25T11:00:00Z")
    payload = personal_brief.for_you(conn, user_id=READER, settings=get_settings(), now_iso=NOW)
    assert payload["generating"] is False


# ── the warm pass has a population ───────────────────────────────────────


def test_touch_daily_stamps_once_a_day_and_the_warm_pass_finds_them(conn: db.Conn) -> None:
    """The bug this repairs: `recently_seen` filters on `last_seen_at`, which
    the sign-in callback alone cannot keep current -- a long-lived session
    reads for weeks without a second callback, and with auth off there is no
    callback at all. A new row starts NULL, so the warm population was empty.
    """
    from venona.db.repositories import users as users_repo

    _reader(conn)
    assert users_repo.recently_seen(conn, since="2026-08-20T00:00:00Z") == []

    written = users_repo.touch_daily(
        conn, user_id=READER, now="2026-08-25T09:00:00Z", today="2026-08-25"
    )
    assert written == 1
    assert [r["id"] for r in users_repo.recently_seen(conn, since="2026-08-20T00:00:00Z")] == [
        READER
    ]

    # Same day, later: the WHERE clause refuses, so the write happens at most
    # once per reader per day however many times the probe runs.
    again = users_repo.touch_daily(
        conn, user_id=READER, now="2026-08-25T18:00:00Z", today="2026-08-25"
    )
    assert again == 0

    # A new day moves it, which is what makes the warm window mean days of USE.
    assert (
        users_repo.touch_daily(conn, user_id=READER, now="2026-08-26T08:00:00Z", today="2026-08-26")
        == 1
    )
    # And the window still excludes a reader who has been away longer than it.
    assert users_repo.recently_seen(conn, since="2026-08-27T00:00:00Z") == []


def test_touch_daily_service_writes_once_per_process_per_day(conn: db.Conn) -> None:
    """The in-process guard keeps this off the hot path: the probe runs on
    every page load, and only the first of a day costs a round trip."""
    from venona.services import user_service as svc

    _reader(conn)
    svc._seen_today.clear()
    try:
        calls: list[str] = []
        original = svc.repo.touch_daily

        def counting(*args, **kwargs):
            calls.append(kwargs["today"])
            return original(*args, **kwargs)

        svc.repo.touch_daily = counting  # type: ignore[assignment]
        svc.touch_daily(conn, user_id=READER, now="2026-08-25T09:00:00Z")
        svc.touch_daily(conn, user_id=READER, now="2026-08-25T18:00:00Z")
        assert calls == ["2026-08-25"]
        svc.touch_daily(conn, user_id=READER, now="2026-08-26T08:00:00Z")
        assert calls == ["2026-08-25", "2026-08-26"]
    finally:
        svc.repo.touch_daily = original  # type: ignore[assignment]
        svc._seen_today.clear()


def test_touch_daily_never_fails_the_session_probe(conn: db.Conn) -> None:
    """It backs the probe `main.ts` awaits before it mounts. A database error
    here must not take the whole app down with it."""
    import psycopg
    from venona.services import user_service as svc

    _reader(conn)
    svc._seen_today.clear()
    original = svc.repo.touch_daily

    def boom(*args, **kwargs):
        raise psycopg.OperationalError("connection gone")

    try:
        svc.repo.touch_daily = boom  # type: ignore[assignment]
        svc.touch_daily(conn, user_id=READER, now="2026-08-25T09:00:00Z")
        # Not cached as done: the next probe retries rather than suppressing
        # the write for the rest of the day on a failure the database never
        # acknowledged.
        assert READER not in svc._seen_today
    finally:
        svc.repo.touch_daily = original  # type: ignore[assignment]
        svc._seen_today.clear()


# ── which shared briefs pull the readership along ────────────────────────


async def _fanout_calls(database_url: str, *, policy: str, scheduled: bool) -> list[str]:
    """Which readers the warm pass would launch for, under one policy."""
    settings = get_settings().model_copy(deep=True)
    settings.llm.personal_brief_fanout = policy
    # The pass opens its own connection, so it has to be pointed at the
    # throwaway database rather than the one `config/settings.yaml` names.
    settings.database_url_override = database_url
    started: list[str] = []

    original = personal_brief.start_generation

    async def record(request, *, settings, user_id, announce=False):  # noqa: ANN001
        # `announce` mirrors the real signature. The warm pass is the ONE caller
        # that passes it True -- a reader who pressed the button is watching the
        # page render and a message about it is noise -- so a double that
        # omitted it would make the pass raise, and the pass swallows per-reader
        # failures, so the test would have gone on reporting an empty fan-out
        # with the reason only in a log line.
        assert announce is True, "the warm pass is the case where nobody is watching"
        started.append(user_id)
        return {}

    personal_brief.start_generation = record  # type: ignore[assignment]
    try:
        await personal_brief.warm_recent_readers(
            None, settings=settings, now_iso=NOW, scheduled=scheduled
        )
    finally:
        personal_brief.start_generation = original  # type: ignore[assignment]
    return started


def test_the_fanout_policy_decides_which_briefs_warm_the_readership(
    conn: db.Conn, database_url: str
) -> None:
    """A brief written outside the schedule is still new reading, and whether
    that pulls every recent reader along is worth what it costs only on some
    instances. The switch is one setting read in one place, so the two callers
    do not each hold half the rule."""
    import asyncio

    from venona.db.repositories import users as users_repo

    _reader(conn)
    users_repo.touch_daily(conn, user_id=READER, now=RECENT, today=RECENT[:10])

    def run(policy: str, scheduled: bool) -> list[str]:
        return asyncio.run(_fanout_calls(database_url, policy=policy, scheduled=scheduled))

    assert run("every_brief", True) == [READER]
    assert run("every_brief", False) == [READER]
    assert run("scheduled_only", True) == [READER]
    assert run("scheduled_only", False) == []
    assert run("off", True) == []
    assert run("off", False) == []
