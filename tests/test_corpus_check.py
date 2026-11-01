"""Asking our own documents whether the definition we bought makes sense here.

Tier 3's four checks ask *does this page define these letters this way*. A page
can answer yes and be irrelevant to every article we hold -- which is how `BAE`
became "brigade aviation element" in a corpus where those letters are followed
by "Systems" sixteen times out of sixteen.

The temperament being pinned here is the asymmetry. Presence of the phrase is
evidence; ABSENCE IS NOT. Measured over 37 live definitions, 22 had no
occurrence of their expansion and most of those were correct -- `USC`, `AWACS`,
`DVIDS`, `USV`. A check that demoted on absence would be wrong about all of
them, and the corpus not spelling an acronym out is the reason tier 3 exists.
"""

from __future__ import annotations

import pytest
from helpers import NOW
from venona.db import core as db
from venona.pipeline.acronyms import confidence_for, corroborate_from_corpus, store_definition
from venona.services import corpus_check


def _doc(conn: db.Conn, doc_id: str, text: str) -> None:
    # Same column set `test_brief.py` uses -- `documents` has several NOT NULL
    # columns without defaults, and discovering them one IntegrityError at a
    # time is not a test helper.
    # `db.upsert` rather than the `INSERT OR REPLACE` this was: that is SQLite
    # syntax, and `upsert` already emits the portable
    # `ON CONFLICT (...) DO UPDATE` form.
    db.upsert(
        conn,
        "documents",
        {
            "id": doc_id,
            "url": f"https://x.test/{doc_id}",
            "url_key": f"https://x.test/{doc_id}",
            "title": doc_id,
            "text_nfc": text,
            "text_chars": len(text),
            "content_hash": doc_id,
            "cluster_id": doc_id,
            "fetched_at": "n",
            "created_at": "n",
        },
        key=["id"],
    )


@pytest.fixture
def empty(conn: db.Conn) -> db.Conn:
    conn.execute("DELETE FROM acronym_definitions")
    conn.execute("DELETE FROM documents")
    conn.commit()
    return conn


# ── corroboration ────────────────────────────────────────────────────


def test_a_document_that_spells_it_out_is_corroboration(empty: db.Conn) -> None:
    _doc(empty, "doc:1", "The Futures and Concepts Command (FCC) set the requirement.")
    _doc(empty, "doc:2", "FCC will run the trials next year.")

    found = corpus_check.support(empty, "FCC", "Futures and Concepts Command")
    assert found.documents == 2
    assert found.phrase_documents == ["doc:1"]
    assert found.verdict == "corroborated"


@pytest.mark.negative
def test_absence_says_nothing(empty: db.Conn) -> None:
    """`USC` = United States Code is correct and appears spelled out nowhere.
    A check that demoted on absence would be wrong about most of the corpus."""
    _doc(empty, "doc:1", "Under 10 USC 4025 the programme proceeds.")

    found = corpus_check.support(empty, "USC", "United States Code")
    assert found.documents == 1
    assert found.phrase_documents == []
    assert found.verdict == "unsupported", "not `contradicted` -- nothing was said either way"
    assert not found.contradicted


def test_the_surface_match_is_case_sensitive(empty: db.Conn) -> None:
    """Lower-casing the text before matching the surface is how an earlier pass
    at this analysis matched nothing at all while confidently reporting that
    nothing was corroborated."""
    _doc(empty, "doc:1", "The Fire Control Center is aboard. FCC feeds the launcher.")

    assert corpus_check.support(empty, "FCC", "Fire Control Center").documents == 1


# ── contradiction: the surface is a name, not an acronym ─────────────


def test_a_surface_always_followed_by_one_word_is_a_name(empty: db.Conn) -> None:
    _doc(empty, "doc:1", "BAE Systems won it. BAE Systems said so. Later, BAE Systems again.")

    found = corpus_check.support(empty, "BAE", "brigade aviation element")
    assert found.name_head == "Systems"
    assert found.verdict == "contradicted"


@pytest.mark.negative
def test_a_spelled_out_expansion_clears_the_name_suspicion(empty: db.Conn) -> None:
    """BOTH halves are required. A surface can head a name AND be a real
    acronym -- `DISA Directorate`, with "Defense Information Systems Agency"
    written out in the same document, is not in question."""
    _doc(
        empty,
        "doc:1",
        "The Defense Information Systems Agency runs it. DISA Directorate leads, "
        "DISA Directorate reports, DISA Directorate signs.",
    )

    found = corpus_check.support(empty, "DISA", "Defense Information Systems Agency")
    assert found.name_head == "Directorate"
    assert found.corroborated
    assert not found.contradicted, "the corpus spells it out; the follower is a coincidence"


@pytest.mark.negative
def test_two_mentions_are_not_a_pattern(empty: db.Conn) -> None:
    """A coincidence needs a bigger sample than an instance."""
    _doc(empty, "doc:1", "XYZ Systems bid. XYZ Systems won.")

    assert corpus_check.support(empty, "XYZ", "extremely young zebra").name_head is None


# ── what corroboration does to the row ───────────────────────────────


def test_corroboration_raises_confidence_without_touching_provenance(
    empty: db.Conn,
) -> None:
    """It adds SOURCES on the row's existing tier.

    Going through `store_definition` was the first attempt and it was wrong
    twice over: it would have overwritten the real page and sentence the four
    checks passed, and claiming `in_corpus` for "the phrase occurs somewhere in
    a document" would overstate evidence that tier 2 defines much more strictly.
    """
    entry = store_definition(
        empty,
        surface="FCC",
        expansion="Futures and Concepts Command",
        method="web_grounded",
        confidence=0.7,
        article_id=None,
        quote="The Futures and Concepts Command (FCC) is an Army organisation.",
        url="https://army.example/fcc",
        now=NOW,
    )
    _doc(empty, "doc:1", "The Futures and Concepts Command (FCC) set the requirement.")

    assert corroborate_from_corpus(empty, "FCC", "Futures and Concepts Command", now=NOW) == 1

    row = empty.execute("SELECT * FROM acronym_definitions WHERE id = ?", (entry,)).fetchone()
    assert row["corroboration_count"] == 2
    assert row["confidence"] == confidence_for("web_grounded", 2, declared=0.7)
    # The tier and its evidence are untouched.
    assert row["method"] == "web_grounded"
    assert row["grounding_url"] == "https://army.example/fcc"
    assert "(FCC)" in row["grounding_quote"]


@pytest.mark.negative
def test_running_it_twice_does_not_raise_confidence_twice(empty: db.Conn) -> None:
    store_definition(
        empty,
        surface="FCC",
        expansion="Futures and Concepts Command",
        method="web_grounded",
        confidence=0.7,
        article_id=None,
        quote="The Futures and Concepts Command (FCC) is an Army organisation.",
        url="https://army.example/fcc",
        now=NOW,
    )
    _doc(empty, "doc:1", "The Futures and Concepts Command (FCC) set the requirement.")

    assert corroborate_from_corpus(empty, "FCC", "Futures and Concepts Command", now=NOW) == 1
    assert corroborate_from_corpus(empty, "FCC", "Futures and Concepts Command", now=NOW) == 0


@pytest.mark.negative
def test_it_does_not_touch_a_row_a_human_rejected(empty: db.Conn) -> None:
    entry = store_definition(
        empty,
        surface="FCC",
        expansion="Fire Control Center",
        method="web_grounded",
        confidence=0.7,
        article_id=None,
        quote="The Fire Control Center (FCC) is aboard.",
        url="https://kongsberg.example/fcc",
        now=NOW,
    )
    empty.execute(
        "UPDATE acronym_definitions SET status='rejected', decided_at=? WHERE id=?", (NOW, entry)
    )
    _doc(empty, "doc:1", "The Fire Control Center (FCC) is aboard the vessel.")

    assert corroborate_from_corpus(empty, "FCC", "Fire Control Center", now=NOW) == 0


# ── the corroboration sweep does not re-read an unchanged corpus ─────


def test_a_second_corroboration_over_an_unchanged_corpus_is_skipped(conn: db.Conn) -> None:
    """⭐ O(documents x definitions) of regex over every body in the corpus.

    A second press a minute after the first cannot find anything the first did
    not, and the button is right there. The stamp covers both inputs, because a
    human verdict changes the answer without a single article arriving.
    """
    from venona.api.routers import glossary as glossary_router

    glossary_router._LAST_CORROBORATION.clear()

    first = glossary_router.corroborate_all(conn)
    assert first["skipped"] is False

    second = glossary_router.corroborate_all(conn)
    assert second["skipped"] is True, "an unchanged corpus must not be re-read"
    assert second["checked"] == 0


def test_a_new_document_reopens_the_corroboration_sweep(conn: db.Conn) -> None:
    """The mechanism behind accuracy improving over time is a definition being
    spelled out in something ingested since -- so an arriving article must
    always be able to reopen the question."""
    from venona.api.routers import glossary as glossary_router

    glossary_router._LAST_CORROBORATION.clear()
    glossary_router.corroborate_all(conn)
    assert glossary_router.corroborate_all(conn)["skipped"] is True

    db.upsert(
        conn,
        "clusters",
        {
            "id": "cluster:corroborate-new",
            "canonical_doc_id": "doc:corroborate-new",
            "title": "A new article",
            "first_seen": "2026-08-26T00:00:00.000Z",
            "last_seen": "2026-08-26T00:00:00.000Z",
        },
        key=["id"],
    )
    db.insert(
        conn,
        "documents",
        {
            "id": "doc:corroborate-new",
            "url": "https://example.test/new",
            "url_key": "https://example.test/new",
            "title": "A new article",
            "text_nfc": "Body text.",
            "text_chars": 10,
            "content_hash": "hash-new",
            "cluster_id": "cluster:corroborate-new",
            "published_at": "2026-08-26T00:00:00.000Z",
            "fetched_at": "2026-08-26T00:00:00.000Z",
            "created_at": "2026-08-26T00:00:00.000Z",
        },
    )

    assert glossary_router.corroborate_all(conn)["skipped"] is False


# ── what the audit re-reads, and when ────────────────────────────────


def _grounded(conn: db.Conn, surface: str, expansion: str) -> None:
    store_definition(
        conn,
        surface=surface,
        expansion=expansion,
        method="web_grounded",
        confidence=0.7,
        article_id=None,
        quote=f"The {expansion} ({surface}) is a thing.",
        url=f"https://example.com/{surface.lower()}",
        now=NOW,
    )


def _corpus(conn: db.Conn) -> None:
    _doc(conn, "d1", "The FCC said so. FCC Systems shipped. Fire Control Center is a place.")
    _doc(conn, "d2", "BAE Systems won it. BAE Systems again. BAE Systems a third time.")
    _doc(conn, "d3", "Nothing relevant here at all, just prose about x_C2_y and .NET.")
    _grounded(conn, "FCC", "Fire Control Center")
    _grounded(conn, "BAE", "brigade aviation element")
    _grounded(conn, "C2", "Command and Control")
    conn.commit()


def _naive(conn: db.Conn) -> list[corpus_check.Support]:
    """The whole-corpus sweep, with no narrowing and no memo.

    The narrowed pass is only allowed to be faster, never different, so this is
    the answer it has to reproduce.
    """
    documents = [(r["id"], r["text_nfc"] or "") for r in corpus_check.repo.select_documents(conn)]
    rows = corpus_check.repo.select_acronym_definitions_by_method(conn, "web_grounded").fetchall()
    return [
        corpus_check.support(conn, r["surface"], r["expansion"], documents=documents) for r in rows
    ]


def _readable(results: list[corpus_check.Support]) -> list[tuple]:
    return [
        (r.surface, r.expansion, r.documents, tuple(r.phrase_documents), r.name_head, r.verdict)
        for r in results
    ]


def test_narrowing_the_scan_does_not_change_a_single_verdict(empty: db.Conn) -> None:
    _corpus(empty)
    assert _readable(corpus_check.audit(empty)) == _readable(_naive(empty))


def test_a_surface_the_boundary_rules_reject_still_matches_nothing(empty: db.Conn) -> None:
    """`C2` occurs in `x_C2_y`, and a word boundary is what says it does not count.

    A substring index is the cheap way to find candidate bodies; it is not the
    rule. `_` is a word character to `\\b`, so this surface has no match here --
    and a narrowing pass that trusted its own hits would invent one.
    """
    _corpus(empty)
    c2 = next(r for r in corpus_check.audit(empty) if r.surface == "C2")
    assert c2.documents == 0


def test_a_verdict_does_not_send_the_audit_back_to_the_corpus(empty: db.Conn) -> None:
    """The curator's own action must not be what makes the queue slow.

    Ruling on one surface changes which rows are audited, so the list is rebuilt
    -- but it says nothing about what the documents contain, so no body is read
    again. That is the whole point of stamping weighings on the corpus alone.
    """
    _corpus(empty)
    corpus_check.audit(empty)

    reads = 0
    original = corpus_check.repo.select_documents

    def counted(conn):
        nonlocal reads
        reads += 1
        return original(conn)

    corpus_check.repo.select_documents = counted
    try:
        empty.execute(
            "UPDATE acronym_definitions SET status = 'rejected', decided_at = ? "
            "WHERE surface = 'FCC'",
            (NOW,),
        )
        empty.commit()
        after = corpus_check.audit(empty)
    finally:
        corpus_check.repo.select_documents = original

    assert reads == 0
    assert all(r.surface != "FCC" for r in after)


def test_a_new_document_does_send_it_back(empty: db.Conn) -> None:
    """The other half of the same stamp: the corpus moving is a real change."""
    _corpus(empty)
    before = corpus_check.audit(empty)

    _doc(empty, "d4", "brigade aviation element, spelled out, beside BAE itself.")
    empty.commit()
    after = corpus_check.audit(empty)

    assert after is not before
    bae = next(r for r in after if r.surface == "BAE")
    assert bae.corroborated
