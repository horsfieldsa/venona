"""The table decides; the code is measured against it.

WHAT WENT WRONG. Merge-decision cases lived in three inline parametrize tables
-- in `test_curate.py` and twice in `test_resolve.py` -- with the same shape and
no shared truth. Between them they cover nine live pairs, and the four splits
that fragmented the production corpus (Palantir, two Mavens, General Atomics)
were in none of them, because a case entered a table only when somebody wrote a
test. `fixtures/curation/decisions.yaml` inverts that: a case enters when
somebody makes a JUDGEMENT, and every consumer of that judgement is checked
against the same row.
"""

from __future__ import annotations

import decisions
import pytest
from decisions import Case
from helpers import NOW
from venona.db import core as db
from venona.pipeline.curate import (
    _classify_open,
    _file_entity_duplicates,
    _relation_for,
    identical_pairs,
)


def _run_detection(conn: db.Conn) -> None:
    """The two stages that turn a corpus into a labelled queue, in run order."""
    lex = decisions.lexicons()
    _file_entity_duplicates(conn, now=NOW, **lex)
    _classify_open(conn, **lex)
    conn.commit()


@pytest.mark.parametrize("case", decisions.params(needs="classify"))
def test_the_classifier_agrees_with_the_table(conn: db.Conn, case: Case) -> None:
    """`kind` is what both consumers filter on -- `identical_pairs` takes only
    `duplicate` and `adjudicate` only the unsettled kinds -- so a
    misclassification is not cosmetic, it routes the pair to the wrong half of
    the stage or to neither."""
    decisions.plant(conn, case)
    _run_detection(conn)

    kinds = {
        frozenset((r["existing_entity_id"], r["proposed_entity_id"])): r["kind"]
        for r in conn.execute(
            "SELECT existing_entity_id, proposed_entity_id, kind FROM alias_conflicts"
        ).fetchall()
    }
    got = kinds.get(frozenset((case.a.id, case.b.id)))
    assert got == case.classify, f"{case.key}: {case.reason}"


@pytest.mark.parametrize("case", decisions.params(verdict={"merge"}))
def test_a_pair_the_table_calls_one_thing_is_merged_without_a_human(
    conn: db.Conn, case: Case
) -> None:
    """Through the real detection and classification, not a hand-set `kind`:
    the two halves of the stage have to agree with each other on their own."""
    decisions.plant(conn, case)
    _run_detection(conn)

    merges = identical_pairs(conn, **decisions.lexicons())
    got = {(loser, survivor) for loser, survivor, _ in merges}
    assert (case.loser_id, case.survivor_id) in got, f"{case.key}: {case.reason}"


@pytest.mark.negative
@pytest.mark.parametrize("case", decisions.params(verdict={"keep_apart", "relate", "escalate"}))
def test_a_pair_the_table_reserves_for_a_human_is_never_merged_behind_them(
    conn: db.Conn, case: Case
) -> None:
    """An incorrect auto-merge is invisible and poisons every downstream query.
    These are the rows where being right matters most, so they assert on the
    pair rather than on the count: another pair merging is not this row's
    business, but these two touching each other is."""
    decisions.plant(conn, case)
    _run_detection(conn)

    for loser, survivor, why in identical_pairs(conn, **decisions.lexicons()):
        assert {loser, survivor} != {case.a.id, case.b.id}, (
            f"{case.key}: merged as {why!r} -- {case.reason}"
        )


@pytest.mark.parametrize("case", decisions.params(needs="containment_veto"))
def test_the_resolver_vetoes_exactly_the_qualified_containments(case: Case) -> None:
    """The resolver's veto and the curator's verdict answer DIFFERENT questions,
    and the table records both. A `containment_veto: true` beside a
    `verdict: merge` is not a contradiction: it says the resolver was right to
    decline the match and mint separately, and that deciding the pair is the
    curator's job. Reading the veto as a finding that the two are different
    things is what left Palantir split."""
    from venona.pipeline.acronyms import load_non_distinguishing
    from venona.pipeline.resolve import _is_qualified_containment
    from venona.settings import get_settings

    non_distinguishing = load_non_distinguishing(get_settings())
    assert (
        _is_qualified_containment(case.a.name, case.b.name, non_distinguishing)
        is case.containment_veto
    ), f"{case.key}: {case.reason}"


@pytest.mark.parametrize("case", decisions.params(verdict={"relate"}))
def test_the_ontology_yields_the_predicate_the_table_names(case: Case) -> None:
    """The model answers with an ACTION; `_relation_for` translates it through
    the ontology. That translation is deterministic and is where the queue came
    to offer a decision the API then refused, so it is asserted here even though
    the model's verdict cannot be."""
    child = case.side(case.child)
    parent = case.side("b" if case.child == "a" else "a")
    assert _relation_for(child.type, parent.type) == case.predicate, f"{case.key}: {case.reason}"


def test_every_row_carries_a_reason_a_reader_can_audit() -> None:
    """The loader enforces presence; this enforces that the reason is worth
    reading. A row saying "duplicate" explains nothing a future maintainer could
    not have guessed, and the whole value of the table is that it holds the
    judgement, not just the answer."""
    thin = [c.key for c in decisions.CASES if len(c.reason.split()) < 12]
    assert not thin, f"decision rows with a reason too short to audit: {thin}"


# ── correcting a type ────────────────────────────────────────────────


def test_a_type_conflict_folds_both_readings_into_the_kind_you_pick(conn: db.Conn) -> None:
    """The curator's own example: one body read as two kinds, neither right.

    `research:us-navy` and `unit:us-navy` are one navy. Choosing GovernmentBody
    picks neither side -- it mints `govt:us-navy` and folds both in, so the id
    prefix, the type and the colour all still agree, and both old ids keep
    resolving.
    """
    from helpers import entity
    from venona.services.curate import correct_entity_type, resolve_entity_id

    entity(conn, "research:us-navy", "US Navy", entity_type="ResearchOrg")
    entity(conn, "unit:us-navy", "United States Navy", entity_type="MilitaryUnit")
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": "typeconflict|research:us-navy|unit:us-navy",
            "surface": "US Navy",
            "normalized": "us navy",
            "existing_entity_id": "research:us-navy",
            "proposed_entity_id": "unit:us-navy",
            "entity_type": "ResearchOrg",
            "kind": "type_conflict",
            "created_at": NOW,
        },
        key=["id"],
    )
    conn.commit()

    result = correct_entity_type(
        conn,
        "typeconflict|research:us-navy|unit:us-navy",
        entity_type="GovernmentBody",
        merge_ids=("merge:a", "merge:b"),
        now=NOW,
    )
    conn.commit()

    # The id follows the FULLER name, because the id is minted from the name a
    # reader would recognise and `mint_id` is the only thing that makes ids.
    assert result["survivor_id"] == "govt:united-states-navy"
    assert result["minted"] is True
    for old in ("research:us-navy", "unit:us-navy"):
        assert resolve_entity_id(conn, old) == "govt:united-states-navy", (
            "every id that used to name this navy must still reach it"
        )
    row = conn.execute(
        "SELECT type, name, status FROM entities WHERE id = 'govt:united-states-navy'"
    ).fetchone()
    assert row["type"] == "GovernmentBody"
    assert row["name"] == "United States Navy", "the fuller name is the readable one"


def test_choosing_a_kind_one_side_already_is_mints_nothing(conn: db.Conn) -> None:
    """The common case, and the one the live audit found every time: the right
    kind is already one of the two, so this is a plain merge into it."""
    from helpers import entity
    from venona.services.curate import correct_entity_type

    entity(conn, "plat:ge-f414", "GE F414", entity_type="Platform")
    entity(conn, "tech:ge-f414", "GE F414", entity_type="Technology")
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": "typeconflict|plat:ge-f414|tech:ge-f414",
            "surface": "GE F414",
            "normalized": "ge f414",
            "existing_entity_id": "plat:ge-f414",
            "proposed_entity_id": "tech:ge-f414",
            "entity_type": "Platform",
            "kind": "type_conflict",
            "created_at": NOW,
        },
        key=["id"],
    )
    conn.commit()

    result = correct_entity_type(
        conn,
        "typeconflict|plat:ge-f414|tech:ge-f414",
        entity_type="Technology",
        merge_ids=("merge:c", "merge:d"),
        now=NOW,
    )
    assert result == {
        "survivor_id": "tech:ge-f414",
        "minted": False,
        "settled_conflict_ids": result["settled_conflict_ids"],
    }


def test_the_ontology_decides_which_boundaries_blur(conn: db.Conn) -> None:
    """`confusable_with` is the whole difference between the two questions, and
    it is configuration rather than code because only the domain knows. Havoc
    is a company and, separately, another firm's missile; GE F414 is one engine
    read twice. The names cannot tell them apart and the types can."""
    from venona.ontology import get_ontology

    onto = get_ontology()
    assert onto.confusable("Platform", "Technology"), "one engine, read twice"
    assert onto.confusable("GovernmentBody", "ResearchOrg"), "one body, read twice"
    assert not onto.confusable("Company", "Platform"), (
        "a firm and an unrelated missile: merging these is the expensive mistake"
    )
    assert not onto.confusable("Program", "Platform"), "the distinction the two types exist to draw"


# ── the survivor of a model merge ────────────────────────────────────


def _verdict(conn: db.Conn, conflict_id: str, a: str, b: str) -> None:
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": conflict_id,
            "surface": "x",
            "normalized": "x",
            "existing_entity_id": a,
            "proposed_entity_id": b,
            "entity_type": "Company",
            "kind": "duplicate",
            "suggestion": "merge",
            "suggestion_confidence": "certain",
            "created_at": NOW,
        },
        key=["id"],
    )


def test_a_model_merge_judges_the_pair_it_actually_merges(conn: db.Conn) -> None:
    """THE PRODUCTION FAILURE, 2026-08-28.

    `Anduril Industries` -- eleven mentions, eight edges, declared in the seed --
    was merged INTO `Anduril's`, a possessive that had become an entity. The row
    named `Anduril`, which an earlier merge in the same pass had already turned
    into `Anduril Industries`; the survivor was chosen from the row's own names,
    so the decision was made about a pair that no longer existed and applied to
    one that did.
    """
    from helpers import entity
    from venona.pipeline.curate import model_merges
    from venona.services.curate import merge_entities

    entity(conn, "company:anduril", "Anduril")
    entity(conn, "company:anduril-industries", "Anduril Industries")
    entity(conn, "company:anduril-s", "Anduril's")
    _verdict(
        conn, "contain|company:anduril-s|company:anduril", "company:anduril", "company:anduril-s"
    )
    conn.commit()

    # The earlier merge in the same pass, which is what makes the row stale.
    merge_entities(
        conn,
        from_id="company:anduril",
        to_id="company:anduril-industries",
        merge_id="merge:earlier",
        reason="auto: same name",
        now=NOW,
    )
    conn.commit()

    # THE DEFECT THIS GUARDS is the DIRECTION. Reading the row's own stale names,
    # the merge went ahead on a comparison between two entities that were no
    # longer the ones being merged, and a declared company with eleven mentions
    # lost its identity to a sentence fragment.
    #
    # Judged on the pair it would actually merge, the possessive is the loser.
    # `_comparable` drops a trailing possessive, so `Anduril's` and `Anduril
    # Industries` do corroborate -- and that is the merge the corpus wants, which
    # is why `_survivor_of` disqualifies a possessive from surviving rather than
    # refusing the pair. What must never happen is the other direction.
    pairs = model_merges(conn)
    assert not any(loser == "company:anduril-industries" for loser, _s, _w in pairs), (
        "a declared company with eleven mentions must never merge into a possessive"
    )
    assert all(survivor == "company:anduril-industries" for _l, survivor, _w in pairs), (
        "and whatever is offered must resolve ONTO the declared company"
    )


def test_a_possessive_is_never_the_survivor(conn: db.Conn) -> None:
    """A possessive is a sentence fragment that reached the resolver, and it is
    the LONGER string -- so the fuller-name default hands it the identity."""
    from helpers import entity
    from venona.pipeline.curate import model_merges

    entity(conn, "company:acme", "Acme")
    entity(conn, "company:acme-s", "Acme's")
    _verdict(conn, "contain|company:acme-s|company:acme", "company:acme", "company:acme-s")
    conn.commit()

    loser, survivor, _why = model_merges(conn)[0]
    assert (survivor, loser) == ("company:acme", "company:acme-s")


@pytest.mark.parametrize(
    ("plain", "carrier", "carrier_name"),
    [
        ("company:cso", "company:darpa-cso", "DARPA’s Commercial Strategy Office"),
        ("company:cso", "company:the-cso", "The Commercial Strategy Office"),
        ("company:cso", "company:la-cso", "la Commercial Strategy Office"),
        ("company:cso", "company:l-cso", "l’Commercial Strategy Office"),
    ],
)
def test_a_prefix_is_never_the_survivor(
    conn: db.Conn, plain: str, carrier: str, carrier_name: str
) -> None:
    """THE PRODUCTION FAILURE, nineteen merges to 2026-09-17.

    An article belongs to the sentence and a possessive belongs to a DIFFERENT
    entity, and both make the string longer -- so the fuller-name default handed
    the identity to `DARPA's Commercial Strategy Office` and `The U.S. Army
    Corps of Engineers`. The trailing-possessive rule that was supposed to stop
    this is anchored to the end of the name and saw none of them.
    """
    from helpers import entity
    from venona.pipeline.curate import model_merges

    entity(conn, plain, "Commercial Strategy Office")
    entity(conn, carrier, carrier_name, status="provisional")
    _verdict(conn, f"contain|{carrier}|{plain}", plain, carrier)
    conn.commit()

    loser, survivor, _why = model_merges(conn)[0]
    assert (survivor, loser) == (plain, carrier), (
        f"{carrier_name!r} is the office wearing a prefix, not a second entity"
    )


def test_a_prefix_that_carries_name_is_kept(conn: db.Conn) -> None:
    """The stripping is gated on the remainder BEING the other name, because a
    prefix is only provably noise when the name survives without it. Ungated,
    `People's Liberation Army` loses its apostrophe to a typo of itself."""
    from helpers import entity
    from venona.pipeline.curate import model_merges

    entity(conn, "unit:pla-typo", "Peoples Liberation Army", status="provisional")
    entity(conn, "unit:pla", "People’s Liberation Army", status="provisional")
    _verdict(conn, "contain|unit:pla|unit:pla-typo", "unit:pla-typo", "unit:pla")
    conn.commit()

    loser, survivor, _why = model_merges(conn)[0]
    assert (survivor, loser) == ("unit:pla", "unit:pla-typo"), (
        "`People’s` is the name, not a possessive in front of one"
    )


def test_a_declared_entity_outranks_a_longer_name(conn: db.Conn) -> None:
    """Length is only the default. A person who wrote the entity into the seed
    chose what to call the thing, and that outranks a longer string."""
    from helpers import entity
    from venona.pipeline.curate import model_merges

    entity(conn, "company:gripen-co", "Gripen")
    entity(conn, "company:gripen-jets", "Gripen fighter jets", status="provisional")
    _verdict(
        conn,
        "contain|company:gripen-jets|company:gripen-co",
        "company:gripen-co",
        "company:gripen-jets",
    )
    conn.commit()

    loser, survivor, _why = model_merges(conn)[0]
    assert (survivor, loser) == ("company:gripen-co", "company:gripen-jets"), (
        "an active entity outranks a provisional one carrying a descriptive phrase"
    )


@pytest.mark.parametrize("case", decisions.params(verdict={"escalate", "keep_apart"}))
def test_a_pair_the_pipeline_cannot_settle_is_still_read_by_something(
    conn: db.Conn, case: Case
) -> None:
    """A row nothing reads is a row nobody decides.

    `identical_pairs` acts only on provable identity and `declared_type_pairs`
    only where the ontology calls the boundary confusable, so everything else
    reaches a curator with whatever reading the adjudicator left on it. Six
    `cross_type` and two `type_conflict` rows on the live queue carried none at
    all: filed, classified, and then read by no stage on any run.

    The ontology's boundary is untouched by this. The UI refuses Merge on both
    kinds whatever the model says; what the adjudicator supplies is the other
    question, which is whether one belongs to the other.
    """
    from venona.db.repositories.pipeline_curate import select_alias_conflicts_2

    decisions.plant(conn, case)
    _run_detection(conn)

    pool = {
        frozenset((r["existing_entity_id"], r["proposed_entity_id"]))
        for r in select_alias_conflicts_2(conn, 100).fetchall()
    }
    assert frozenset((case.a.id, case.b.id)) in pool, f"{case.key}: {case.reason}"


def _distinct_verdict(conn: db.Conn, conflict_id: str, a: str, b: str, kind: str) -> None:
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": conflict_id,
            "surface": "x",
            "normalized": "x",
            "existing_entity_id": a,
            "proposed_entity_id": b,
            "entity_type": "MilitaryUnit",
            "kind": kind,
            "suggestion": "distinct",
            "suggestion_confidence": "certain",
            "created_at": NOW,
        },
        key=["id"],
    )


def test_an_obvious_distinction_is_settled_without_a_curator(conn: db.Conn) -> None:
    """The queue a reader called obvious.

    `Swedish Air Force` and `Sudanese Armed Forces` share an acronym and nothing
    else, and asking a person which of the two is the other is not curation. The
    asymmetry runs the opposite way from a merge: keeping two entities apart
    leaves them as what they already were, so a wrong distinction costs a missed
    merge that comes back, not a corruption nobody can see.
    """
    from helpers import entity
    from venona.pipeline.curate import model_distinctions

    entity(conn, "unit:swedish-air-force", "Swedish Air Force", entity_type="MilitaryUnit")
    entity(conn, "unit:sudanese-armed-forces", "Sudanese Armed Forces", entity_type="MilitaryUnit")
    _distinct_verdict(
        conn, "coll|saf", "unit:swedish-air-force", "unit:sudanese-armed-forces", "collision"
    )
    conn.commit()

    assert model_distinctions(conn, set()) == ["coll|saf"]


def test_a_provable_identity_beats_a_model_calling_it_distinct(conn: db.Conn) -> None:
    """The one veto, and it is not a similarity check.

    A pair the derivational rules would merge is provably ONE thing, so a model
    calling it distinct is the model contradicting a token list a person wrote.
    Closing that silently loses a merge nobody would ever be shown again --
    which is the only way an auto-distinction can do lasting damage.
    """
    from helpers import entity
    from venona.pipeline.curate import model_distinctions

    entity(conn, "unit:space-command", "Space Systems Command", entity_type="MilitaryUnit")
    entity(conn, "unit:ssc", "Space Systems Command (SSC)", entity_type="MilitaryUnit")
    _distinct_verdict(conn, "dup|ssc", "unit:space-command", "unit:ssc", "duplicate")
    conn.commit()

    mergeable = {("unit:ssc", "unit:space-command")}
    assert model_distinctions(conn, mergeable) == []
    # ...and the veto matches whichever way round the pair was filed.
    assert model_distinctions(conn, {("unit:space-command", "unit:ssc")}) == []


def test_a_placement_question_is_never_answered_as_a_distinction(conn: db.Conn) -> None:
    """`anchor` proposes where a floating name belongs and `resolver_error` says
    the resolver fell over. Neither asks whether two things are one, so settling
    either as "kept apart" answers a question nobody put."""
    from helpers import entity
    from venona.pipeline.curate import model_distinctions

    entity(conn, "unit:a", "A", entity_type="MilitaryUnit")
    entity(conn, "unit:b", "B", entity_type="MilitaryUnit")
    _distinct_verdict(conn, "anchor|a", "unit:a", "unit:b", "anchor")
    conn.commit()

    assert model_distinctions(conn, set()) == []


def _relate_verdict(conn: db.Conn, cid: str, a: str, b: str, child: str, pred: str) -> None:
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": cid,
            "surface": "x",
            "normalized": "x",
            "existing_entity_id": a,
            "proposed_entity_id": b,
            "entity_type": "Company",
            "kind": "containment",
            "suggestion": "part_of",
            "suggestion_confidence": "certain",
            "suggestion_child": child,
            "suggestion_predicate": pred,
            "created_at": NOW,
        },
        key=["id"],
    )


def test_a_subsidiary_is_related_without_a_curator(conn: db.Conn) -> None:
    """The third verdict, applied rather than suggested.

    Keeping `BAE Systems` and `BAE Space & Mission Systems` apart is right and
    throws away the one fact the conflict surfaced. An edge is not a merge: a
    wrong one is visible in the graph, reversible by retracting one assertion,
    and destroys no id.
    """
    from helpers import entity
    from venona.pipeline.curate import model_relations

    entity(conn, "company:bae", "BAE Systems")
    entity(conn, "company:bae-space", "BAE Space Mission Systems")
    _relate_verdict(
        conn, "contain|bae", "company:bae", "company:bae-space", "company:bae-space", "PART_OF"
    )
    conn.commit()

    assert model_relations(conn) == [("contain|bae", "company:bae-space", "company:bae", "PART_OF")]


def test_a_relation_pointing_the_wrong_way_is_left_for_a_person(conn: db.Conn) -> None:
    """THE GATE, and the failure it is built from.

    `adjudicate`'s docstring records the model getting this backwards on three
    of twelve hand-checked pairs -- `U.S. Army PART_OF Army`, `Anduril
    Industries PART_OF Anduril`, `Northrop Grumman PART_OF Northrop` -- all
    wrong and all wrong the same way. A subsidiary's name carries its parent's,
    never the reverse, so a verdict naming the SHORTER name as the child is the
    shape the model is known to invert.
    """
    from helpers import entity
    from venona.pipeline.curate import model_relations

    entity(conn, "company:anduril", "Anduril")
    entity(conn, "company:anduril-industries", "Anduril Industries")
    # The model naming the parent as the child.
    _relate_verdict(
        conn,
        "contain|anduril",
        "company:anduril",
        "company:anduril-industries",
        "company:anduril",
        "PART_OF",
    )
    conn.commit()

    assert model_relations(conn) == []


def test_a_verdict_the_ontology_cannot_express_is_never_offered(conn: db.Conn) -> None:
    """`_relation_for` writes NULL when no declared predicate is legal between
    two types, and a row like that cannot be applied by anybody -- the API
    refuses it with the very message that function exists to prevent. Offering
    it to a curator is offering a button that 422s."""
    from helpers import entity
    from venona.pipeline.curate import model_relations

    entity(conn, "tech:drones", "drones", entity_type="Technology")
    entity(conn, "tech:attack-drones", "one-way attack drones", entity_type="Technology")
    _relate_verdict(
        conn, "contain|dr", "tech:drones", "tech:attack-drones", "tech:attack-drones", "PART_OF"
    )
    conn.execute("UPDATE alias_conflicts SET suggestion_predicate = NULL WHERE id = 'contain|dr'")
    conn.commit()

    assert model_relations(conn) == []


def test_a_parent_that_has_since_been_renamed_by_a_merge_still_relates(conn: db.Conn) -> None:
    """TEN CORRECT RELATIONSHIPS WERE DECLINED FOR THIS, on the live queue.

    `Army` was merged into `U.S. Army`, which is the fuller name and the right
    survivor. The conflict filed against `Army` then resolved to a parent whose
    displayed name `Army Medical Command` does not contain -- so the direction
    check, which exists because the model inverts `part_of`, refused a pair it
    was built to accept.

    The alias is the claim the display name was making before the merge, and
    aliases move with a merge precisely so that nothing downstream loses the
    older spelling. Reading them is not a weakening of the gate.
    """
    from helpers import entity
    from venona.pipeline.curate import model_relations

    entity(conn, "unit:us-army", "U.S. Army", entity_type="MilitaryUnit")
    entity(conn, "unit:army-medical", "Army Medical Command", entity_type="MilitaryUnit")
    db.upsert(
        conn,
        "aliases",
        {
            "id": "alias:army",
            "entity_id": "unit:us-army",
            "surface": "Army",
            "normalized": "army",
            "strictness": 1,
            "is_acronym": 0,
            "added_by": "merge",
            "created_at": NOW,
        },
        key=["id"],
    )
    _relate_verdict(
        conn,
        "contain|amc",
        "unit:us-army",
        "unit:army-medical",
        "unit:army-medical",
        "PART_OF",
    )
    conn.commit()

    assert model_relations(conn) == [
        ("contain|amc", "unit:army-medical", "unit:us-army", "PART_OF")
    ]


def test_a_child_merged_since_the_verdict_is_still_related(conn: db.Conn) -> None:
    """`suggestion_child` records the id that was live when the model answered.

    Nothing rewrites it when that side is later merged away, so comparing the
    raw id against the resolved pair silently never matches and the verdict is
    dropped -- no error, no count, just a row that stays open. It cost `U.S.
    Army Europe-Africa`, merged between the verdict and the run that would have
    applied it.
    """
    from helpers import entity
    from venona.pipeline.curate import model_relations
    from venona.services.curate import merge_entities

    entity(conn, "unit:army", "Army", entity_type="MilitaryUnit")
    entity(conn, "unit:usarmy-eur", "U.S. Army Europe-Africa", entity_type="MilitaryUnit")
    entity(conn, "unit:usareur-af", "U.S. Army Europe and Africa", entity_type="MilitaryUnit")
    _relate_verdict(
        conn, "contain|eur", "unit:army", "unit:usarmy-eur", "unit:usarmy-eur", "PART_OF"
    )
    conn.commit()

    # The child becomes somebody else between the verdict and this run.
    merge_entities(
        conn,
        from_id="unit:usarmy-eur",
        to_id="unit:usareur-af",
        merge_id="merge:eur",
        reason="auto: same name",
        now=NOW,
    )
    conn.commit()

    assert model_relations(conn) == [("contain|eur", "unit:usareur-af", "unit:army", "PART_OF")]


def test_an_article_in_front_of_a_name_is_not_part_of_it(conn: db.Conn) -> None:
    """`the Air Force` is the surface an outlet wrote, and it became an alias of
    the survivor. `31st Air Task Force` contains the name and not the article,
    so the direction check refused two correct relationships over one word."""
    from helpers import entity
    from venona.pipeline.curate import model_relations

    entity(conn, "unit:usaf", "United States Air Force", entity_type="MilitaryUnit")
    entity(conn, "unit:31st-atf", "31st Air Task Force", entity_type="MilitaryUnit")
    db.upsert(
        conn,
        "aliases",
        {
            "id": "alias:the-air-force",
            "entity_id": "unit:usaf",
            "surface": "the Air Force",
            "normalized": "the air force",
            "strictness": 1,
            "is_acronym": 0,
            "added_by": "merge",
            "created_at": NOW,
        },
        key=["id"],
    )
    _relate_verdict(conn, "contain|atf", "unit:usaf", "unit:31st-atf", "unit:31st-atf", "PART_OF")
    conn.commit()

    assert model_relations(conn) == [("contain|atf", "unit:31st-atf", "unit:usaf", "PART_OF")]
