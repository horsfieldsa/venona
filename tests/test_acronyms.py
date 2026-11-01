"""Acronym generation and the grounding gate.

The negative cases matter more than the positive ones: a wrong expansion becomes
an alias and corrupts entity resolution downstream, so the gate has to reject
confidently.
"""

from __future__ import annotations

from collections import Counter

import pytest
from helpers import entity
from venona.db import core as db
from venona.db.models import (
    acronym_variants,
    find_parentheticals,
    looks_like_acronym,
    verify_expansion,
)
from venona.pipeline.acronyms import detect, ordinary_words

LEXICON = {"next generation": ["next-gen", "nextgen", "ng"]}


# ── generation ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # Repeated-initial compression is the defence-specific rule generic
        # initialism code misses. This is what makes NGC2 fall out
        # automatically instead of being hand-listed.
        ("Next Generation Command and Control", "NGC2"),
        ("Command and Control", "C2"),
        ("Joint All-Domain Command and Control", "JADC2"),
        ("Command, Control, Communications, Computers, ISR", "C4ISR"),
        ("Modular Open Systems Approach", "MOSA"),
        ("Technology Readiness Level", "TRL"),
    ],
)
def test_acronym_generation(name: str, expected: str) -> None:
    assert expected in acronym_variants(name, lexicon=LEXICON)


def test_the_three_ngc2_spellings_share_a_variant_set() -> None:
    """The motivating case: NGC2 / Next Generation Command and Control /
    next-gen C2 must all be reachable from one canonical name."""
    variants = {
        v.lower() for v in acronym_variants("Next Generation Command and Control", lexicon=LEXICON)
    }
    assert "ngc2" in variants
    assert any("next-gen" in v for v in variants)


def test_looks_like_acronym() -> None:
    assert looks_like_acronym("NGC2")
    assert looks_like_acronym("C4ISR")
    assert looks_like_acronym("MQ-9")
    assert not looks_like_acronym("Anduril")
    assert not looks_like_acronym("a")


# ── the verification gate ────────────────────────────────────────────


def test_verify_accepts_real_expansions() -> None:
    assert verify_expansion("NGC2", "Next Generation Command and Control")
    assert verify_expansion("MOSA", "Modular Open Systems Approach")
    assert verify_expansion("C2", "Command and Control")


@pytest.mark.parametrize(
    ("acronym", "expansion"),
    [
        # A stopword sometimes contributes an initial and sometimes does not.
        ("DOD", "Department of Defense"),
        ("RFI", "Request for Information"),
        ("DOT", "Department of Transportation"),
        # ...and sometimes it is genuinely dropped.
        ("DHS", "Department of Homeland Security"),
        ("C2", "Command and Control"),
        ("JADC2", "Joint All-Domain Command and Control"),
    ],
)
def test_verify_accepts_both_stopword_conventions(acronym: str, expansion: str) -> None:
    """Dropping stopwords unconditionally rejected `DOD` — the most common
    acronym in the corpus — in Tier 1 as well as Tier 3.

    Found by a live Tier-3 lookup of `RFI`, which the gate rejected with
    `initials_mismatch` on a verbatim FAR quote. English is simply inconsistent
    here, so both shapes have to be admitted.
    """
    assert verify_expansion(acronym, expansion)


@pytest.mark.negative
def test_admitting_the_stopword_shape_does_not_weaken_the_gate() -> None:
    """The reason it is safe: the acronym still has to equal a structural
    function of the words. One more shape is not "anything goes"."""
    assert not verify_expansion("DOD", "Defense of Departments Everywhere")
    assert not verify_expansion("RFI", "Really Fancy")
    assert not verify_expansion("DOD", "Department of Defence Something")


@pytest.mark.negative
def test_verify_rejects_fabricated_expansions() -> None:
    """The gate that stops a hallucinated parenthetical becoming an alias."""
    assert not verify_expansion("MOSA", "Modular Open Systems")
    assert not verify_expansion("NGC2", "Fabricated Widget Program")
    assert not verify_expansion("TRL", "Totally Random Letters Here Extra")


@pytest.mark.negative
def test_extraction_rejects_unverifiable_parentheticals() -> None:
    text = (
        "The Army's Next Generation Command and Control (NGC2) effort uses a "
        "Modular Open Systems Approach (MOSA) baseline. Critics cite the "
        "Fabricated Widget Program (XYZ9) claim."
    )
    found = {acronym: expansion for acronym, expansion, _, _ in find_parentheticals(text)}

    assert found["NGC2"] == "Next Generation Command and Control"
    assert found["MOSA"] == "Modular Open Systems Approach"
    assert "XYZ9" not in found, "initials do not match; must be rejected"


def test_extraction_trims_greedy_capture_to_the_verifying_window() -> None:
    """The regex captures leftwards greedily, so "The Army's Next Generation..."
    arrives with lead-in words. Requiring the whole capture to verify would
    reject a CORRECT definition -- this is a regression guard for that bug."""
    text = "The Army's Next Generation Command and Control (NGC2) effort continues."
    ((acronym, expansion, start, end),) = find_parentheticals(text)
    assert acronym == "NGC2"
    assert expansion == "Next Generation Command and Control"
    # The stored span must point at the verified window, not the greedy capture.
    assert text[start:end] == expansion


def test_extraction_handles_all_four_shapes() -> None:
    cases = [
        ("Modular Open Systems Approach (MOSA) is mandated.", "MOSA"),
        ("MOSA (Modular Open Systems Approach) is mandated.", "MOSA"),
        ("TRL, or Technology Readiness Level, gates apply.", "TRL"),
        ("Technology Readiness Level, known as TRL, gates apply.", "TRL"),
    ]
    for text, acronym in cases:
        found = [a for a, _, _, _ in find_parentheticals(text)]
        assert acronym in found, f"failed on: {text}"


def test_expansion_excludes_a_leading_determiner() -> None:
    """ "a Modular Open Systems Approach (MOSA)" must yield the NAME.

    Articles and determiners are stopwords to `acronym_variants`, so the longer
    window verifies just as well and would win by being longer -- storing the
    same concept twice under near-identical strings, which is exactly the
    duplication the alias system exists to prevent.
    """
    for text, expected in [
        (
            "The service is mandating a Modular Open Systems Approach (MOSA) baseline.",
            "Modular Open Systems Approach",
        ),
        (
            "It uses the Next Generation Command and Control (NGC2) stack.",
            "Next Generation Command and Control",
        ),
        ("Their Technology Readiness Level (TRL) gates apply.", "Technology Readiness Level"),
    ]:
        found = dict((a, e) for a, e, _, _ in find_parentheticals(text))
        acronym = next(iter(found))
        assert found[acronym] == expected, f"got {found[acronym]!r} from {text!r}"


def test_possessive_is_not_part_of_the_name() -> None:
    """ "Federal Emergency Management Agency's (FEMA)" must not create a second
    glossary row for the same organisation."""
    found = dict(
        (a, e)
        for a, e, _, _ in find_parentheticals(
            "The Federal Emergency Management Agency's (FEMA) role expanded."
        )
    )
    assert found["FEMA"] == "Federal Emergency Management Agency"


@pytest.mark.negative
def test_numbering_schemes_are_not_definitions() -> None:
    """ "Division I (DI)" is a numbering scheme, not a definition worth having.

    Judged on the EXPANSION rather than the acronym: "CIV" and "MIL" are valid
    Roman numerals *and* real abbreviations, so rejecting by acronym shape would
    throw away good definitions. Only the thing being numbered gives it away.
    """
    assert not find_parentheticals("They compete in Division I (DI) athletics.")
    assert not find_parentheticals("The Division II (DII) programs differ.")

    # Meanwhile a genuine acronym is unaffected.
    found = dict(
        (a, e)
        for a, e, _, _ in find_parentheticals("The Modular Open Systems Approach (MOSA) applies.")
    )
    assert found.get("MOSA") == "Modular Open Systems Approach"


# ── the detector's junk filter ───────────────────────────────────────


def test_a_dateline_is_not_an_acronym() -> None:
    """`ACRONYM_TOKEN` is a shape regex, so every capitalised word in a wire
    dateline matched it. Measured over the live corpus, 46 surfaces cleared the
    Tier-3 gate and roughly a third were not acronyms at all -- LONDON, PARIS,
    WARSAW, DAYTON. Each would have spent a WebSearch and a WebFetch trying to
    define an English place name.
    """
    text = (
        "LONDON — The programme continues. London has committed further funds, "
        "and the ISR requirement remains unchanged."
    )
    found = detect(text, stoplist=set(), short_whitelist=set())

    assert "LONDON" not in found, "the corpus itself says this is a word, not an acronym"
    assert "ISR" in found, "a real acronym must survive the filter"


def test_a_shouted_english_word_is_not_an_acronym() -> None:
    """HAVE, AIR, HEAT, ATLAS, VISTA -- headline capitals, all live findings."""
    text = "HEAT AND AIR. The heat is on and the air defence budget grew. C4ISR spending held."
    found = detect(text, stoplist=set(), short_whitelist=set())

    assert "HEAT" not in found
    assert "AIR" not in found
    assert "C4ISR" in found


def test_mixed_case_is_not_evidence_of_an_ordinary_word() -> None:
    """`DoD` is a STYLING of an acronym. Counting it as an ordinary word would
    suppress `DOD` -- the opposite of what the filter is for."""
    text = "The DoD confirmed it. DOD officials said the DOD review is ongoing."
    assert "DOD" in detect(text, stoplist=set(), short_whitelist=set())


def test_one_stray_proper_noun_does_not_suppress_an_acronym() -> None:
    """The VICTOR regression, at the gate that actually killed it.

    `run_acronyms` accumulated `ordinary_words` into a SET, so a single
    title-cased word anywhere in the corpus suppressed the shouted form
    everywhere. A Haitian ex-parliamentarian named `Prophane Victor`, in an
    article about Haiti, is why the Army's VICTOR programme was never once
    detected -- upstream of the skip set, so it never reached any tier and
    never appeared in `surfaces_seen`.
    """
    corpus = [
        "The Army is developing VICTOR, an AI knowledge platform. VICTOR is in testing.",
        "The sanctions committee designated former parliamentarian Prophane Victor.",
    ]
    df = Counter()
    for text in corpus:
        df.update(ordinary_words(text))
    assert df["VICTOR"] == 1, "one document, one coincidence"

    # As a set (the old behaviour), one sighting is enough to suppress it.
    as_a_set = {word for word, n in df.items() if n >= 1}
    assert "VICTOR" not in detect(
        corpus[0], stoplist=set(), short_whitelist=set(), ordinary=as_a_set
    )

    # Counting documents, a coincidence is not evidence.
    by_document = {word for word, n in df.items() if n >= 2}
    assert (
        detect(corpus[0], stoplist=set(), short_whitelist=set(), ordinary=by_document)["VICTOR"]
        == 2
    )


def test_a_genuinely_shouted_word_is_still_suppressed_by_recurrence() -> None:
    """The other half, and the reason the threshold is 2 rather than removed.

    Datelines and shouted English recur -- that is what separates them from a
    one-off surname. Measured on the live corpus: HAVE in 246 documents, AIR
    117, HEAT 13, LONDON 8, DAYTON 7, PARIS 6. Every one stays suppressed.
    """
    corpus = [
        "LONDON — The ministry confirmed the order.",
        "Officials in London said the review continues.",
        "A supplier in London won the contract.",
    ]
    df = Counter()
    for text in corpus:
        df.update(ordinary_words(text))
    by_document = {word for word, n in df.items() if n >= 2}

    assert df["LONDON"] == 2, "two documents use it as an ordinary word"
    assert "LONDON" not in detect(
        corpus[0], stoplist=set(), short_whitelist=set(), ordinary=by_document
    )


def test_an_acronym_never_written_as_a_word_survives() -> None:
    text = "The NGC2 programme and the PNT requirement are both funded."
    found = detect(text, stoplist=set(), short_whitelist=set())
    assert "NGC2" in found
    assert "PNT" in found


# ── a human verdict outranks re-grounding ────────────────────────────


def test_a_rejected_definition_is_not_resurrected(conn) -> None:
    """The bug behind "clean up accept/reject".

    `store_definition` upserts on `key=["id"]` and wrote `status`
    unconditionally, so the next time the acronyms stage re-grounded a surface
    somebody had rejected, it flipped straight back to `active` and reappeared
    in briefs. The curator's only recourse was to reject it again, every night,
    for as long as the corpus kept mentioning it.
    """
    from venona.pipeline.acronyms import store_definition

    store_definition(
        conn,
        surface="XYZ",
        expansion="Experimental Yield Zone",
        method="in_article",
        confidence=1.0,
        article_id="doc:1",
        quote="The Experimental Yield Zone (XYZ) programme.",
        now="t0",
    )
    entry_id = conn.execute(
        "SELECT id FROM acronym_definitions WHERE normalized = 'xyz'"
    ).fetchone()["id"]

    # A curator rejects it. `decided_at` is what records that a person ruled.
    conn.execute(
        "UPDATE acronym_definitions SET status='rejected', decided_at='t1' WHERE id=?",
        (entry_id,),
    )
    conn.commit()

    # The stage re-grounds the same surface on a later run.
    store_definition(
        conn,
        surface="XYZ",
        expansion="Experimental Yield Zone",
        method="in_article",
        confidence=1.0,
        article_id="doc:2",
        quote="Another article mentioning the Experimental Yield Zone (XYZ).",
        now="t2",
    )

    row = conn.execute(
        "SELECT status FROM acronym_definitions WHERE id = ?", (entry_id,)
    ).fetchone()
    assert row["status"] == "rejected", "a human verdict must survive the pipeline"


def test_an_undecided_definition_still_updates_normally(conn) -> None:
    """The guard keys on `decided_at`, so rows nobody has reviewed keep behaving
    exactly as before — re-grounding is how definitions improve."""
    from venona.pipeline.acronyms import store_definition

    store_definition(
        conn,
        surface="QRS",
        expansion="Quick Reaction System",
        method="in_article",
        confidence=1.0,
        article_id="doc:1",
        quote="The Quick Reaction System (QRS).",
        now="t0",
    )
    row = conn.execute(
        "SELECT status, decided_at FROM acronym_definitions WHERE normalized='qrs'"
    ).fetchone()
    assert row["status"] == "active"
    assert row["decided_at"] is None


# ── the skip set: "already resolves" means the INDEX, not the table ──


def _alias(conn, entity_id: str, surface: str, added_by: str = "extraction") -> None:
    conn.execute(
        "INSERT INTO aliases (id, entity_id, surface, normalized, strictness, "
        "is_acronym, added_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (f"{entity_id}|{surface}", entity_id, surface, surface.lower(), 2, 1, added_by, "t0"),
    )


def test_a_provisional_entitys_alias_is_annotated_and_therefore_claimed(conn) -> None:
    """The VICTOR regression, from the other side.

    The incident was a surface CLAIMED by the skip set and matched by nothing:
    `analyze` mints a provisional entity and its alias before the acronyms stage
    runs, the skip set had no status filter, and `build_index` filtered
    provisional out -- so VICTOR ended up with no `acronym_definitions` row at
    all: not grounded, not undefined, not in the review queue, and not rescuable
    from the hover card.

    The index now carries provisional entities, so the gap shuts the other way
    round: the surface is skipped BECAUSE it is annotated, rather than skipped
    and abandoned. The thing that must never happen -- claimed and unmatchable --
    is what `test_the_skip_set_is_exactly_what_the_index_matches` asserts, and
    it is now structural: both callers read `_entity_entries`.
    """
    from venona.services.alias_index import build_index, indexed_entity_surfaces

    entity(conn, "prog:victor", "VICTOR", "provisional")
    _alias(conn, "prog:victor", "VICTOR")
    conn.commit()

    assert "VICTOR" in indexed_entity_surfaces(conn)
    # ... and the reason it may be skipped: something DOES match it now.
    matched = build_index(conn).find("The Army is developing VICTOR, a platform.")
    assert [m.ref for m in matched if m.surface == "VICTOR"] == ["prog:victor"]


def test_a_provisional_alias_colliding_with_a_glossary_row_is_dropped_from_both(conn) -> None:
    """The collision admitting provisional entities would otherwise create.

    204 surfaces in the live corpus have both a provisional entity alias and a
    glossary row -- `A400M`, `ACE`, `AIR` -- every one an acronym `analyze`
    minted an entity for after the acronyms stage had already written a row.
    Indexed together they carry the same text, the same length and the same
    strictness, and `_resolve_overlaps` breaks that tie by INSERTION ORDER, so
    which one a span resolves to would depend on row order.

    The glossary row wins, because the acronyms stage only minted it while
    nothing else claimed the surface. The alias leaves the index and the skip
    set TOGETHER -- dropping it from one side only is the drift that made
    VICTOR.
    """
    from venona.pipeline.acronyms import mark_undefined
    from venona.services.alias_index import build_index, indexed_entity_surfaces

    entity(conn, "prog:delta", "DELTA", "provisional")
    _alias(conn, "prog:delta", "DELTA")
    mark_undefined(conn, "DELTA", now="t0", sightings=3, articles=1)
    conn.commit()

    assert "DELTA" not in indexed_entity_surfaces(conn)
    entries = [e for e in build_index(conn)._entries if e.surface == "DELTA"]
    assert [e.kind for e in entries] == ["undefined"]


def test_an_active_entitys_alias_does_claim_its_surface(conn) -> None:
    """The other half. Skipping these is not a bug, it is the point -- the
    surface is annotated as an entity, so a glossary row would match twice."""
    from venona.services.alias_index import indexed_entity_surfaces

    entity(conn, "prog:zulu", "ZULU", "active")
    _alias(conn, "prog:zulu", "ZULU")
    conn.commit()

    assert "ZULU" in indexed_entity_surfaces(conn)


def test_a_merged_alias_claims_its_surface_while_its_survivor_is_active(conn) -> None:
    """`merged` is in the index deliberately, repointed at the survivor, so the
    skip set must agree. If the survivor is not active the index drops it, and
    the skip set has to drop it too or the surface falls in the same gap."""
    from venona.services.alias_index import indexed_entity_surfaces

    entity(conn, "prog:alpha", "ALPHA", "merged")
    _alias(conn, "prog:alpha", "ALPHA")
    entity(conn, "prog:bravo", "BRAVO", "active")
    conn.execute(
        "INSERT INTO entity_mappings (from_id, to_id, merge_id, active, created_at) "
        "VALUES (?,?,?,?,?)",
        ("prog:alpha", "prog:bravo", "merge:1", 1, "t0"),
    )
    conn.commit()
    assert "ALPHA" in indexed_entity_surfaces(conn)

    # Survivor demoted to `provisional`: still ANNOTATED, so still claimed. This
    # half used to assert the opposite, because provisional entities were not in
    # the index at all; the alias follows whatever its survivor's own aliases do.
    conn.execute("UPDATE entities SET status='provisional' WHERE id='prog:bravo'")
    conn.commit()
    assert "ALPHA" in indexed_entity_surfaces(conn)

    # Survivor no longer annotated at all: the index cannot point at it, so
    # neither may the skip set -- ALPHA becomes eligible for a glossary entry
    # again. This is the half that guards the gap.
    conn.execute("UPDATE entities SET status='dormant' WHERE id='prog:bravo'")
    conn.commit()
    assert "ALPHA" not in indexed_entity_surfaces(conn)


def test_the_skip_set_is_exactly_what_the_index_matches(conn) -> None:
    """The invariant the two callers exist to share. Asserted against the real
    seed rather than a hand-built pair, because the failure mode is a filter
    that drifts on one side only.

    `strictness > 0` is part of the claim, not a convenience: strictness 0 is
    "never auto-matched", so those entries exist in `_entries` and match
    nothing. `ARMY`, `TITAN` and `SDA` are in the seed at 0 -- skipping them
    would leave them unannotated AND ungrounded, the exact VICTOR gap.
    """
    from venona.services.alias_index import build_index, indexed_entity_surfaces

    entity(conn, "prog:charlie", "CHARLIE", "provisional")
    _alias(conn, "prog:charlie", "CHARLIE")
    conn.commit()

    matchable = {
        e.surface.upper()
        for e in build_index(conn)._entries
        if e.kind == "entity" and e.strictness > 0
    }
    assert indexed_entity_surfaces(conn) == matchable
    assert "ARMY" not in indexed_entity_surfaces(conn), "strictness 0 never matches"


def test_a_promoted_entity_leaves_one_index_entry_not_two(conn) -> None:
    """The collision this fix would otherwise create.

    VICTOR's sequence is: provisional entity -> no glossary row was possible
    (the old skip set) -> now an `undefined` row -> the entity is later promoted
    to active. At that point the alias and the undefined glossary row are both
    in the index for the same surface, same length, both strictness 2 and
    upper-case. `_resolve_overlaps` breaks that tie by insertion order, so the
    span's `kind` would depend on row ordering -- a non-deterministic render.
    """
    from venona.pipeline.acronyms import link_to_entities, mark_undefined
    from venona.services.alias_index import build_index

    entity(conn, "prog:victor", "VICTOR", "provisional")
    _alias(conn, "prog:victor", "VICTOR")
    mark_undefined(conn, "VICTOR", now="t0", sightings=7, articles=1)
    conn.commit()

    # While provisional: the glossary row is the ONLY thing annotating VICTOR.
    entries = [e for e in build_index(conn)._entries if e.surface == "VICTOR"]
    assert [e.kind for e in entries] == ["undefined"]

    # Promoted. Without the claim below there would now be two.
    conn.execute("UPDATE entities SET status='active' WHERE id='prog:victor'")
    link_to_entities(conn)
    conn.commit()

    entries = [e for e in build_index(conn)._entries if e.surface == "VICTOR"]
    assert [e.kind for e in entries] == ["entity"], "the entity wins; the row steps aside"

    # Stepping aside is not deletion -- the audit trail survives.
    row = conn.execute(
        "SELECT status, entity_id FROM acronym_definitions WHERE normalized='victor'"
    ).fetchone()
    assert row["entity_id"] == "prog:victor"
    assert row["status"] == "undefined"


def test_a_grounded_definition_is_never_claimed_by_surface(conn) -> None:
    """The `expansion IS NULL` guard. Claiming a row that HAS an expansion would
    silently pull a grounded definition out of the index."""
    from venona.pipeline.acronyms import link_to_entities, store_definition

    entity(conn, "prog:mosa", "MOSA", "active")
    _alias(conn, "prog:mosa", "MOSA")
    store_definition(
        conn,
        surface="MOSA",
        expansion="Modular Open Systems Approach",
        method="in_article",
        confidence=1.0,
        article_id="doc:1",
        quote="A Modular Open Systems Approach (MOSA) baseline.",
        now="t0",
    )
    conn.commit()
    link_to_entities(conn)

    row = conn.execute(
        "SELECT expansion, status FROM acronym_definitions WHERE normalized='mosa'"
    ).fetchone()
    assert row["expansion"] == "Modular Open Systems Approach"
    assert row["status"] == "active", "a grounded definition is untouched"


def test_every_detected_surface_lands_in_exactly_one_bucket() -> None:
    """`resolved_by_entity` exists so the skip is an outcome rather than a
    silent drop. Without it a surface could leave `surfaces_seen` and appear in
    no tier, which is precisely how the VICTOR miss stayed invisible."""
    from venona.pipeline.acronyms import AcronymReport

    fields = AcronymReport().as_dict()
    assert "resolved_by_entity" in fields

    # The tier outcomes a surface can reach after `surfaces_seen`.
    outcomes = {"resolved_by_entity", "grounded_in_corpus", "grounded_web", "undefined", "rejected"}
    assert outcomes <= set(fields)

    # The real numbers from the first run after the fix, which is the shape the
    # identity has to hold for: 57 + 147 + 5 + 554 == 763.
    report = AcronymReport(
        surfaces_seen=763,
        grounded_in_article=287,  # events, not a bucket -- deliberately excluded
        resolved_by_entity=57,
        grounded_in_corpus=147,
        grounded_web=5,
        undefined=554,
        rejected=3,  # a subset of `undefined`, also excluded
        lookups_attempted=8,
    )
    assert report.reconciles()

    # A surface dropped without an outcome is exactly what this catches.
    report.resolved_by_entity -= 1
    assert not report.reconciles()


# ── the tier-3 call shape ────────────────────────────────────────────


def test_a_web_grounded_definition_needs_no_declared_confidence(
    conn: db.Conn,
) -> None:
    """⭐ The exact call the four tier-3 sites make.

    They used to pass `confidence=0.7`, which `confidence_for` discards for
    web_grounded -- it computes the number from the corroboration count
    instead, and 0.7 is the pre-0018 constant migration 0019 exists to erase
    from the data. Removing the argument was right; leaving the PARAMETER
    required was not, and `store_definition() missing 1 required keyword-only
    argument` took out the whole acronyms stage of a live run.

    Nothing offline reached this: the grounding tests fake `call_and_record`
    and stop short of the write, so the first thing to execute this line was
    the pipeline itself.
    """
    from venona.pipeline.acronyms import store_definition

    store_definition(
        conn,
        surface="ABC",
        expansion="Advanced Battle Control",
        method="web_grounded",
        article_id=None,
        url="https://example.com/abc",
        quote="Advanced Battle Control (ABC) is a programme.",
        now="t0",
    )

    row = conn.execute(
        "SELECT method, confidence FROM acronym_definitions WHERE normalized = 'abc'"
    ).fetchone()
    assert row["method"] == "web_grounded"
    # 0.55 base for one source -- computed, not declared.
    assert row["confidence"] == 0.55


@pytest.mark.negative
def test_a_non_web_tier_must_still_declare_its_confidence(conn: db.Conn) -> None:
    """The other half. An `in_article` expansion's 1.0 is a real decision its
    caller makes, so making the parameter optional must not let that one slip
    through as NULL."""
    from venona.pipeline.acronyms import store_definition

    with pytest.raises(ValueError, match="requires an explicit confidence"):
        store_definition(
            conn,
            surface="DEF",
            expansion="Defence Experimental Framework",
            method="in_article",
            article_id="doc:1",
            quote="The Defence Experimental Framework (DEF).",
            now="t0",
        )
