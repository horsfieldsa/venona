"""Entity resolution: the S4 gates.

This is the highest-risk component in the system, and its failures are quiet — a
wrongly-merged entity produces a graph that looks fine and answers questions
incorrectly. So the negative tests matter more than the positive ones: what the
resolver *declines* to do is most of its value.
"""

from __future__ import annotations

import pytest
from venona.db import core as db
from venona.db.models import normalize
from venona.pipeline.resolve import Resolver, promote_provisionals
from venona.settings import get_settings

LEXICON = {"next generation": ["next-gen", "nextgen", "ng"], "command and control": ["c2"]}
NGC2 = "prog:next-generation-command-and-control"


def resolver(conn: db.Conn) -> Resolver:
    return Resolver(conn, settings=get_settings(), lexicon=LEXICON)


# ── THE motivating gate ──────────────────────────────────────────────


def test_three_ngc2_spellings_resolve_to_one_id(conn: db.Conn) -> None:
    """The question that started the project: NGC2, the full name, and
    "next-gen C2" must all be the same node."""
    r = resolver(conn)
    ids = {
        r.resolve(surface, "Program", now="n").entity_id
        for surface in [
            "NGC2",
            "Next Generation Command and Control",
            "next-generation command and control",
            "Next-Gen C2",
        ]
    }
    assert ids == {NGC2}, f"expected one id, got {ids}"


def test_case_and_punctuation_variants_resolve(conn: db.Conn) -> None:
    r = resolver(conn)
    for surface in ["anduril industries", "Anduril Industries, Inc.", "Anduril"]:
        assert r.resolve(surface, "Company", now="n").entity_id == "company:anduril-industries"


# ── what it declines to GENERATE ─────────────────────────────────────


def _generated(conn: db.Conn, entity_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT surface FROM aliases WHERE entity_id = ? AND added_by = 'acronym_gen'",
        (entity_id,),
    ).fetchall()
    return {row["surface"] for row in rows}


def test_a_person_carries_no_generated_acronyms(conn: db.Conn) -> None:
    """Nobody writes a person's initials as an abbreviation.

    The generator read `Adm. Brad Cooper` as `ABC` and `Bassam al-Hassan` as
    `BAH`, at the tier reserved for all-caps acronyms, so "the ABC network" was
    underlined as an admiral and `BAH` competed with Booz Allen Hamilton. The
    ontology declares which of its own types are abbreviated; `Person` is not.
    """
    r = resolver(conn)
    person = r.resolve("Adm. Brad Cooper", "Person", now="n")
    program = r.resolve("Agile Combat Employment", "Program", now="n")
    conn.commit()

    assert _generated(conn, person.entity_id or "") == set()
    assert "ACE" in _generated(conn, program.entity_id or ""), (
        "a type the ontology does abbreviate must be unaffected"
    )


def test_a_variant_carrying_punctuation_is_never_written(conn: db.Conn) -> None:
    """`"U.S.".isupper()` is True, so the generator reads it as an
    already-acronym tail and pastes it in whole: `U.S. Space Command` yields
    `SCU.S.` and `U.S. Navy` yields `NU.S.`. No publication prints either, and
    both were shown to readers in the entity's own alias list."""
    r = resolver(conn)
    command = r.resolve("U.S. Space Command", "MilitaryUnit", now="n")
    conn.commit()

    written = _generated(conn, command.entity_id or "")
    assert written, "the entity should still generate the well-formed variants"
    assert not [s for s in written if not s.isalnum()], (
        f"a generated acronym is capitals and digits; got {sorted(written)}"
    )


# ── what it declines to do ───────────────────────────────────────────


@pytest.mark.negative
def test_ambiguous_surfaces_are_never_auto_resolved(conn: db.Conn) -> None:
    """SDA is Space Development Agency AND software-defined anything; TITAN is
    a program AND a commercial product line. Assigning either to one owner is
    worse than leaving it unlinked."""
    r = resolver(conn)
    for surface, entity_type in [
        ("SDA", "Company"),
        ("TITAN", "Program"),
        ("EW", "Technology"),
    ]:
        resolution = r.resolve(surface, entity_type, now="n")
        assert resolution.entity_id is None, f"{surface} must not auto-resolve"
        assert resolution.method == "ambiguous"


def _ambiguous_with_readings(conn: db.Conn, surface: str, readings: list[str]) -> Resolver:
    """A surface the corpus declares ambiguous, with candidate meanings."""
    db.upsert(
        conn,
        "ambiguous_surfaces",
        {
            "normalized": normalize(surface),
            "reason": "test",
            "readings": db.jdump(readings),
            "created_at": "2026-01-01T00:00:00Z",
        },
        key=["normalized"],
    )
    conn.commit()
    return resolver(conn)


def test_a_declared_reading_named_in_the_document_is_taken(conn: db.Conn) -> None:
    """The writer spelled it out once and then used the short form, which is
    exactly why the short form is ambiguous. Reading the same document is enough
    to know which one they meant."""
    r = _ambiguous_with_readings(conn, "the service", ["unit:u-s-army", "unit:u-s-navy"])
    resolution = r.resolve(
        "the service",
        "MilitaryUnit",
        now="n",
        context="The United States Army said on Tuesday that the service would field it.",
    )
    assert resolution.entity_id == "unit:u-s-army"


@pytest.mark.negative
def test_two_readings_named_in_one_document_decline(conn: db.Conn) -> None:
    """A document about both is a document about the ambiguity. Picking the
    first, or the better-corroborated one, would be picking by prior rather than
    by what the writer said."""
    r = _ambiguous_with_readings(conn, "the service", ["unit:u-s-army", "unit:u-s-navy"])
    resolution = r.resolve(
        "the service",
        "MilitaryUnit",
        now="n",
        context="The United States Army and the United States Navy will exercise together.",
    )
    assert resolution.entity_id is None
    assert resolution.method == "ambiguous"


@pytest.mark.negative
def test_a_document_naming_no_reading_declines(conn: db.Conn) -> None:
    """Silence is the case this guard was built for, and it still declines. A
    surface with readings must not become a surface that guesses."""
    r = _ambiguous_with_readings(conn, "the service", ["unit:u-s-army", "unit:u-s-navy"])
    for context in ("", "It is buying more of them."):
        resolution = r.resolve("the service", "MilitaryUnit", now="n", context=context)
        assert resolution.entity_id is None, f"{context!r} names no reading"
        assert resolution.method == "ambiguous"


@pytest.mark.negative
def test_a_reading_of_another_type_is_not_a_reading(conn: db.Conn) -> None:
    """Scoped to the requested type like every other rung: `Titan` is a Program
    and a Platform, and the wrong-typed one is not a candidate at all."""
    r = _ambiguous_with_readings(conn, "the service", ["unit:u-s-army", "unit:u-s-navy"])
    resolution = r.resolve(
        "the service", "Company", now="n", context="The United States Army bought it."
    )
    assert resolution.entity_id is None


@pytest.mark.negative
def test_a_reading_matches_on_whole_words_only(conn: db.Conn) -> None:
    """The evidence test is a phrase search, so it has to respect word
    boundaries: `Iran` inside `Irani` would let an unrelated name vote for a
    reading nobody wrote."""
    from venona.pipeline.resolve import _searchable

    assert " iran " in _searchable("tension with Iran continued")
    assert " iran " not in _searchable("Mohammad Irani was named")


@pytest.mark.negative
def test_an_ambiguous_surface_with_no_readings_still_declines(conn: db.Conn) -> None:
    """The default is unchanged. Nothing about this feature makes a surface
    somebody declared ambiguous start resolving on its own."""
    r = resolver(conn)
    resolution = r.resolve(
        "SDA", "Company", now="n", context="The Space Development Agency said so."
    )
    assert resolution.entity_id is None
    assert resolution.method == "ambiguous"


@pytest.mark.negative
def test_close_fuzzy_candidates_decline_rather_than_guess(conn: db.Conn) -> None:
    """Two candidates within the margin means the surface is ambiguous here, not
    that the first one wins. This is the case LLM adjudication would settle."""
    now = "n"
    for name in ["Sentinel Alpha Systems", "Sentinel Alpha Solutions"]:
        conn.execute(
            "INSERT INTO entities (id, type, name, canonical_key, status, first_seen, "
            "last_seen, created_at) VALUES (?,?,?,?,'active',?,?,?)",
            (
                f"company:{name.lower().replace(' ', '-')}",
                "Company",
                name,
                name.lower(),
                now,
                now,
                now,
            ),
        )
    r = resolver(conn)
    resolution = r.resolve("Sentinel Alpha Systm", "Company", now=now)
    assert resolution.method in {"ambiguous", "provisional"}
    # Either way it must NOT have picked one of the two near-identical names.
    assert resolution.entity_id not in {
        "company:sentinel-alpha-systems",
        "company:sentinel-alpha-solutions",
    }


@pytest.mark.negative
def test_type_scoping_prevents_cross_type_collisions(conn: db.Conn) -> None:
    """A surface owned by a Program must not satisfy a Platform lookup."""
    r = resolver(conn)
    program = r.resolve("Replicator", "Program", now="n")
    assert program.entity_id == "prog:replicator"

    platform = r.resolve("Replicator", "Platform", now="n")
    assert platform.entity_id != "prog:replicator"


# ── alias conflicts ──────────────────────────────────────────────────


@pytest.mark.negative
def test_alias_conflict_files_a_row_and_does_not_merge(conn: db.Conn) -> None:
    """An automatic merge here would be invisible and would poison every
    downstream query, so a contested surface becomes a review item instead."""
    now = "n"
    conn.execute(
        "INSERT INTO entities (id, type, name, canonical_key, status, first_seen, "
        "last_seen, created_at) VALUES "
        "('company:rival-corp','Company','Rival Corp','rival corp',?,?,?,?)",
        ("active", now, now, now),
    )
    r = resolver(conn)

    # "Anduril" already belongs to company:anduril-industries.
    assert r._add_alias("company:rival-corp", "Anduril", "extraction", now=now) is False

    row = conn.execute("SELECT * FROM alias_conflicts WHERE normalized = 'anduril'").fetchone()
    assert row is not None
    assert row["existing_entity_id"] == "company:anduril-industries"
    assert row["proposed_entity_id"] == "company:rival-corp"

    # And the original mapping is untouched.
    assert r.resolve("Anduril", "Company", now=now).entity_id == "company:anduril-industries"


@pytest.mark.negative
def test_short_aliases_are_rejected(conn: db.Conn) -> None:
    """Two-character surfaces collide with everything."""
    r = resolver(conn)
    assert r._add_alias(NGC2, "N2", "extraction", now="n") is False


# ── provisional minting and the promotion gate ───────────────────────


def test_unknown_surface_mints_provisional_and_stays_hidden(
    conn: db.Conn,
) -> None:
    """A one-off model hallucination must never reach the graph."""
    r = resolver(conn)
    resolution = r.resolve("Fictional Defense Widgets", "Company", now="n")

    assert resolution.ok
    assert resolution.method == "provisional"
    assert resolution.created

    row = conn.execute(
        "SELECT status FROM entities WHERE id = ?", (resolution.entity_id,)
    ).fetchone()
    assert row["status"] == "provisional", "must not be active on first sight"

    promoted = promote_provisionals(conn, now="n")
    assert resolution.entity_id not in promoted, "no assertions yet, so nothing to corroborate"


def test_provisional_promotes_on_two_sources(conn: db.Conn) -> None:
    now = "n"
    r = resolver(conn)
    resolution = r.resolve("Emerging Systems Group", "Company", now=now)
    entity_id = resolution.entity_id

    # Two feeds AND two publishers. Corroboration counts `site_domain`, because
    # one outlet reaches us through several feeds -- see
    # `test_two_feeds_from_one_publisher_are_not_two_sources`.
    for i, (source, domain) in enumerate(
        [("breaking_defense", "breakingdefense.com"), ("defense_news", "defensenews.com")]
    ):
        conn.execute(
            "INSERT INTO documents (id, source_id, site_domain, url, url_key, title, text_nfc, "
            "content_hash, cluster_id, fetched_at, created_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"doc:{i}",
                source,
                domain,
                f"https://{domain}/{i}",
                f"https://{domain}/{i}",
                "T",
                "body",
                f"h{i}",
                f"cluster:{i}",
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO assertions (id, run_id, article_id, cluster_id, kind, surface, "
            "entity_type, entity_id_raw, evidence_quote, confidence, method, created_at) "
            "VALUES (?,?,?,?,'entity',?,?,?,?,?,'extraction',?)",
            (
                f"a{i}",
                "r",
                f"doc:{i}",
                f"cluster:{i}",
                "Emerging Systems Group",
                "Company",
                entity_id,
                "quoted sentence",
                0.7,
                now,
            ),
        )

    promoted = promote_provisionals(conn, now=now)
    assert entity_id in promoted

    row = conn.execute("SELECT status FROM entities WHERE id = ?", (entity_id,)).fetchone()
    assert row["status"] == "active"


@pytest.mark.negative
def test_single_source_repetition_does_not_promote(conn: db.Conn) -> None:
    """One outlet repeating itself is not corroboration."""
    now = "n"
    r = resolver(conn)
    entity_id = r.resolve("Solo Sighting Corp", "Company", now=now).entity_id

    for i in range(3):
        conn.execute(
            "INSERT INTO documents (id, source_id, url, url_key, title, text_nfc, "
            "content_hash, cluster_id, fetched_at, created_at) VALUES "
            "(?,'one_outlet',?,?,?,?,?,?,?,?)",
            (
                f"doc:s{i}",
                f"https://e.com/s{i}",
                f"https://e.com/s{i}",
                "T",
                "body",
                f"hs{i}",
                f"cluster:s{i}",
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO assertions (id, run_id, article_id, cluster_id, kind, surface, "
            "entity_type, entity_id_raw, evidence_quote, confidence, method, created_at) "
            "VALUES (?,?,?,?,'entity',?,?,?,?,?,'extraction',?)",
            (
                f"as{i}",
                "r",
                f"doc:s{i}",
                f"cluster:s{i}",
                "Solo Sighting Corp",
                "Company",
                entity_id,
                "quote",
                0.6,
                now,
            ),
        )

    assert entity_id not in promote_provisionals(conn, now=now)


def test_minting_generates_acronym_aliases(conn: db.Conn) -> None:
    """A newly-minted long name must resolve its own acronym next time, without
    anyone hand-listing it."""
    r = resolver(conn)
    minted = r.resolve("Joint Rapid Acquisition Cell", "Company", now="n")

    aliases = {
        row["surface"]
        for row in conn.execute(
            "SELECT surface FROM aliases WHERE entity_id = ?", (minted.entity_id,)
        )
    }
    assert "JRAC" in aliases

    assert r.resolve("JRAC", "Company", now="n").entity_id == minted.entity_id


def test_relation_endpoints_do_not_mint(conn: db.Conn) -> None:
    """An entity that appears only in a relationship is weak evidence -- resolve
    it if known, but never create it."""
    r = resolver(conn)
    resolution = r.resolve("Never Seen Before Inc", "Company", now="n", allow_mint=False)
    assert resolution.entity_id is None
    assert resolution.method == "rejected"


def test_resolution_is_stable_across_resolver_instances(conn: db.Conn) -> None:
    """A surface resolved in one run must resolve the same way in the next --
    otherwise the graph churns."""
    first = resolver(conn).resolve("Adaptive Sensing Labs", "Company", now="n")
    second = resolver(conn).resolve("Adaptive Sensing Labs", "Company", now="n")
    assert first.entity_id == second.entity_id
    assert second.method == "exact_alias", "the second lookup should hit the alias table"


@pytest.mark.negative
def test_model_confidence_alone_never_promotes(conn: db.Conn) -> None:
    """Self-reported model confidence is NOT calibrated.

    Measured on a real 15-article corpus, allowing a single >=0.9-confidence
    extraction to promote produced 48 active entities against 6 genuinely
    corroborated ones -- single-mention noise like "Chaos 1" and "COMSPOC"
    became graph nodes. Corroboration is the only path.
    """
    now = "n"
    r = resolver(conn)
    entity_id = r.resolve("Supremely Confident Corp", "Company", now=now).entity_id

    conn.execute(
        "INSERT INTO documents (id, source_id, url, url_key, title, text_nfc, "
        "content_hash, cluster_id, fetched_at, created_at) VALUES "
        "('doc:c','one_outlet','https://e.com/c','https://e.com/c','T','body',"
        "'hc','cluster:c',?,?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO assertions (id, run_id, article_id, cluster_id, kind, surface, "
        "entity_type, entity_id_raw, evidence_quote, confidence, method, created_at) "
        "VALUES ('ac','r','doc:c','cluster:c','entity','Supremely Confident Corp',"
        "'Company',?,'quote',1.0,'extraction',?)",
        (entity_id, now),
    )

    assert entity_id not in promote_provisionals(conn, now=now)
    row = conn.execute("SELECT status FROM entities WHERE id=?", (entity_id,)).fetchone()
    assert row["status"] == "provisional"


def test_reconcile_demotes_an_entity_that_lost_its_evidence(conn: db.Conn) -> None:
    """Status is derived from evidence, so it must be able to move both ways --
    otherwise a retraction leaves a stale active node behind."""
    now = "n"
    r = resolver(conn)
    entity_id = r.resolve("Transient Systems", "Company", now=now).entity_id
    conn.execute("UPDATE entities SET status='active' WHERE id=?", (entity_id,))

    promote_provisionals(conn, now=now)
    row = conn.execute("SELECT status FROM entities WHERE id=?", (entity_id,)).fetchone()
    assert row["status"] == "provisional", "no corroborating assertions -> not active"


def test_seed_entities_are_never_demoted(conn: db.Conn) -> None:
    """Seeds are active by declaration, not by evidence."""
    promote_provisionals(conn, now="n")
    row = conn.execute("SELECT status FROM entities WHERE id=?", (NGC2,)).fetchone()
    assert row["status"] == "active"


# ── containment: qualifiers are not synonyms ─────────────────────────


def _nd() -> set[str]:
    from venona.pipeline.acronyms import load_non_distinguishing
    from venona.settings import get_settings

    return load_non_distinguishing(get_settings())


@pytest.mark.negative
@pytest.mark.parametrize(
    ("surface", "candidate"),
    [
        # Every one of these was LIVE in the database, three on active entities
        # that were already annotating briefs.
        ("Amazon", "Amazon Web Services"),
        ("Thales", "Thales Australia"),
        ("General Dynamics", "General Dynamics Information Technology"),
        # Not observed, but the same shape.
        ("Lockheed Martin", "Lockheed Martin Space"),
        ("BAE", "BAE Systems"),
    ],
)
def test_a_qualifier_is_not_a_synonym(surface: str, candidate: str) -> None:
    """`fuzz.token_set_ratio` scores a token SUBSET at exactly 100, so these all
    cleared the 92 accept threshold with a maximal runner-up margin.

    The extra tokens are doing the distinguishing work: AWS is not Amazon and
    Thales Australia is not Thales.
    """
    from rapidfuzz import fuzz
    from venona.pipeline.resolve import _is_qualified_containment

    assert fuzz.token_set_ratio(surface, candidate) == 100.0, "the trap this guards"
    assert _is_qualified_containment(surface, candidate, _nd())


@pytest.mark.parametrize(
    ("surface", "candidate"),
    [
        # Containment, but the extra tokens carry no meaning — same body.
        # Rejecting these outright would break annotation for six live aliases.
        ("Defense Department", "Department of Defense"),
        ("Air Force", "United States Air Force"),
        ("Navy", "United States Navy"),
        ("Kaizen", "Kaizen Laboratories"),
        ("Department of Justice", "Justice Department"),
    ],
)
def test_a_formalising_prefix_or_suffix_still_matches(surface: str, candidate: str) -> None:
    from venona.pipeline.resolve import _is_qualified_containment

    assert not _is_qualified_containment(surface, candidate, _nd())


def test_non_containment_is_untouched() -> None:
    """The guard must only fire on containment. An acronym shares no tokens with
    its expansion, and a token SUBSTITUTION is not containment at all."""
    from venona.pipeline.resolve import _is_qualified_containment

    nd = _nd()
    assert not _is_qualified_containment("MDA", "Missile Defense Agency", nd)
    # JIATF-401 vs JIATF-South: '401' and 'south' are different tokens, so
    # neither set contains the other. This guard does not catch it — the review
    # queue does.
    assert not _is_qualified_containment(
        "Joint Interagency Task Force 401", "Joint Interagency Task Force South", nd
    )


def test_the_token_list_is_user_editable() -> None:
    """It lives in `profile/lexicon.yaml`, not in code — the distinction between
    a formalising suffix and a subsidiary is domain knowledge."""
    assert {"of", "united", "states", "laboratories"} <= _nd()
    # Substantive in defense names; a wrong merge costs more than a missed one.
    assert not {"systems", "technologies", "services"} & _nd()


@pytest.mark.negative
def test_the_resolver_actually_applies_the_containment_guard(conn: db.Conn) -> None:
    """End to end, not just the predicate.

    Unit-testing `_is_qualified_containment` proves the rule is right; it does
    NOT prove `resolve()` consults it. Deleting the call site leaves those unit
    tests green, which is precisely how a guard rots.
    """
    from venona.db import core as db

    now = "2026-08-06T00:00:00.000Z"
    db.upsert(
        conn,
        "entities",
        {
            "id": "company:amazon-web-services",
            "type": "Company",
            "name": "Amazon Web Services",
            "canonical_key": "amazon web services",
            "status": "active",
            "first_seen": now,
            "last_seen": now,
            "created_at": now,
        },
        key=["id"],
    )
    # The candidate index is built by joining `aliases`, so an entity with no
    # alias row is invisible to fuzzy matching. Every live entity has one.
    db.upsert(
        conn,
        "aliases",
        {
            "id": "company:amazon-web-services|Amazon Web Services",
            "entity_id": "company:amazon-web-services",
            "surface": "Amazon Web Services",
            "normalized": "amazon web services",
            "strictness": 2,
            "is_acronym": 0,
            "added_by": "seed",
            "created_at": now,
        },
        key=["id"],
    )
    conn.commit()

    result = resolver(conn).resolve("Amazon", "Company", article_id="doc:test", now=now)

    assert result.entity_id != "company:amazon-web-services", "Amazon is not AWS"
    assert result.method == "provisional", f"expected a separate mint, got {result.method}"

    # No alias may have been attached to the parent.
    aliased = conn.execute(
        "SELECT 1 FROM aliases WHERE entity_id = ? AND normalized = 'amazon'",
        ("company:amazon-web-services",),
    ).fetchone()
    assert aliased is None, "the wrong alias was written anyway"

    # And the pair must be filed for review rather than silently dropped.
    filed = conn.execute(
        "SELECT * FROM alias_conflicts WHERE existing_entity_id = ?",
        ("company:amazon-web-services",),
    ).fetchone()
    assert filed is not None, "a declined containment pair must reach the review queue"
    assert filed["proposed_entity_id"] == result.entity_id
    assert filed["article_id"] == "doc:test", "the conflict must cite its evidence"
    assert not filed["resolved"], "a freshly filed conflict is open"


# ── corroboration counts publishers, not feeds ───────────────────────


def test_two_feeds_from_one_publisher_are_not_two_sources(conn: db.Conn) -> None:
    """`source_id` is a FEED. One publisher routinely arrives through several.

    After discovery was split into eight topic sweeps, `defensedaily.com`
    reached us under four `source_id`s. Counting those as independent
    corroboration is the opposite of what the gate is for.
    """
    from venona.db import core as db

    now = "2026-08-06T00:00:00.000Z"
    db.upsert(
        conn,
        "entities",
        {
            "id": "company:one-publisher",
            "type": "Company",
            "name": "One Publisher Co",
            "canonical_key": "one publisher co",
            "status": "provisional",
            "first_seen": now,
            "last_seen": now,
            "created_at": now,
        },
        key=["id"],
    )

    # Two articles, two DIFFERENT feeds, but the SAME publisher.
    for i, source_id in enumerate(("topic_space", "topic_undersea")):
        db.upsert(
            conn,
            "documents",
            {
                "id": f"doc:same-pub-{i}",
                "source_id": source_id,
                "url": f"https://defensedaily.com/story-{i}",
                "url_key": f"defensedaily.com/story-{i}",
                "title": f"Story {i}",
                "text_nfc": "x" * 800,
                "text_chars": 800,
                "site_domain": "defensedaily.com",
                "content_hash": f"hash{i}",
                "cluster_id": f"cluster:same-pub-{i}",
                "fetched_at": now,
                "created_at": now,
            },
            key=["id"],
        )
        db.insert(
            conn,
            "assertions",
            {
                "id": f"assert:same-pub-{i}",
                "run_id": "run:test",
                "article_id": f"doc:same-pub-{i}",
                "cluster_id": f"cluster:same-pub-{i}",
                "kind": "entity",
                "surface": "One Publisher Co",
                "entity_id_raw": "company:one-publisher",
                "evidence_quote": "One Publisher Co did a thing.",
                "confidence": 0.9,
                "method": "provisional",
                "created_at": now,
            },
        )
    conn.commit()

    promote_provisionals(conn, now=now)

    status = conn.execute(
        "SELECT status FROM entities WHERE id = 'company:one-publisher'"
    ).fetchone()
    assert status["status"] == "provisional", "one publisher is not two sources"

    # And the gate saw what we think it saw: two articles, one publisher.
    counts = conn.execute(
        """
        SELECT COUNT(DISTINCT a.article_id) AS articles,
               COUNT(DISTINCT d.site_domain) AS publishers,
               COUNT(DISTINCT d.source_id) AS feeds
          FROM assertions a JOIN documents d ON d.id = a.article_id
         WHERE a.entity_id_raw = 'company:one-publisher'
        """
    ).fetchone()
    assert (counts["articles"], counts["publishers"]) == (2, 1)
    assert counts["feeds"] == 2, "two feeds -- which is exactly what used to promote it"


def test_the_promotion_thresholds_come_from_settings(conn: db.Conn) -> None:
    """`provisional_promote_sightings` was assigned in the Resolver and never
    read; the thresholds were hardcoded, so editing settings.yaml did nothing."""
    import inspect

    from venona.pipeline import analyze
    from venona.settings import get_settings

    source = inspect.getsource(analyze.run_analyze)
    assert "provisional_promote_sightings" in source
    assert "provisional_promote_sources" in source
    assert get_settings().pipeline.provisional_promote_sources >= 1


# ── dormancy: the bound on a pool that had none ──────────────────────


def _lone_sighting(conn: db.Conn, entity_id: str, *, seen_at: str) -> None:
    """One entity, one article, one publisher -- the shape 690 of 710 live
    provisionals actually have."""
    db.upsert(
        conn,
        "entities",
        {
            "id": entity_id,
            "type": "Person",
            "name": entity_id.split(":")[1],
            "canonical_key": entity_id.split(":")[1],
            "status": "provisional",
            "first_seen": seen_at,
            "last_seen": seen_at,
            "created_at": seen_at,
        },
        key=["id"],
    )
    db.upsert(
        conn,
        "documents",
        {
            "id": f"doc:{entity_id}",
            "source_id": "breaking_defense",
            "site_domain": "breakingdefense.com",
            "url": f"https://breakingdefense.com/{entity_id}",
            "url_key": f"breakingdefense.com/{entity_id}",
            "title": "T",
            "text_nfc": "body",
            "text_chars": 4,
            "content_hash": f"h{entity_id}",
            "cluster_id": f"cluster:{entity_id}",
            "fetched_at": seen_at,
            "created_at": seen_at,
        },
        key=["id"],
    )
    db.upsert(
        conn,
        "assertions",
        {
            "id": f"assert:{entity_id}",
            "run_id": "run:test",
            "article_id": f"doc:{entity_id}",
            "cluster_id": f"cluster:{entity_id}",
            "kind": "entity",
            "surface": "Someone",
            "entity_id_raw": entity_id,
            "evidence_quote": "Someone said a thing.",
            "confidence": 0.9,
            "method": "provisional",
            "created_at": seen_at,
        },
        key=["id"],
    )
    conn.commit()


def _status(conn: db.Conn, entity_id: str) -> str:
    return conn.execute("SELECT status FROM entities WHERE id=?", (entity_id,)).fetchone()["status"]


def test_a_quiet_uncorroborated_entity_goes_dormant(conn: db.Conn) -> None:
    """Without this the provisional pool grows forever.

    Measured before the change: 710 provisional entities, 690 at (1 article,
    1 publisher), 184 of them `Person` -- someone quoted once who will never be
    quoted again -- and no code path anywhere retired one.
    """
    _lone_sighting(conn, "person:old-quote", seen_at="2026-01-01T00:00:00.000Z")

    promote_provisionals(conn, now="2026-08-06T00:00:00.000Z", dormant_after_days=30)
    assert _status(conn, "person:old-quote") == "dormant"


def test_dormancy_is_off_by_default(conn: db.Conn) -> None:
    """`dormant_after_days=0` disables it, which is what every caller that does
    not pass a settings value gets."""
    _lone_sighting(conn, "person:old-quote", seen_at="2026-01-01T00:00:00.000Z")

    promote_provisionals(conn, now="2026-08-06T00:00:00.000Z")
    assert _status(conn, "person:old-quote") == "provisional"


def test_a_recent_sighting_stays_provisional(conn: db.Conn) -> None:
    """Dormancy is a statement about SILENCE, not about being uncorroborated."""
    _lone_sighting(conn, "person:fresh", seen_at="2026-08-05T00:00:00.000Z")

    promote_provisionals(conn, now="2026-08-06T00:00:00.000Z", dormant_after_days=30)
    assert _status(conn, "person:fresh") == "provisional"


def test_a_dormant_entity_revives_on_new_evidence(conn: db.Conn) -> None:
    """Nothing is deleted, so one new sighting brings it straight back. This is
    what makes dormancy safe: it is reversible and costs no history."""
    _lone_sighting(conn, "person:returner", seen_at="2026-01-01T00:00:00.000Z")
    promote_provisionals(conn, now="2026-08-06T00:00:00.000Z", dormant_after_days=30)
    assert _status(conn, "person:returner") == "dormant"

    db.upsert(
        conn,
        "documents",
        {
            "id": "doc:comeback",
            "source_id": "defense_news",
            "site_domain": "defensenews.com",
            "url": "https://defensenews.com/comeback",
            "url_key": "defensenews.com/comeback",
            "title": "T",
            "text_nfc": "body",
            "text_chars": 4,
            "content_hash": "hcomeback",
            "cluster_id": "cluster:comeback",
            "fetched_at": "2026-08-06T00:00:00.000Z",
            "created_at": "2026-08-06T00:00:00.000Z",
        },
        key=["id"],
    )
    db.upsert(
        conn,
        "assertions",
        {
            "id": "assert:comeback",
            "run_id": "run:test",
            "article_id": "doc:comeback",
            "cluster_id": "cluster:comeback",
            "kind": "entity",
            "surface": "Someone",
            "entity_id_raw": "person:returner",
            "evidence_quote": "Someone said another thing.",
            "confidence": 0.9,
            "method": "exact_alias",
            "created_at": "2026-08-06T00:00:00.000Z",
        },
        key=["id"],
    )
    conn.commit()

    # Two articles, two publishers -- so it does not merely revive, it promotes.
    promote_provisionals(conn, now="2026-08-06T01:00:00.000Z", dormant_after_days=30)
    assert _status(conn, "person:returner") == "active"


def test_an_active_entity_never_goes_dormant(conn: db.Conn) -> None:
    """Dormancy applies to the UNCORROBORATED tail. A corroborated entity that
    stops being mentioned is still a real thing the graph should answer about."""
    _lone_sighting(conn, "person:corroborated", seen_at="2026-01-01T00:00:00.000Z")
    conn.execute("UPDATE entities SET status='active' WHERE id='person:corroborated'")
    db.upsert(
        conn,
        "documents",
        {
            "id": "doc:second",
            "source_id": "defense_news",
            "site_domain": "defensenews.com",
            "url": "https://defensenews.com/second",
            "url_key": "defensenews.com/second",
            "title": "T",
            "text_nfc": "body",
            "text_chars": 4,
            "content_hash": "hsecond",
            "cluster_id": "cluster:second",
            "fetched_at": "2026-01-01T00:00:00.000Z",
            "created_at": "2026-01-01T00:00:00.000Z",
        },
        key=["id"],
    )
    db.upsert(
        conn,
        "assertions",
        {
            "id": "assert:second",
            "run_id": "run:test",
            "article_id": "doc:second",
            "cluster_id": "cluster:second",
            "kind": "entity",
            "surface": "Someone",
            "entity_id_raw": "person:corroborated",
            "evidence_quote": "Someone said a second thing.",
            "confidence": 0.9,
            "method": "exact_alias",
            "created_at": "2026-01-01T00:00:00.000Z",
        },
        key=["id"],
    )
    conn.commit()

    promote_provisionals(conn, now="2026-08-06T00:00:00.000Z", dormant_after_days=30)
    assert _status(conn, "person:corroborated") == "active"


# ── the write storm ──────────────────────────────────────────────────


def test_status_and_counts_are_written_in_one_statement(
    conn: db.Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⭐ A round trip per entity, several times per run.

    Both resolve passes finish their arithmetic in Python and then issued one
    UPDATE per entity to write it down. The numbers were never in question; the
    waiting was. On a corpus with thousands of entities that is thousands of
    round trips for values already computed, and `promote_provisionals` runs
    more than once in a run.

    What must not change is what gets written, which is what the assertions
    below check alongside the count.
    """
    from venona.db.repositories import pipeline_resolve as repo

    for index in range(6):
        _lone_sighting(conn, f"person:batch{index}", seen_at="2026-08-01T00:00:00.000Z")
    conn.commit()

    statements: list[int] = []
    original = repo.update_entity_statuses

    def counted(target, now, rows):
        statements.append(len(rows))
        return original(target, now, rows)

    monkeypatch.setattr(repo, "update_entity_statuses", counted)
    promote_provisionals(conn, now="2026-09-30T00:00:00.000Z", dormant_after_days=30)

    assert len(statements) == 1, f"{len(statements)} write statements for one pass"
    assert statements[0] == 6, "every decision must ride the one statement"
    for index in range(6):
        assert _status(conn, f"person:batch{index}") == "dormant"


def test_the_set_based_write_lands_the_same_values(conn: db.Conn) -> None:
    """The golden comparison: counts written by the batched statement must match
    what the per-entity loop wrote — same mention count, same source count, and
    the reset-to-zero for an entity whose evidence has moved away."""
    from venona.pipeline.resolve import refresh_mention_counts

    _lone_sighting(conn, "person:counted", seen_at="2026-08-01T00:00:00.000Z")
    conn.commit()

    refresh_mention_counts(conn)
    row = conn.execute(
        "SELECT mention_count, source_count FROM entities WHERE id = 'person:counted'"
    ).fetchone()
    assert row["mention_count"] == 1
    assert row["source_count"] == 1

    conn.execute("UPDATE assertions SET retracted_at = 'n' WHERE entity_id_raw = 'person:counted'")
    conn.commit()
    refresh_mention_counts(conn)
    row = conn.execute(
        "SELECT mention_count, source_count FROM entities WHERE id = 'person:counted'"
    ).fetchone()
    assert row["mention_count"] == 0, "a retracted assertion must drop the count to zero"
    assert row["source_count"] == 0
