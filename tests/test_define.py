"""Entity definitions: grounded or absent, never guessed.

Invariant 3 says a definition exists only if it is traceable to a sentence in a
document. `pipeline/acronyms.py` has had that enforced since S8; this is the
same rule applied to entities, and these are the checks that keep it there.
"""

from __future__ import annotations

from venona.db import core as db
from venona.pipeline.confidence import ENTITY_CONFIDENCE, ENTITY_TIER_RANK
from venona.pipeline.define import (
    defining_sentence,
    mark_entity_undefined,
    store_entity_definition,
)

# ── tier 1: a sentence that DEFINES, not one that mentions ───────────


def test_an_appositive_defines(conn: db.Conn) -> None:
    sentence = (
        "The U.S. Army's Combined Arms Command is developing VICTOR, a mobile-first AI "
        "platform that synthesizes answers from the Army's doctrine library."
    )
    assert defining_sentence(sentence, "VICTOR") == sentence


def test_a_copular_sentence_defines(conn: db.Conn) -> None:
    sentence = (
        "Hivemind is an advanced AI-based software platform designed to ensure full "
        "autonomy of unmanned systems in combat conditions."
    )
    assert defining_sentence(sentence, "Hivemind") == sentence


def test_a_premodifier_defines(conn: db.Conn) -> None:
    """The description arrives BEFORE the name, which is the commonest shape in
    defence prose and the one worth the most: every sentence this catches is a
    free quoted definition tier 2 would otherwise pay a model call for."""
    sentence = (
        "The American company Shield AI, known for its V-BAT drones, is collaborating "
        "with a Ukrainian enterprise."
    )
    assert defining_sentence(sentence, "Shield AI") == sentence


def test_a_bare_mention_does_not_define(conn: db.Conn) -> None:
    """The check this file exists for.

    "Thales Australia said the contract runs through 2027" names the entity and
    says nothing about what it is. Accepting it would put a sentence about a
    contract in the slot that answers "what is this?", and it would look exactly
    like a real definition.
    """
    assert (
        defining_sentence(
            "Thales Australia said the contract runs through 2027 with options beyond.",
            "Thales Australia",
        )
        is None
    )


def test_a_possessive_does_not_define(conn: db.Conn) -> None:
    """`the Army's V-BAT contract` has a determiner and a noun before the name,
    and defines the CONTRACT rather than V-BAT. The kind-noun gate is what keeps
    it out."""
    assert (
        defining_sentence(
            "The service exercised an option on the Army's V-BAT contract this week.",
            "V-BAT",
        )
        is None
    )


def test_a_sentence_that_does_not_name_the_entity_never_defines_it(conn: db.Conn) -> None:
    assert defining_sentence("It is already in use on reconnaissance drones.", "V-BAT") is None


def test_a_fragment_does_not_define(conn: db.Conn) -> None:
    assert defining_sentence("Anduril Industries", "Anduril Industries") is None


# ── storage: provenance, and a human's verdict ───────────────────────


def test_a_human_verdict_outranks_the_pipeline(conn: db.Conn) -> None:
    """The same guard migration 0015 bought for acronyms.

    Without it the next nightly run silently overwrites a correction, and the
    curator's only recourse is to make it again, every night, forever.
    """
    conn.execute(
        "INSERT INTO entity_definitions (entity_id, definition, method, confidence, "
        "status, decided_at, first_seen, last_seen) VALUES "
        "('company:x', 'What a person wrote.', 'manual', 1.0, 'active', '2026-08-01', "
        "'2026-08-01', '2026-08-01')"
    )

    store_entity_definition(
        conn,
        entity_id="company:x",
        definition="What the pipeline wrote.",
        method="in_corpus",
        confidence=0.7,
        source_article_ids=["doc:1"],
        now="2026-08-07",
    )

    row = conn.execute("SELECT definition, method FROM entity_definitions").fetchone()
    assert row["definition"] == "What a person wrote."
    assert row["method"] == "manual"


def test_a_pipeline_pass_is_not_an_attempt(conn: db.Conn) -> None:
    """`last_attempt_at` means a WEB LOOKUP ran and grounded nothing.

    A define stage that simply found no defining sentence has not answered the
    question, so it must not stamp one -- the card reads this to decide whether
    to offer "Look this up" or "Try again", and a false stamp tells the reader a
    question was asked that never was.
    """
    mark_entity_undefined(conn, entity_id="company:x", now="2026-08-07")
    row = conn.execute("SELECT * FROM entity_definitions").fetchone()
    assert row["last_attempt_at"] is None
    assert row["attempt_count"] == 0

    mark_entity_undefined(
        conn, entity_id="company:x", now="2026-08-08", run_id="run:1", attempted=True
    )
    row = conn.execute("SELECT * FROM entity_definitions").fetchone()
    assert row["last_attempt_at"] == "2026-08-08"
    assert row["last_attempt_run_id"] == "run:1"
    assert row["attempt_count"] == 1


def test_a_later_pass_does_not_erase_a_recorded_attempt(conn: db.Conn) -> None:
    """The upsert writes every column, so a subsequent pipeline pass would blank
    the attempt record and put the card back to offering a first-time lookup for
    a question already asked and paid for."""
    mark_entity_undefined(
        conn, entity_id="company:x", now="2026-08-07", run_id="run:1", attempted=True
    )
    mark_entity_undefined(conn, entity_id="company:x", now="2026-08-08")

    row = conn.execute("SELECT * FROM entity_definitions").fetchone()
    assert row["last_attempt_at"] == "2026-08-07"
    assert row["last_attempt_run_id"] == "run:1"
    assert row["attempt_count"] == 1


def test_first_seen_survives_a_second_write(conn: db.Conn) -> None:
    store_entity_definition(
        conn,
        entity_id="company:x",
        definition="One.",
        method="in_article",
        confidence=1.0,
        article_id="doc:1",
        quote="One.",
        now="2026-08-01",
    )
    store_entity_definition(
        conn,
        entity_id="company:x",
        definition="Two.",
        method="in_article",
        confidence=1.0,
        article_id="doc:2",
        quote="Two.",
        now="2026-08-07",
    )
    row = conn.execute("SELECT first_seen, last_seen FROM entity_definitions").fetchone()
    assert row["first_seen"] == "2026-08-01"
    assert row["last_seen"] == "2026-08-07"


def test_a_written_definition_carries_articles_and_never_a_quote(conn: db.Conn) -> None:
    """Tier 2 is a SYNTHESIS. It is stored with the ids it drew from and no
    quote, because the card renders a quote as a blockquote -- and a synthesis
    shown as a quotation is exactly the failure invariant 3 exists to prevent."""
    store_entity_definition(
        conn,
        entity_id="company:x",
        definition="A defense technology company supplying autonomous systems.",
        method="in_corpus",
        confidence=0.7,
        source_article_ids=["doc:1", "doc:2"],
        now="2026-08-07",
    )
    row = conn.execute("SELECT * FROM entity_definitions").fetchone()
    assert row["grounding_quote"] is None
    assert row["source_article_ids"] == '["doc:1","doc:2"]'


# ── the evidence ladder ──────────────────────────────────────────────


def test_a_web_lookup_cannot_overwrite_a_sentence_from_an_article(conn: db.Conn) -> None:
    """The entity side of the rule `acronyms.py:641` has had all along.

    Without it, pressing "look this up" on a hover card replaces a sentence
    lifted verbatim from an article we hold with a paraphrase of a web page --
    downgrading the best definition in the database, on purpose, at the user's
    request, with no way to tell afterwards.
    """
    store_entity_definition(
        conn,
        entity_id="company:anduril",
        definition="Anduril Industries is a defense technology company.",
        method="in_article",
        confidence=ENTITY_CONFIDENCE["in_article"],
        article_id="art:1",
        quote="Anduril Industries is a defense technology company.",
        now="t0",
    )

    store_entity_definition(
        conn,
        entity_id="company:anduril",
        definition="A weaker paraphrase found on the web.",
        method="web_grounded",
        confidence=ENTITY_CONFIDENCE["web_grounded"],
        url="https://example.com",
        quote="A weaker paraphrase found on the web.",
        now="t1",
    )

    row = conn.execute(
        "SELECT definition, method, confidence, grounding_article_id, last_seen "
        "FROM entity_definitions WHERE entity_id = 'company:anduril'"
    ).fetchone()

    assert row["method"] == "in_article"
    assert row["confidence"] == ENTITY_CONFIDENCE["in_article"]
    assert row["definition"] == "Anduril Industries is a defense technology company."
    assert row["grounding_article_id"] == "art:1"
    # Still seen, though: the weaker attempt is evidence the entity is live.
    assert row["last_seen"] == "t1"


def test_the_definition_and_its_evidence_travel_together(conn: db.Conn) -> None:
    """The tempting half-measure -- keep the stronger method and quote, take the
    newer definition text -- produces a row asserting in_article provenance for
    a sentence that came off the web, with a quote that does not support it.
    Every column looks individually plausible, which is what makes it bad."""
    store_entity_definition(
        conn,
        entity_id="company:x",
        definition="The original, from an article.",
        method="in_article",
        confidence=ENTITY_CONFIDENCE["in_article"],
        article_id="art:1",
        quote="The original, from an article.",
        now="t0",
    )
    store_entity_definition(
        conn,
        entity_id="company:x",
        definition="Something else entirely.",
        method="web_grounded",
        confidence=ENTITY_CONFIDENCE["web_grounded"],
        quote="Something else entirely.",
        now="t1",
    )

    row = conn.execute(
        "SELECT definition, grounding_quote FROM entity_definitions WHERE entity_id = 'company:x'"
    ).fetchone()

    assert row["definition"] == row["grounding_quote"]


def test_a_stronger_tier_still_replaces_a_weaker_one(conn: db.Conn) -> None:
    """Upward movement is the point. A later run that finds a real defining
    sentence must be able to replace a web paraphrase."""
    store_entity_definition(
        conn,
        entity_id="company:y",
        definition="A web paraphrase.",
        method="web_grounded",
        confidence=ENTITY_CONFIDENCE["web_grounded"],
        now="t0",
    )
    store_entity_definition(
        conn,
        entity_id="company:y",
        definition="A real defining sentence.",
        method="in_article",
        confidence=ENTITY_CONFIDENCE["in_article"],
        article_id="art:9",
        quote="A real defining sentence.",
        now="t1",
    )

    row = conn.execute(
        "SELECT definition, method FROM entity_definitions WHERE entity_id = 'company:y'"
    ).fetchone()

    assert row["method"] == "in_article"
    assert row["definition"] == "A real defining sentence."


def test_a_human_verdict_still_outranks_everything(conn: db.Conn) -> None:
    """`decided_at` is checked before the ladder and is unaffected by it."""
    store_entity_definition(
        conn,
        entity_id="company:z",
        definition="What a person wrote.",
        method="web_grounded",
        confidence=ENTITY_CONFIDENCE["web_grounded"],
        now="t0",
    )
    conn.execute("UPDATE entity_definitions SET decided_at = 't0' WHERE entity_id = 'company:z'")

    store_entity_definition(
        conn,
        entity_id="company:z",
        definition="A machine's better idea.",
        method="in_article",
        confidence=ENTITY_CONFIDENCE["in_article"],
        article_id="art:1",
        quote="A machine's better idea.",
        now="t1",
    )

    row = conn.execute(
        "SELECT definition FROM entity_definitions WHERE entity_id = 'company:z'"
    ).fetchone()
    assert row["definition"] == "What a person wrote."


def test_the_two_tier_tables_agree_while_they_agree(conn: db.Conn) -> None:
    """`ENTITY_TIER_RANK` is deliberately not an import of `acronyms.TIER_RANK`:
    they answer the same question about two different row types, and coupling
    them means a change to one silently changes the other. This pins that they
    currently coincide, so a divergence is a decision someone made rather than a
    surprise someone discovers."""
    from venona.pipeline.acronyms import TIER_RANK

    assert ENTITY_TIER_RANK == TIER_RANK
