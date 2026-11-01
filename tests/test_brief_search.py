"""Search over brief content.

`ArchiveView`'s filter was client-side over title and dek and said so on screen:
remembering a program name from the BODY of an item did not find it. The data
was always there -- `brief_blocks.text` is the plain text the runs concatenate
back to, and `brief_blocks.spans` carries the resolved entity references.

These cover the two things that are easy to get quietly wrong: what counts as a
match, and what an id lookup is allowed to match.
"""

from __future__ import annotations

import pytest
from helpers import NOW
from venona.db import core as db
from venona.services import brief_service

# Deliberately nonsense proper nouns. The `conn` fixture loads the seed corpus,
# which has real briefs mentioning the Army and Anduril -- so a test asserting an
# EXACT result set has to search for something only these fixtures contain.
# Getting this wrong is how a search test passes while matching the wrong rows.
BODY = (
    "The Army named Zephyrine Dynamics to lead the common data layer for "
    "Next Generation Command and Control, following a quibblesome exercise."
)
REF = "company:zephyrine-dynamics"


def _brief(
    conn: db.Conn,
    brief_id: str,
    *,
    date: str = "2026-08-08",
    title: str = "A quiet day",
    dek: str | None = None,
) -> None:
    db.upsert(
        conn,
        "briefs",
        {
            "id": brief_id,
            "date": date,
            "title": title,
            "dek": dek,
            "generated_at": NOW,
            "created_at": NOW,
        },
        key=["id"],
    )


def _item(conn: db.Conn, brief_id: str, item_id: str, headline: str) -> None:
    db.upsert(
        conn,
        "brief_items",
        {
            "id": item_id,
            "brief_id": brief_id,
            "section": "UAS",
            "rank": 1,
            "headline": headline,
            "state": "new",
            "created_at": NOW,
        },
        key=["id"],
    )


def _block(
    conn: db.Conn,
    brief_id: str,
    block_id: str,
    text: str,
    *,
    spans: str = "[]",
    item_id: str | None = None,
) -> None:
    db.upsert(
        conn,
        "brief_blocks",
        {
            "id": block_id,
            "brief_id": brief_id,
            "section": 1,
            "ordinal": 1,
            "type": "paragraph",
            "depth": 0,
            "text": text,
            "spans": spans,
            "marks": "[]",
            "item_id": item_id,
        },
        key=["id"],
    )


@pytest.fixture
def corpus(conn: db.Conn) -> db.Conn:
    """Two briefs the seed does not provide, so the assertions are about these."""
    _brief(
        conn,
        "brief:t1",
        date="2026-08-08",
        title="Data layer day",
        dek="A dek about quibblesome things",
    )
    _item(conn, "brief:t1", "brief:t1:i01", "Anduril takes the data layer")
    _block(
        conn,
        "brief:t1",
        "brief:t1:b1",
        BODY,
        spans=f'[{{"start":15,"end":33,"ref":"{REF}","kind":"entity"}}]',
        item_id="brief:t1:i01",
    )
    _brief(conn, "brief:t2", date="2026-08-07", title="Nothing much", dek=None)
    _block(conn, "brief:t2", "brief:t2:b1", "An unrelated paragraph about shipbuilding.")
    return conn


def test_a_word_only_in_the_body_is_found(corpus: db.Conn) -> None:
    """The whole point. `validation exercise` appears in no title, no dek and no
    headline -- it is the case the old client-side filter could not answer, and
    the reason the view apologised for itself twice."""
    result = brief_service.search_briefs(corpus, q="quibblesome exercise")

    assert [h.brief.id for h in result.items] == ["brief:t1"]
    assert result.items[0].matches[0].where == "body"
    assert "quibblesome exercise" in result.items[0].matches[0].snippet


def test_the_snippet_keeps_the_documents_casing(corpus: db.Conn) -> None:
    """Searching `anduril` must not render the brief as though it had been
    written in lower case."""
    result = brief_service.search_briefs(corpus, q="zephyrine dynamics")

    snippets = " ".join(m.snippet for m in result.items[0].matches)
    assert "Zephyrine Dynamics" in snippets


def test_matching_is_case_insensitive_in_both_directions(corpus: db.Conn) -> None:
    lower = brief_service.search_briefs(corpus, q="QUIBBLESOME")
    upper = brief_service.search_briefs(corpus, q="quibblesome")
    assert [h.brief.id for h in lower.items] == [h.brief.id for h in upper.items] == ["brief:t1"]


def test_where_says_which_part_matched(corpus: db.Conn) -> None:
    """A title match means the brief is ABOUT the thing; a body match means one
    item mentions it. The reader does different things with those."""
    result = brief_service.search_briefs(corpus, q="Data layer")

    wheres = {m.where for m in result.items[0].matches}
    assert "title" in wheres
    assert "headline" in wheres


def test_an_empty_query_returns_nothing_rather_than_everything(
    corpus: db.Conn,
) -> None:
    """`GET /briefs` is what "show me all of them" is for. Returning the archive
    from a search box makes an empty box look like a bug, and the archive is
    already on screen behind it."""
    assert brief_service.search_briefs(corpus, q="").items == []
    assert brief_service.search_briefs(corpus, q="   ").items == []


@pytest.mark.negative
def test_an_entity_id_is_matched_whole_never_as_a_substring(
    corpus: db.Conn,
) -> None:
    """The failure an id lookup must not have.

    `spans` is a JSON column, so a `LIKE '%company:anduril%'` over the serialised
    row would be the obvious implementation and would silently return a
    SUPERSET -- `company:anduril` matching `company:anduril-industries`. `json_each`
    plus an equality on `$.ref` is what makes that impossible.
    """
    exact = brief_service.search_briefs(corpus, entity=REF)
    assert [h.brief.id for h in exact.items] == ["brief:t1"]

    prefix = brief_service.search_briefs(corpus, entity=REF[: -len("-dynamics")])
    assert prefix.items == [], "a prefix must not match a longer id"


def test_entity_search_finds_a_mention_the_text_does_not_spell(
    corpus: db.Conn,
) -> None:
    """Why the entity filter is not just a second text search: the annotation
    already resolved the surface, so this finds mentions under every alias --
    including ones a reader would never think to type."""
    by_id = brief_service.search_briefs(corpus, entity=REF)
    assert by_id.total == 1
    assert by_id.items[0].match_count == 1


def test_the_two_filters_are_anded(corpus: db.Conn) -> None:
    both = brief_service.search_briefs(corpus, q="shipbuilding", entity=REF)
    assert both.items == [], "t2 has the word, t1 has the entity, neither has both"


def test_matches_are_capped_per_brief_but_the_count_is_not(
    conn: db.Conn,
) -> None:
    """One brief that says a word forty times must not crowd the others out of
    the list, and the reader still has to be told there is more."""
    _brief(conn, "brief:many", title="Repetitive")
    for n in range(6):
        _block(conn, "brief:many", f"brief:many:b{n}", f"flibbertigibbet paragraph {n}")

    result = brief_service.search_briefs(conn, q="flibbertigibbet")
    hit = next(h for h in result.items if h.brief.id == "brief:many")

    assert len(hit.matches) == brief_service.MATCHES_PER_BRIEF
    assert hit.match_count == 6


def test_briefs_come_back_newest_first_not_by_match_count(
    corpus: db.Conn,
) -> None:
    """The archive is a chronology. A brief jumping to the top because it
    repeated a word would read as a relevance ranking the system has no basis
    for."""
    # t2 is OLDER and says it three times; t1 is newer and says it once.
    for n in range(3):
        _block(corpus, "brief:t2", f"brief:t2:x{n}", f"flibbertigibbet {n}")
    _block(corpus, "brief:t1", "brief:t1:b2", "flibbertigibbet once")

    result = brief_service.search_briefs(corpus, q="flibbertigibbet")

    assert [h.brief.id for h in result.items] == ["brief:t1", "brief:t2"]
    assert result.items[0].match_count < result.items[1].match_count


async def test_search_is_served_over_http(api_client, api_db: db.Conn) -> None:
    """Through FastAPI, because a `response_model` fails in two directions and
    neither is loud -- see `tests/test_response_models.py`."""
    _brief(api_db, "brief:http", title="Served over HTTP", dek=None)
    _block(api_db, "brief:http", "brief:http:b1", "A paragraph mentioning hypersonics.")
    api_db.commit()

    response = await api_client.get("/api/briefs/search", params={"q": "hypersonics"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "hypersonics"
    ids = [hit["brief"]["id"] for hit in body["items"]]
    assert "brief:http" in ids
    assert body["items"][ids.index("brief:http")]["matches"][0]["where"] == "body"


async def test_search_is_not_captured_as_a_brief_id(api_client) -> None:
    """`/briefs/search` must beat `/briefs/{brief_id}`. Declared in that order in
    the router; if it ever moves, this answers 404 with a body naming the id."""
    response = await api_client.get("/api/briefs/search", params={"q": "anything"})
    assert response.status_code == 200
    assert "no brief" not in response.text


# ── the cost of a search ─────────────────────────────────────────────


def test_a_search_reads_detail_only_for_the_page_it_returns(
    conn: db.Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⭐ The archive grows by one brief a day, forever, and this endpoint is
    reachable from the MCP surface as well as the reader's filter.

    Read unfiltered, the search walked EVERY brief and ran two or three more
    queries against each one — so its cost was O(briefs) round trips regardless
    of how many matched or how many the caller asked for. The filter belongs in
    SQL; the detail queries are then paid only for the briefs actually returned.
    """
    from venona.db.repositories import brief as repo

    for index in range(6):
        brief_id = f"brief:cost{index}"
        _brief(conn, brief_id, date=f"2026-07-0{index + 1}", title="Quibblesome day")
        _item(conn, brief_id, f"{brief_id}:i01", "A quibblesome headline")
    conn.commit()

    calls: list[list[str]] = []
    original = repo.brief_items_matching

    def counted(target, brief_ids, like):
        calls.append(list(brief_ids))
        return original(target, brief_ids, like)

    monkeypatch.setattr(repo, "brief_items_matching", counted)

    results = brief_service.search_briefs(conn, q="quibblesome", limit=2)

    assert len(results.items) == 2
    # ONE statement, whatever the page holds: a round trip per row on the page
    # is the shape this exists to keep out, and `limit` reaches 200.
    assert len(calls) == 1, f"detail reads ran {len(calls)} times for one page"
    assert calls[0] == [hit.brief.id for hit in results.items], (
        "detail was read for briefs that are not on the page"
    )


def test_a_bounded_page_still_reports_how_much_matched(conn: db.Conn) -> None:
    """`total` is what tells the reader there is more, so bounding the page must
    not bound the count. Counted by the same predicate that selects, so the two
    cannot disagree about what a match is."""
    for index in range(5):
        brief_id = f"brief:total{index}"
        _brief(conn, brief_id, date=f"2026-06-0{index + 1}", title="Quibblesome day")
    conn.commit()

    results = brief_service.search_briefs(conn, q="quibblesome", limit=2)

    assert len(results.items) == 2
    assert results.total >= 5
    assert results.truncated is True
