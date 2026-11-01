"""The personal brief's item machinery (ADR 0176).

Mirrors `tests/test_brief.py`'s key cases at the reader's scale, driving
`_persist_personal` and `render_brief` directly with a RunContext -- the same
seam that suite uses for `_persist`, so nothing here spends or records.
"""

from __future__ import annotations

from venona.db import core as db
from venona.db.repositories import relevance as repo
from venona.pipeline.types import FrozenClock, RunContext, SeededIdGen
from venona.services import personal_brief, user_service
from venona.settings import get_settings

NOW = "2026-08-25T12:00:00.000Z"
READER = "user:reader-items"

#: Two desks in `profile/brief_sections.yaml`'s declared order, so a test can
#: tell "the profile's order" apart from "the order the reader ranked them".
FIRST_DESK = "COMMAND & CONTROL"
LATER_DESK = "POLICY & ACQUISITION"


def _reader(conn: db.Conn, user_id: str = READER) -> None:
    user_service.create(
        conn, user_id=user_id, email=f"{user_id.split(':')[-1]}@example.com", role="reader", now=NOW
    )


def _cluster(conn: db.Conn, cid: str, *, title: str = "Headline") -> None:
    body = "The Army awarded a contract for command and control work. " * 20
    conn.execute(
        "INSERT INTO documents (id, url, url_key, title, publisher, text_nfc, text_chars, "
        "content_hash, cluster_id, published_at, fetched_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,'n','n')",
        (
            f"doc:{cid}",
            f"https://e.com/{cid}",
            f"https://e.com/{cid}",
            title,
            "Test Wire",
            body,
            len(body),
            cid,
            cid,
            "2026-08-25T09:00:00Z",
        ),
    )
    db.upsert(
        conn,
        "clusters",
        {
            "id": cid,
            "canonical_doc_id": f"doc:{cid}",
            "title": title,
            "relevance_overall": 50,
            "tier": "must_read",
            "axes": db.jdump({}),
            "analyzed_at": NOW,
            "first_seen": "n",
            "last_seen": "n",
        },
        key=["id"],
    )


def _item(cluster_id: str, **over) -> dict:
    base = {
        "cluster_id": cluster_id,
        "section": FIRST_DESK,
        "headline": "Army picks a vendor for the NGC2 common data layer",
        "state": "new",
        "state_note": "",
        "summary": "The Army selected a vendor. The award is worth $99.6M.",
        "why_it_matters": "It pushes inference to the edge.",
        "top_line": "",
    }
    base.update(over)
    return base


def _persist(
    conn: db.Conn,
    data: dict,
    supplied: list[str],
    *,
    user_id: str = READER,
    date: str = "2026-08-25",
    ids=None,
    source_ids: list[str] | None = None,
) -> str | None:
    ctx = RunContext(
        run_id="run:t",
        db=conn,
        llm=None,
        settings=get_settings(),
        clock=FrozenClock(),
        ids=ids or SeededIdGen(0),
    )
    brief_id = personal_brief._persist_personal(
        conn,
        ctx,
        run_id="run:t",
        user_id=user_id,
        date=date,
        inputs_hash="hash",
        data=data,
        supplied_ids=supplied,
        source_ids=source_ids if source_ids is not None else [f"doc:{c}" for c in supplied],
    )
    conn.commit()
    return brief_id


def test_summary_and_why_are_stored_as_separate_annotated_role_blocks(conn: db.Conn) -> None:
    """The whole point of the migration: fact and judgement are structural,
    exactly as in the shared brief."""
    _reader(conn)
    brief_id = _persist(
        conn,
        {"headline": "A day", "dek": "", "items": [_item("cluster:a")]},
        ["cluster:a"],
    )
    assert brief_id is not None
    roles = [
        r["role"]
        for r in conn.execute(
            "SELECT role FROM personal_brief_blocks WHERE brief_id = ? ORDER BY ordinal",
            (brief_id,),
        ).fetchall()
    ]
    assert roles == ["summary", "why_it_matters"]
    items = repo.personal_brief_items_for(conn, brief_id=brief_id)
    assert [i["ord"] for i in items] == [1]


def test_an_item_for_an_unknown_cluster_is_dropped(conn: db.Conn) -> None:
    _reader(conn)
    brief_id = _persist(
        conn,
        {
            "headline": "A day",
            "dek": "",
            "items": [_item("cluster:a"), _item("cluster:invented")],
        },
        ["cluster:a"],
    )
    items = repo.personal_brief_items_for(conn, brief_id=brief_id)
    assert [i["cluster_id"] for i in items] == ["cluster:a"]


def test_a_refused_result_leaves_the_prior_brief_intact(conn: db.Conn) -> None:
    """Validation precedes the delete: a hallucinated-ids response must not
    destroy the reader's existing document."""
    _reader(conn)
    first = _persist(
        conn, {"headline": "Day one", "dek": "", "items": [_item("cluster:a")]}, ["cluster:a"]
    )
    second = _persist(
        conn,
        {"headline": "Day one again", "dek": "", "items": [_item("cluster:invented")]},
        ["cluster:a"],
        ids=SeededIdGen(9),
    )
    assert second is None
    row = repo.latest_personal_brief(conn, user_id=READER)
    assert row is not None and row["id"] == first


def test_the_top_block_is_filled_to_min_three_and_model_lines_win(conn: db.Conn) -> None:
    _reader(conn)
    supplied = [f"cluster:{n}" for n in ("a", "b", "c", "d")]
    items = [_item(cid, headline=f"Headline {cid}") for cid in supplied]
    items[3]["top_line"] = "The one the model chose."
    brief_id = _persist(conn, {"headline": "A day", "dek": "", "items": items}, supplied)
    rows = repo.personal_brief_items_for(conn, brief_id=brief_id)
    tops = {r["ord"]: (r["top_rank"], r["top_line"]) for r in rows if r["top_rank"]}
    assert len(tops) == 3
    # The model's choice survives; the two fill slots promote the leading
    # headlines, exactly as the shared brief does.
    assert tops[4][1] == "The one the model chose."
    assert tops[1][1] == "Headline cluster:a"
    assert tops[2][1] == "Headline cluster:b"


def test_the_supplied_ranking_orders_the_document(conn: db.Conn) -> None:
    """Within a desk the reader's ranking is the order; the model's own
    ordering is not consulted."""
    _reader(conn)
    brief_id = _persist(
        conn,
        {
            "headline": "A day",
            "dek": "",
            # Model returns them backwards; the supplied order is b, a.
            "items": [_item("cluster:a"), _item("cluster:b")],
        },
        ["cluster:b", "cluster:a"],
    )
    rows = repo.personal_brief_items_for(conn, brief_id=brief_id)
    assert [r["cluster_id"] for r in rows] == ["cluster:b", "cluster:a"]


def test_desks_order_the_document_and_the_ranking_orders_each_desk(conn: db.Conn) -> None:
    """The shared brief's arrangement at the reader's scale: desks in the
    profile's declared order, the reader's ranking preserved inside each.

    The ranking here is deliberately at odds with the desk order -- the
    reader's top story is on the LATER desk -- so a document that merely kept
    the supplied order, or one that sorted desks alphabetically, both fail.
    """
    _reader(conn)
    supplied = ["cluster:a", "cluster:b", "cluster:c"]
    brief_id = _persist(
        conn,
        {
            "headline": "A day",
            "dek": "",
            "items": [
                _item("cluster:a", section=LATER_DESK),
                _item("cluster:b", section=FIRST_DESK),
                _item("cluster:c", section=LATER_DESK),
            ],
        },
        supplied,
    )
    rows = repo.personal_brief_items_for(conn, brief_id=brief_id)
    assert [(r["section"], r["cluster_id"]) for r in rows] == [
        (FIRST_DESK, "cluster:b"),
        (LATER_DESK, "cluster:a"),
        (LATER_DESK, "cluster:c"),
    ]


def test_the_top_block_names_the_highest_ranked_stories_after_the_regroup(
    conn: db.Conn,
) -> None:
    """WHICH three is settled before the desks move anything.

    The reader's top two sit on the later desk, so a top block chosen from the
    finished document would name the third- and fourth-ranked stories instead
    -- an accident of where the desks fall, not a judgement.
    """
    _reader(conn)
    supplied = [f"cluster:{n}" for n in ("a", "b", "c", "d")]
    brief_id = _persist(
        conn,
        {
            "headline": "A day",
            "dek": "",
            "items": [
                _item("cluster:a", section=LATER_DESK, headline="Ranked first"),
                _item("cluster:b", section=LATER_DESK, headline="Ranked second"),
                _item("cluster:c", section=FIRST_DESK, headline="Ranked third"),
                _item("cluster:d", section=FIRST_DESK, headline="Ranked fourth"),
            ],
        },
        supplied,
    )
    rows = repo.personal_brief_items_for(conn, brief_id=brief_id)
    tops = {r["cluster_id"] for r in rows if r["top_rank"]}
    assert tops == {"cluster:a", "cluster:b", "cluster:c"}
    # Ranked by where they land in the finished document: the rail is a
    # contents list and walks the page.
    assert [r["cluster_id"] for r in rows if r["top_rank"]] == [
        "cluster:c",
        "cluster:a",
        "cluster:b",
    ]


def test_recent_items_are_scoped_to_the_reader_and_exclude_the_day(conn: db.Conn) -> None:
    """The follow-up diff reads the reader's OWN history, minus the date being
    written -- the day's document is replaced in place, so today's items are
    about to stop existing."""
    other = "user:reader-other"
    _reader(conn)
    _reader(conn, other)
    _persist(
        conn,
        {"headline": "Yesterday", "dek": "", "items": [_item("cluster:y", headline="Mine, prior")]},
        ["cluster:y"],
        date="2026-08-24",
    )
    _persist(
        conn,
        {"headline": "Today", "dek": "", "items": [_item("cluster:t", headline="Mine, today")]},
        ["cluster:t"],
        date="2026-08-25",
        ids=SeededIdGen(5),
    )
    _persist(
        conn,
        {"headline": "Theirs", "dek": "", "items": [_item("cluster:o", headline="Someone else's")]},
        ["cluster:o"],
        user_id=other,
        date="2026-08-24",
        ids=SeededIdGen(7),
    )
    rows = repo.personal_recent_items(
        conn, user_id=READER, cutoff="0", exclude_date="2026-08-25", limit=25
    )
    assert [r["headline"] for r in rows] == ["Mine, prior"]


def test_regeneration_replaces_the_items_via_the_cascade(conn: db.Conn) -> None:
    _reader(conn)
    ids = SeededIdGen(0)
    first = _persist(
        conn, {"headline": "One", "dek": "", "items": [_item("cluster:a")]}, ["cluster:a"], ids=ids
    )
    _persist(
        conn, {"headline": "Two", "dek": "", "items": [_item("cluster:b")]}, ["cluster:b"], ids=ids
    )
    orphans = conn.execute(
        "SELECT COUNT(*) AS n FROM personal_brief_items WHERE brief_id = ?", (first,)
    ).fetchone()["n"]
    assert orphans == 0


def test_a_second_generation_on_one_day_replaces_the_days_document(conn: db.Conn) -> None:
    """New shared reading rewrites the reader's brief whenever it lands, so
    two generations on one date is the ordinary case rather than the odd one.
    The row is keyed by day and replaced in place, and `latest_personal_brief`
    is what the reader is then shown."""
    _reader(conn)
    ids = SeededIdGen(0)
    _persist(
        conn,
        {"headline": "Morning", "dek": "", "items": [_item("cluster:a")]},
        ["cluster:a"],
        ids=ids,
    )
    second = _persist(
        conn,
        {"headline": "Afternoon", "dek": "", "items": [_item("cluster:b")]},
        ["cluster:b"],
        ids=ids,
    )
    rows = conn.execute(
        "SELECT id FROM personal_briefs WHERE user_id = ? AND date = ?", (READER, "2026-08-25")
    ).fetchall()
    assert [r["id"] for r in rows] == [second]
    assert repo.latest_personal_brief(conn, user_id=READER)["title"] == "Afternoon"


def test_render_carries_the_desks_attribution_and_top(conn: db.Conn) -> None:
    _reader(conn)
    _cluster(conn, "cluster:a")
    brief_id = _persist(
        conn, {"headline": "A day", "dek": "", "items": [_item("cluster:a")]}, ["cluster:a"]
    )
    row = repo.latest_personal_brief(conn, user_id=READER)
    brief = personal_brief.render_brief(conn, row)
    assert brief.id == brief_id
    assert len(brief.sections) == 1 and brief.sections[0].heading == FIRST_DESK
    item = brief.sections[0].items[0]
    # Attribution joined from `documents` via the cluster, never model-written.
    assert item.publisher == "Test Wire"
    assert item.url == "https://e.com/cluster:a"
    assert [b for b in item.summary] and [b for b in item.why]
    assert len(brief.top) == 1 and brief.top[0].item_id == item.id
    assert brief.freshness.total == 1


def test_the_sources_footer_credits_the_whole_ranked_window(conn: db.Conn) -> None:
    """Every article the ranking read, not the handful the brief argued about
    -- the shared brief's meaning of the word."""
    _reader(conn)
    for cid in ("cluster:a", "cluster:b"):
        _cluster(conn, cid, title=f"Title for {cid}")
    _persist(
        conn,
        {"headline": "A day", "dek": "", "items": [_item("cluster:a")]},
        ["cluster:a"],
        source_ids=["doc:cluster:a", "doc:cluster:b"],
    )
    row = repo.latest_personal_brief(conn, user_id=READER)
    brief = personal_brief.render_brief(conn, row)
    assert {s.article_id for s in brief.sources} == {"doc:cluster:a", "doc:cluster:b"}
    # Attribution joined from `documents`, exactly as the shared footer does.
    assert all(s.publisher == "Test Wire" for s in brief.sources)


def test_the_two_briefs_offer_the_model_the_same_desks(conn: db.Conn) -> None:
    """A desk the model invents is rejected by the validator rather than
    quietly creating a one-item desk that looks like coverage."""
    from venona.llm import schemas

    def desks(schema: dict) -> list[str]:
        return schema["properties"]["items"]["items"]["properties"]["section"]["enum"]

    assert desks(schemas.personal_brief_schema()) == desks(schemas.brief_schema())
    assert "section" in schemas.personal_brief_schema()["properties"]["items"]["items"]["required"]


def test_a_pre_migration_narrative_document_still_renders(conn: db.Conn) -> None:
    """The legacy branch: blocks with no item_id render as narrative, so the
    format change empties nobody's archive."""
    _reader(conn)
    stamp = "2026-08-20T08:00:00.000Z"
    db.insert(
        conn,
        "personal_briefs",
        {
            "id": "pbrief:2026-08-20:legacy",
            "user_id": READER,
            "date": "2026-08-20",
            "run_id": None,
            "inputs_hash": "old",
            "title": "The old shape",
            "dek": None,
            "generated_at": stamp,
            "created_at": stamp,
        },
    )
    db.insert(
        conn,
        "personal_brief_blocks",
        {
            "id": "pbrief:2026-08-20:legacy:b1",
            "brief_id": "pbrief:2026-08-20:legacy",
            "section": 1,
            "heading": None,
            "ordinal": 1,
            "type": "paragraph",
            "depth": 0,
            "text": "A narrative paragraph.",
            "spans": db.jdump([]),
            "marks": db.jdump([]),
            "item_id": None,
            "role": None,
        },
    )
    conn.commit()
    row = repo.latest_personal_brief(conn, user_id=READER)
    brief = personal_brief.render_brief(conn, row)
    assert len(brief.sections) == 1
    assert brief.sections[0].items == []
    assert len(brief.sections[0].blocks) == 1


def test_the_format_version_is_part_of_the_inputs_hash(conn: db.Conn, monkeypatch) -> None:
    """Bumping the format constant flips every stored document to stale --
    the only path by which a format change reaches readers whose inputs have
    not moved."""
    _reader(conn)
    settings = get_settings()
    before = personal_brief._inputs_hash(conn, user_id=READER, settings=settings, effective={})
    monkeypatch.setattr(
        personal_brief, "PERSONAL_BRIEF_FORMAT", personal_brief.PERSONAL_BRIEF_FORMAT + 1
    )
    after = personal_brief._inputs_hash(conn, user_id=READER, settings=settings, effective={})
    assert before != after
