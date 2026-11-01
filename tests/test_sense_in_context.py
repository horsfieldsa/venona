"""One acronym, two meanings, and the text deciding which.

`verify_expansion` has always said it: *"Definitions are scoped to the document
that grounded them -- if an article says the second thing, then in that article
that is what NGC2 means."* The data model never honoured that. One row per
surface, one global answer, and when a surface acquired a second reading the
alias index stopped annotating it entirely -- so `FCC` was either confidently
one thing everywhere or invisible everywhere.

What these pin is the resolver's temperament rather than its cleverness. It has
exactly one kind of evidence -- the expansion, spelled out, in the same text --
and it declines in every other case. Declining costs an annotation; guessing
costs the reader's trust in every annotation on the page.
"""

from __future__ import annotations

import pytest
from helpers import NOW, entity
from venona.db import core as db
from venona.pipeline.acronyms import store_definition
from venona.services import alias_index
from venona.services.alias_index import build_index, pick_sense
from venona.services.annotate import annotate_blocks, markdown_to_blocks

FIRE = {"ref": "gloss:fcc", "expansion": "Fire Control Center", "confidence": 0.55}
FUTURES = {
    "ref": "gloss:fcc--futures-and-concepts-command",
    "expansion": "Futures and Concepts Command",
    "confidence": 0.55,
}
READINGS = [FIRE, FUTURES]


# ── the resolver ─────────────────────────────────────────────────────


def test_the_reading_the_text_spells_out_wins() -> None:
    chosen = pick_sense(READINGS, "The Army's Futures and Concepts Command (FCC) published it.")
    assert chosen == FUTURES

    chosen = pick_sense(READINGS, "Rounds are cued by the ship's Fire Control Center. FCC feeds…")
    assert chosen == FIRE


def test_formatting_is_not_meaning() -> None:
    """Case, hyphens and line breaks are how text is set, not what it says."""
    assert pick_sense(READINGS, "the futures and concepts command said") == FUTURES
    assert pick_sense(READINGS, "the FUTURES AND\nCONCEPTS COMMAND said") == FUTURES


@pytest.mark.negative
def test_it_declines_when_the_text_says_neither() -> None:
    """The common case, and the whole point. Bare `FCC` in a paragraph about
    procurement is not evidence for either reading, and the higher-confidence
    one is not a tiebreak -- it is a prior, which is what context is supposed to
    override."""
    assert pick_sense(READINGS, "FCC awarded the contract in March.") is None
    assert pick_sense(READINGS, "") is None
    assert pick_sense([], "Futures and Concepts Command") is None


@pytest.mark.negative
def test_it_declines_when_the_text_says_both() -> None:
    """Text spelling out both readings is text ABOUT the ambiguity. Picking the
    higher-confidence one there would be picking by prior, again."""
    context = (
        "Futures and Concepts Command is not the ship's Fire Control Center, though both are FCC."
    )
    assert pick_sense(READINGS, context) is None


# ── end to end, through the annotator ────────────────────────────────


@pytest.fixture
def split(conn: db.Conn) -> db.Conn:
    conn.execute("DELETE FROM acronym_definitions")
    conn.execute("DELETE FROM ambiguous_surfaces")
    for expansion, url in (
        ("Fire Control Center", "https://kongsberg.example/fcc"),
        ("Futures and Concepts Command", "https://army.example/fcc"),
    ):
        store_definition(
            conn,
            surface="FCC",
            expansion=expansion,
            method="web_grounded",
            confidence=0.7,
            article_id=None,
            quote=f"The {expansion} (FCC) is a thing.",
            url=url,
            now=NOW,
        )
    conn.commit()
    return conn


def _spans(conn, text, *, resolve: bool):
    blocks = markdown_to_blocks(text, section="lead", heading=None)
    annotate_blocks(
        blocks,
        build_index(conn),
        senses=alias_index.senses(conn) if resolve else None,
        context=text if resolve else None,
    )
    return [s for b in blocks for s in b.spans]


def test_an_ambiguous_acronym_is_annotated_with_the_reading_in_play(
    split: db.Conn,
) -> None:
    text = "The Futures and Concepts Command set the requirement. FCC will run the trials."
    spans = _spans(split, text, resolve=True)

    fcc = [s for s in spans if s["ref"].startswith("gloss:")]
    assert fcc, spans
    assert all(s["ref"] == FUTURES["ref"] for s in fcc), fcc
    assert all(s["kind"] == "glossary" for s in fcc)


@pytest.mark.negative
def test_without_context_it_stays_unannotated_exactly_as_before(
    split: db.Conn,
) -> None:
    """The old behaviour is the fallback, not a regression: every caller that
    passes no senses gets what it always got."""
    text = "The Futures and Concepts Command set the requirement. FCC will run the trials."
    assert not [s for s in _spans(split, text, resolve=False) if s["ref"].startswith("gloss:")]


@pytest.mark.negative
def test_a_placeholder_ref_never_reaches_the_wire(split: db.Conn) -> None:
    """`ambiguous:<normalized>` is internal. One reaching `spans` would put an
    id in the wire format (contract C3) that no card endpoint can answer for --
    a hover that spins forever."""
    for resolve in (True, False):
        text = "FCC awarded the contract."  # neither reading is spelled out
        assert not [s for s in _spans(split, text, resolve=resolve) if "ambiguous:" in s["ref"]]


@pytest.mark.negative
def test_an_unsettled_row_never_puts_two_entries_in_the_automaton(
    split: db.Conn,
) -> None:
    """`acronym_definitions.status` and `ambiguous_surfaces` can drift.

    `PATCH /glossary/{id}` sets a status without touching that table, so a
    surface can hold two `ambiguous` ROWS while the table says it is settled.
    Admitting `ambiguous` to the index query -- which sense resolution required
    -- made those rows visible for the first time, and indexed under their own
    ids they would put two entries with identical text in the automaton, where
    `_resolve_overlaps` breaks the tie by INSERTION ORDER. What FCC means would
    depend on row order.
    """
    split.execute("DELETE FROM ambiguous_surfaces WHERE normalized = 'fcc'")
    split.commit()

    # Behaviour, not internals: one mention must not produce two matches, or
    # which one survives is decided by row order.
    text = "The Fire Control Center fired. FCC did it."
    matches = [m for m in build_index(split).find(text) if m.surface == "FCC"]
    assert len(matches) <= 1, matches

    # ...and an unsettled row still reaches no span, exactly as an unsettled
    # surface never did.
    spans = _spans(split, text, resolve=True)
    assert not [s for s in spans if s["ref"].startswith("gloss:fcc")], spans


@pytest.fixture
def bound(conn: db.Conn) -> db.Conn:
    """One grounded reading, attached to an entity, on a surface a person has
    declared ambiguous.

    The shape `SDA` had on the live corpus: "Space Development Agency" grounded
    in an article, `entity_id` set, and `ambiguous_surfaces` carrying the
    operator's note that the letters also stand for "software-defined anything".
    """
    conn.execute("DELETE FROM acronym_definitions")
    conn.execute("DELETE FROM ambiguous_surfaces")
    entity(conn, "govt:agency-for-widgets", "Agency for Widgets")
    store_definition(
        conn,
        surface="AFW",
        expansion="Agency for Widgets",
        method="in_article",
        confidence=0.9,
        article_id=None,
        quote="The Agency for Widgets (AFW) said so.",
        url="https://example.test/afw",
        now=NOW,
    )
    conn.execute("UPDATE acronym_definitions SET entity_id = 'govt:agency-for-widgets'")
    db.upsert(
        conn,
        "ambiguous_surfaces",
        {"normalized": "afw", "reason": "Agency for Widgets vs airframe weight", "created_at": NOW},
        key=["normalized"],
    )
    conn.commit()
    return conn


def test_a_grounded_reading_still_annotates_when_its_definition_names_an_entity(
    bound: db.Conn,
) -> None:
    """Two guards, each assuming the other covers the surface, and a gap between.

    An ambiguous surface is demoted to strictness 0 in every entity alias that
    holds it, so the entity layer deliberately matches nothing. The placeholder
    is what is meant to take over -- and it was built only from definitions with
    no `entity_id`, which is exactly the ones the entity layer had covered. A
    definition naming an entity fell through both: grounded, active, in-article,
    and invisible to every reader.
    """
    text = "The Agency for Widgets published it. AFW will run the trials."
    spans = _spans(bound, text, resolve=True)

    afw = [s for s in spans if text[s["start"] : s["end"]] == "AFW"]
    assert afw, f"the grounded reading annotates nothing: {spans}"
    assert afw[0]["ref"] == "gloss:afw"
    assert afw[0]["kind"] == "glossary"


@pytest.mark.negative
def test_the_bound_definition_is_still_never_indexed_under_two_refs(
    bound: db.Conn,
) -> None:
    """The reason the `entity_id IS NULL` filter exists in the first place.

    A definition that names an entity must not enter the automaton beside that
    entity's own alias -- same text, same length, same strictness, and
    `_resolve_overlaps` breaking the tie by insertion order. One entry per
    surface, whichever layer supplies it.
    """
    entries = [e for e in build_index(bound)._entries if e.surface == "AFW"]
    assert len(entries) == 1, entries
