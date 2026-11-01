"""Confidence as accumulated evidence, not a constant per tier.

`store_definition` wrote whatever `confidence` its caller passed and overwrote
it on every re-grounding, so a definition one web page asserted once and a
definition four documents spell out carried the same 0.7 forever. Its own
comment already named the case -- *"Re-grounding the same expansion from a
second article is corroboration -- the common case"* -- and then discarded it.

What these pin is the part that is easy to get subtly wrong and impossible to
notice afterwards: what counts as a SECOND source, and what a re-grounding is
allowed to do to a row that already holds better evidence.
"""

from __future__ import annotations

import pytest
from helpers import NOW
from venona.db import core as db
from venona.pipeline.acronyms import (
    WEB_BASE_CONFIDENCE,
    WEB_CONFIDENCE_CAP,
    confidence_for,
    store_definition,
)

QUOTE = "The Modular Open Systems Approach (MOSA) is a technical strategy."


def _store(conn: db.Conn, **kw):
    args = {
        "surface": "MOSA",
        "expansion": "Modular Open Systems Approach",
        "method": "web_grounded",
        "confidence": 0.7,
        "article_id": None,
        "quote": QUOTE,
        "now": NOW,
    }
    args.update(kw)
    return store_definition(conn, **args)


def _row(conn: db.Conn, entry_id: str) -> db.Row:
    return conn.execute("SELECT * FROM acronym_definitions WHERE id = ?", (entry_id,)).fetchone()


@pytest.fixture
def empty(conn: db.Conn) -> db.Conn:
    conn.execute("DELETE FROM acronym_definitions")
    conn.commit()
    return conn


# ── the ladder ───────────────────────────────────────────────────────


def test_one_web_page_opens_below_the_old_flat_number() -> None:
    """0.7 claimed more than one page asserting something is worth. The number
    now says what it means: a provisional reading."""
    assert confidence_for("web_grounded", 1, declared=0.7) == WEB_BASE_CONFIDENCE
    assert WEB_BASE_CONFIDENCE < 0.7


def test_agreement_raises_it_and_the_cap_holds() -> None:
    first = confidence_for("web_grounded", 1, declared=0.7)
    second = confidence_for("web_grounded", 2, declared=0.7)
    assert second > first
    assert confidence_for("web_grounded", 99, declared=0.7) == WEB_CONFIDENCE_CAP
    # Below `in_corpus`. A page agreed with three times is still weaker than a
    # sentence in the corpus this system ingested and can re-read.
    assert WEB_CONFIDENCE_CAP < 0.9


def test_the_deterministic_tiers_keep_the_number_their_caller_declared() -> None:
    """A parenthetical either verifies against the initials or it does not, and
    a second one saying the same thing does not make the first more true."""
    for method, declared in (("in_article", 1.0), ("in_corpus", 0.9), ("manual", 1.0)):
        assert confidence_for(method, 1, declared=declared) == declared
        assert confidence_for(method, 5, declared=declared) == declared


# ── what counts as a second source ───────────────────────────────────


def test_a_different_page_is_corroboration(empty: db.Conn) -> None:
    entry = _store(empty, url="https://a.mil/glossary")
    assert _row(empty, entry)["corroboration_count"] == 1

    _store(empty, url="https://b.mil/handbook")
    row = _row(empty, entry)
    assert row["corroboration_count"] == 2
    assert row["confidence"] > WEB_BASE_CONFIDENCE


@pytest.mark.negative
def test_the_same_page_twice_is_not(empty: db.Conn) -> None:
    """The nightly run re-reads documents it has already ingested. A count that
    climbed on re-analysis would manufacture confidence out of nothing -- which
    is why the sources are stored as a list rather than a number."""
    entry = _store(empty, url="https://a.mil/glossary")
    _store(empty, url="https://a.mil/glossary")

    row = _row(empty, entry)
    assert row["corroboration_count"] == 1
    assert row["confidence"] == WEB_BASE_CONFIDENCE


def test_an_article_and_a_url_are_both_sources(empty: db.Conn) -> None:
    entry = _store(empty, url="https://a.mil/glossary")
    _store(empty, method="in_corpus", confidence=0.9, article_id="doc:1", url=None)

    assert _row(empty, entry)["corroboration_count"] == 2


@pytest.mark.negative
def test_a_row_written_before_this_existed_does_not_count_itself_twice(
    empty: db.Conn,
) -> None:
    """Migration 0018 leaves `corroborating_sources` NULL on existing rows. The
    first new evidence seeds the list from the row's own provenance, so a row
    with one source that gains one more reads 2 -- not 3, and not 1."""
    entry = _store(empty, url="https://a.mil/glossary")
    empty.execute(
        "UPDATE acronym_definitions SET corroborating_sources = NULL, corroboration_count = 1 "
        "WHERE id = ?",
        (entry,),
    )

    _store(empty, url="https://b.mil/handbook")
    assert _row(empty, entry)["corroboration_count"] == 2

    # ...and the seeded source is still recognised as already counted.
    _store(empty, url="https://a.mil/glossary")
    assert _row(empty, entry)["corroboration_count"] == 2


# ── evidence may only move a row up ──────────────────────────────────


@pytest.mark.negative
def test_a_web_lookup_cannot_downgrade_a_definition_from_the_corpus(
    empty: db.Conn,
) -> None:
    """More evidence must never record WEAKER provenance.

    `store_definition` overwrote `method` and `confidence` unconditionally, so a
    tier-1 definition -- a parenthetical in a document we hold, verified by
    initials -- would be rewritten as `web_grounded` at 0.55 by a later page
    that agreed with it. Nothing routes a lookup at an already-grounded surface
    today, which is the only reason this never fired.
    """
    entry = _store(empty, method="in_article", confidence=1.0, article_id="doc:1", url=None)
    _store(empty, method="web_grounded", confidence=0.7, url="https://a.mil/glossary")

    row = _row(empty, entry)
    assert row["method"] == "in_article"
    assert row["confidence"] == 1.0
    # The weaker source still counts as agreement, and the stronger tier's own
    # evidence is what stays on the row.
    assert row["corroboration_count"] == 2
    assert row["grounding_article_id"] == "doc:1"


def test_a_corpus_grounding_upgrades_a_web_one(empty: db.Conn) -> None:
    """The other direction is exactly what should happen: the row records the
    strongest evidence that has ever produced it."""
    entry = _store(empty, url="https://a.mil/glossary")
    assert _row(empty, entry)["method"] == "web_grounded"

    _store(empty, method="in_article", confidence=1.0, article_id="doc:1", url=None)
    row = _row(empty, entry)
    assert row["method"] == "in_article"
    assert row["confidence"] == 1.0
    assert row["corroboration_count"] == 2


@pytest.mark.negative
def test_corroboration_does_not_overturn_a_human_rejection(empty: db.Conn) -> None:
    """The verdict outranks the evidence. A curator who rejected an expansion
    must not have it reinstated by a second page agreeing with the first."""
    entry = _store(empty, url="https://a.mil/glossary")
    empty.execute(
        "UPDATE acronym_definitions SET status='rejected', decided_at=? WHERE id=?", (NOW, entry)
    )

    _store(empty, url="https://b.mil/handbook")
    assert _row(empty, entry)["status"] == "rejected"


@pytest.mark.negative
def test_corroboration_does_not_discard_the_stronger_tiers_span(
    empty: db.Conn,
) -> None:
    """The span points INTO the document that grounded this, and it is the only
    part of the provenance that cannot be recovered by looking at the row.

    Keeping the stronger tier's method while dropping its span was silent: the
    column is written and never read back, so nothing would have failed -- the
    offsets would simply have stopped being there.
    """
    entry = _store(
        empty,
        method="in_article",
        confidence=1.0,
        article_id="doc:1",
        url=None,
        span=[10, 41],
    )
    assert _row(empty, entry)["grounding_span"] == "[10,41]"

    # A weaker source agrees. The row keeps tier 1 -- and all four pieces of
    # tier 1's evidence, not three of them.
    _store(empty, method="web_grounded", confidence=0.7, url="https://a.mil/glossary")

    row = _row(empty, entry)
    assert row["method"] == "in_article"
    assert row["grounding_span"] == "[10,41]"
    assert row["grounding_article_id"] == "doc:1"
    assert row["corroboration_count"] == 2
