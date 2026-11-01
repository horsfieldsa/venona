"""Tripwires for the frozen contracts.

These convert "someone quietly changed a contract" from a mystery bug three
weeks later into a red test in the same commit. Each maps to an entry in
docs/CONTRACTS.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
from pydantic import ValidationError
from venona.api.schemas.brief import TextRun
from venona.db import core as db
from venona.db.models import id_pattern, is_valid_id, mint_id, slugify
from venona.ontology import get_ontology
from venona.services.alias_index import build_index
from venona.services.annotate import (
    Block,
    annotate_blocks,
    blocks_to_runs,
    markdown_to_blocks,
    verify_runs,
)
from venona.services.brief_service import get_brief, latest_brief_id
from venona.settings import get_settings

# ── C3: runs concatenate to the block text, exactly ──────────────────


def test_runs_concatenate_to_block_text(conn: db.Conn) -> None:
    """The single most important invariant in the reader. If this breaks, the
    prose silently corrupts."""
    rows = conn.execute("SELECT * FROM brief_blocks").fetchall()
    assert rows, "seed must produce brief blocks"

    for row in rows:
        block = Block(
            id=row["id"],
            type=row["type"],
            depth=row["depth"],
            text=row["text"],
            spans=json.loads(row["spans"]),
        )
        runs = blocks_to_runs(block)
        assert verify_runs(block, runs), f"runs do not reconstruct {row['id']}"


def test_runs_never_overlap(conn: db.Conn) -> None:
    for row in conn.execute("SELECT id, spans FROM brief_blocks"):
        spans = sorted(json.loads(row["spans"]), key=lambda s: s["start"])
        for a, b in zip(spans, spans[1:], strict=False):
            assert a["end"] <= b["start"], f"overlapping spans in {row['id']}"


def test_run_kinds_are_the_frozen_three(conn: db.Conn) -> None:
    """`undefined` must exist from day one even before S8 -- adding a kind later
    touches the component, the card, the store and the tokens."""
    allowed = {"entity", "glossary", "undefined"}
    for row in conn.execute("SELECT spans FROM brief_blocks"):
        for span in json.loads(row["spans"]):
            assert span["kind"] in allowed


def test_no_spans_inside_code_blocks() -> None:
    blocks = markdown_to_blocks("Text with `NGC2` inline.\n\n```\nNGC2 in code\n```\n")
    code = [b for b in blocks if b.type == "code"]
    assert code, "fixture must contain a code block"
    for block in code:
        assert block.spans == []


def test_text_run_rejects_unknown_fields() -> None:
    """`extra: forbid` is what stops the wire format drifting by accretion."""
    with pytest.raises(ValidationError):
        TextRun(t="x", unexpected="y")


# ── C4: entity id scheme ─────────────────────────────────────────────


def test_every_seeded_id_matches_the_pattern(conn: db.Conn) -> None:
    for row in conn.execute("SELECT id FROM entities"):
        assert is_valid_id(row["id"]), f"{row['id']} violates C4"
    for row in conn.execute("SELECT id FROM acronym_definitions"):
        base = row["id"].split("--")[0]
        assert is_valid_id(base), f"{row['id']} violates C4"


def test_slugify_is_idempotent() -> None:
    for name in ["Next Generation Command and Control", "Anduril Industries, Inc.", "PEO C3N"]:
        once = slugify(name)
        assert slugify(once) == once


def test_mint_id_is_deterministic() -> None:
    a = mint_id("Program", "Next Generation Command and Control")
    b = mint_id("Program", "Next Generation Command and Control")
    assert a == b == "prog:next-generation-command-and-control"


def test_id_pattern_prefixes_match_the_ontology() -> None:
    """The id namespace and the ontology cannot be allowed to disagree."""
    ontology = get_ontology()
    for spec in ontology.entity_types.values():
        assert id_pattern().match(f"{spec.prefix}:x"), f"prefix {spec.prefix} not in id_pattern()"


# ── C10: the ontology contract ───────────────────────────────────────


def test_ontology_loads_and_validates() -> None:
    ontology = get_ontology()
    assert ontology.version >= 1
    assert ontology.entity_types and ontology.predicates


def test_ontology_rejects_inverted_predicates() -> None:
    """The check an enum cannot do. `Program --MANAGES--> GovernmentBody` uses a
    real predicate backwards; only domain/range catches it."""
    ontology = get_ontology()
    assert ontology.check_relation("MANAGES", "GovernmentBody", "Program") is None
    assert ontology.check_relation("MANAGES", "Program", "GovernmentBody") is not None
    assert ontology.check_relation("WORKS_FOR", "Company", "Person") is not None
    assert ontology.check_relation("NOT_A_PREDICATE", "Company", "Program") is not None


def test_the_split_separates_who_buys_from_who_sells() -> None:
    """What ontology v3 bought, stated as the assertions it enables.

    With one `Organization` type these four were all well-typed, so "which
    companies are competing for the programs my customer runs" was not a
    question the graph could answer -- COMPETES_FOR had to accept the customer
    too. The first full run duly produced `RTX --COMPETES_FOR--> Army`.
    """
    ontology = get_ontology()
    # A company competes; the body running the program does not compete for it.
    assert ontology.check_relation("COMPETES_FOR", "Company", "Program") is None
    assert ontology.check_relation("COMPETES_FOR", "GovernmentBody", "Program") is not None
    assert ontology.check_relation("COMPETES_FOR", "MilitaryUnit", "Program") is not None
    # And acquisition authority runs the other way.
    assert ontology.check_relation("MANAGES", "MilitaryUnit", "Program") is None
    assert ontology.check_relation("MANAGES", "Company", "Program") is not None
    # Only a company builds a platform or buys another company.
    assert ontology.check_relation("PRODUCES", "Company", "Platform") is None
    assert ontology.check_relation("PRODUCES", "GovernmentBody", "Platform") is not None
    assert ontology.check_relation("ACQUIRED", "Company", "Company") is None
    assert ontology.check_relation("ACQUIRED", "MilitaryUnit", "Company") is not None


def test_the_org_chart_still_crosses_the_four_types() -> None:
    """PART_OF must span them, or the hierarchy the split exists to express
    breaks at every boundary: a MilitaryUnit is PART_OF a GovernmentBody (US
    Army under the department) and a GovernmentBody is PART_OF a MilitaryUnit
    (PEO C3N under the US Army). Narrowing this was the tempting mistake."""
    ontology = get_ontology()
    assert ontology.check_relation("PART_OF", "MilitaryUnit", "GovernmentBody") is None
    assert ontology.check_relation("PART_OF", "GovernmentBody", "MilitaryUnit") is None
    assert ontology.check_relation("PART_OF", "Company", "Company") is None
    assert ontology.check_relation("PART_OF", "ResearchOrg", "GovernmentBody") is None
    # Countries are never a PART_OF object -- that is BASED_IN.
    assert ontology.check_relation("PART_OF", "GovernmentBody", "Country") is not None
    assert ontology.check_relation("BASED_IN", "GovernmentBody", "Country") is None


def test_org_type_is_emittable_by_the_model() -> None:
    """The vocabulary was declared, prompted, stored, projected and served while
    `analyze_schema()` omitted the property and set `additionalProperties:
    false` -- so the model was instructed to emit a field the schema forbade,
    and all 346 Organizations on the first full run had `org_type IS NULL`."""
    from venona.llm.schemas import analyze_schema

    item = analyze_schema()["properties"]["entities"]["items"]
    assert "org_type" in item["properties"], "the guide asks for it; the schema must allow it"

    # NAMED IN `required`, and null is a legal value. The property the scar is
    # about is that the field must be EMITTABLE -- and it must also be possible
    # to say "this entity has none", because the vocabulary is meaningless for
    # Program, Technology, Platform, Person and Country. Absence expressed as
    # null rather than as omission keeps both, and is the only form a provider
    # that validates strictly will accept at all.
    assert "org_type" in item["required"]
    org_type = item["properties"]["org_type"]
    assert "null" in org_type["type"], "the meaningless case must stay expressible"
    enum = set(org_type["enum"])
    assert None in enum, "a nullable field whose enum omits null cannot be null"
    assert {"program_office", "startup", "ffrdc", "service"} <= enum


def test_a_rejected_relation_names_the_triple_that_was_rejected() -> None:
    """Five of run:06a74b50's six warnings were unactionable.

    `check_relation` closed with the ontology's EXAMPLE, unlabelled, so the
    warning read as though `Anduril Industries --PRODUCES--> Lattice` had been
    rejected -- entities that were never in the article. Since the model's raw
    output is clipped to 300 chars in the transcript, the warning is the only
    record, and it named the wrong thing.

    Driven with USES_TECH(MilitaryUnit, Program) -- a shape the v4 rewrite
    table deliberately leaves unrewritable (turning it into MANAGES would
    assert acquisition authority the evidence does not support). The original
    shape here, PRODUCES(Company, Program), is now rewritten rather than
    rejected; see test_an_ill_typed_relation_is_rewritten_not_dropped.
    """
    ontology = get_ontology()
    problem = ontology.check_relation("USES_TECH", "MilitaryUnit", "Program")
    assert problem is not None
    assert "correct form e.g." in problem, "the example must be labelled as one"
    assert ontology.rewrite_for("USES_TECH", "MilitaryUnit", "Program") is None, (
        "this shape must stay unrewritable -- the table's value is as much in "
        "what it refuses as what it fixes"
    )

    from venona.pipeline.analyze import AnalyzeReport, _ingest
    from venona.pipeline.types import FrozenClock, RunContext, SeededIdGen

    report = AnalyzeReport()
    ctx = RunContext(
        run_id="run:t",
        db=None,
        llm=None,
        settings=get_settings(),
        clock=FrozenClock(),
        ids=SeededIdGen(0),
    )
    _ingest(
        ctx,
        resolver=None,
        ontology=ontology,
        cluster_id="cluster:t",
        article_id="doc:t",
        data={
            "entities": [],
            "relations": [
                {
                    "subject_surface": "Northcom",
                    "subject_type": "MilitaryUnit",
                    "predicate": "USES_TECH",
                    "object_surface": "Operation Noble Eagle",
                    "object_type": "Program",
                    "evidence": "Northcom uses Operation Noble Eagle.",
                }
            ],
        },
        report=report,
    )

    assert report.relations_rejected == 1
    # A notice, not a warning: the type check rejecting a relation is it working,
    # and it must not be what makes a run report `succeeded_partial`.
    assert report.warnings == []
    notice = report.notices[0]
    assert "Northcom" in notice
    assert "Operation Noble Eagle" in notice
    assert "USES_TECH" in notice


def test_every_rewrite_row_maps_an_ill_typed_shape_to_a_legal_one() -> None:
    """The two invariants the loader enforces, re-asserted against the live
    config so weakening the loader cannot pass silently: every shape a rewrite
    covers is ill-typed today (a legal triple must never be rewritten) and
    every result is well-typed (the point of the table). Together these make
    the table acyclic -- an output can never match another row's input."""
    ontology = get_ontology()
    assert ontology.rewrites, "v4 declares a rewrites table"
    for row in ontology.rewrites:
        for predicate in row.predicates:
            for subj in row.subjects:
                for obj in row.objects:
                    assert ontology.check_relation(predicate, subj, obj) is not None, (
                        f"{subj} --{predicate}--> {obj} is legal; a rewrite may only "
                        f"cover ill-typed shapes"
                    )
                    new_subj, new_obj = (obj, subj) if row.swap else (subj, obj)
                    assert ontology.check_relation(row.to_predicate, new_subj, new_obj) is None, (
                        f"rewriting {predicate} yields an ill-typed {row.to_predicate}"
                    )
                    assert ontology.rewrite_for(row.to_predicate, new_subj, new_obj) is None, (
                        "a rewrite output matched another row -- the table has a cycle"
                    )


def test_the_shapes_deliberately_left_unrewritable_stay_rejections() -> None:
    """The table's value is as much in what it refuses as what it fixes.

    Each of these had a candidate rewrite that was rejected in review:
    USES_TECH -> MANAGES asserts acquisition authority the evidence does not
    support; OFFERS -> PRODUCES strengthens "markets" into "builds" (a
    reseller is not a manufacturer); LEADS(unit, unit) -> swapped PART_OF is
    the right reading for SOCCENT -> Task Force but not derivable from types.
    """
    ontology = get_ontology()
    for predicate, subj, obj in (
        ("USES_TECH", "MilitaryUnit", "Program"),
        ("OFFERS", "Company", "Platform"),
        ("LEADS", "MilitaryUnit", "MilitaryUnit"),
    ):
        assert ontology.rewrite_for(predicate, subj, obj) is None
        assert ontology.check_relation(predicate, subj, obj) is not None


def test_an_ill_typed_relation_is_rewritten_not_dropped() -> None:
    """PART_OF(Platform, Program) -- three of run:06a83e69's five PART_OF
    rejections -- becomes FIELDS with the endpoints swapped, and every counter
    is keyed on the predicate the MODEL asserted. Keyed on the rewritten one,
    `reject_ontology_PART_OF` would read zero whether or not anything was
    fixed, and the measurement the table exists to produce would be
    self-fulfilling.

    Surfaces are empty so the relation stops at endpoint resolution (an empty
    surface resolves to None before the resolver is consulted) -- the rewrite
    and its accounting all happen before that, and nothing touches the db.
    """
    from venona.pipeline.analyze import AnalyzeReport, _ingest
    from venona.pipeline.types import FrozenClock, RunContext, SeededIdGen

    ontology = get_ontology()
    report = AnalyzeReport()
    rejections: dict[str, int] = {}
    rewrites: dict[str, int] = {}
    ctx = RunContext(
        run_id="run:t",
        db=None,
        llm=None,
        settings=get_settings(),
        clock=FrozenClock(),
        ids=SeededIdGen(0),
    )
    _ingest(
        ctx,
        resolver=None,
        ontology=ontology,
        cluster_id="cluster:t",
        article_id="doc:t",
        data={
            "entities": [],
            "relations": [
                {
                    "subject_surface": "",
                    "subject_type": "Platform",
                    "predicate": "PART_OF",
                    "object_surface": "",
                    "object_type": "Program",
                    "evidence": "The Rogue 1 is part of the LASSO program.",
                }
            ],
        },
        report=report,
        rejections=rejections,
        rewrites=rewrites,
    )

    assert rewrites == {"PART_OF": 1}, "counted under the ORIGINAL predicate"
    assert "ontology_PART_OF" not in rejections, "a rewritten relation is not a rejection"
    assert "ontology_FIELDS" not in rejections, (
        "keying the reject counter on the rewritten predicate makes the "
        "rewrite metric self-fulfilling"
    )
    notice = report.notices[0]
    assert "rewrote PART_OF -> FIELDS" in notice
    assert "(endpoints swapped)" in notice
    # The swap moved the pairs together: Program is now the subject type.
    assert "[Program] --FIELDS--> ? [Platform]" in notice


def test_a_rejected_shape_is_recorded_with_its_first_example() -> None:
    """The aggregate the warning scroll cannot carry: (predicate, subject
    type, object type) with a count and one verbatim instance. Only the
    ontology branch records -- a rewritten-then-legal relation and a
    no-evidence drop leave the dict untouched.
    """
    from venona.pipeline.analyze import AnalyzeReport, _ingest
    from venona.pipeline.types import FrozenClock, RunContext, SeededIdGen

    ontology = get_ontology()
    report = AnalyzeReport()
    rejected_shapes: dict[tuple[str, str, str], dict] = {}

    def ingest(relations: list[dict]) -> None:
        _ingest(
            RunContext(
                run_id="run:t",
                db=None,
                llm=None,
                settings=get_settings(),
                clock=FrozenClock(),
                ids=SeededIdGen(0),
            ),
            resolver=None,
            ontology=ontology,
            cluster_id="cluster:t",
            article_id="doc:t",
            data={"entities": [], "relations": relations},
            report=report,
            rejected_shapes=rejected_shapes,
        )

    ill_typed = {
        "subject_surface": "SOCCENT",
        "subject_type": "MilitaryUnit",
        "predicate": "USES_TECH",
        "object_surface": "LASSO",
        "object_type": "Program",
        "evidence": "SOCCENT uses the LASSO program.",
    }
    ingest([ill_typed, {**ill_typed, "subject_surface": "AFSOC", "evidence": "AFSOC too."}])
    shape = rejected_shapes[("USES_TECH", "MilitaryUnit", "Program")]
    assert shape["n"] == 2
    assert shape["example_subject"] == "SOCCENT", "the FIRST example is kept"
    assert shape["example_evidence"] == "SOCCENT uses the LASSO program."
    assert shape["example_article_id"] == "doc:t"

    # A rewrite that lands legal records nothing -- the shape was expressible.
    ingest(
        [
            {
                "subject_surface": "",
                "subject_type": "Platform",
                "predicate": "PART_OF",
                "object_surface": "",
                "object_type": "Program",
                "evidence": "The Rogue 1 is part of the LASSO program.",
            }
        ]
    )
    # A no-evidence drop is not a contract problem.
    ingest([{**ill_typed, "evidence": ""}])
    assert set(rejected_shapes) == {("USES_TECH", "MilitaryUnit", "Program")}
    assert rejected_shapes[("USES_TECH", "MilitaryUnit", "Program")]["n"] == 2


def test_a_glossary_card_carries_its_expansion_in_definition(conn: db.Conn) -> None:
    """The field the reader must read, pinned.

    `Card` has BOTH `expansion` and `definition`, and only entity cards populate
    `expansion` -- a glossary card leaves it null and puts the acronym's
    expansion in `definition`. The hover card's "Look this up" checked
    `expansion` to decide whether the lookup had grounded anything, so a
    successful grounding was recorded as a rejection and the `grounded` state
    was unreachable. Nothing caught it because the two fields are both
    `str | None` on the same model.
    """
    from venona.services.entity_service import glossary_card

    row = conn.execute(
        "SELECT id FROM acronym_definitions WHERE expansion IS NOT NULL AND status = 'active'"
    ).fetchone()
    assert row is not None, "seed must carry a grounded acronym"

    card = glossary_card(conn, row["id"])
    assert card is not None
    assert card.kind == "glossary"
    assert card.definition, "a grounded glossary card must expose `definition`"
    assert card.expansion is None, (
        "`expansion` is the ENTITY card's field; a client that reads it here "
        "sees null for every grounded acronym"
    )


def test_symmetric_predicates_have_matching_domain_and_range() -> None:
    ontology = get_ontology()
    for name in ontology.symmetric_predicates:
        spec = ontology.predicates[name]
        assert set(spec.domain) == set(spec.range), f"{name} is symmetric but asymmetric-typed"


def test_every_seeded_edge_type_checks(conn: db.Conn) -> None:
    """Seed data must itself obey the ontology -- otherwise the fixture teaches
    the wrong shape."""
    ontology = get_ontology()
    types = {r["id"]: r["type"] for r in conn.execute("SELECT id, type FROM entities")}
    problems: list[str] = []

    sql = """
        SELECT predicate, subject_id_raw AS s, object_id_raw AS o
        FROM assertions WHERE kind = 'relation'
    """
    for row in conn.execute(sql):
        st, ot = types.get(row["s"]), types.get(row["o"])
        if st is None or ot is None:
            problems.append(f"{row['s']} -> {row['o']}: unknown endpoint")
            continue
        problem = ontology.check_relation(row["predicate"], st, ot)
        if problem:
            problems.append(f"{row['s']} -> {row['o']}: {problem}")

    assert not problems, "seed edges violate the ontology:\n" + "\n".join(problems[:10])


def test_every_entity_type_has_a_design_token() -> None:
    """C5: components resolve colours with var(--entity-${prefix}); a missing
    member renders transparent rather than erroring."""
    from pathlib import Path

    tokens = (
        Path(__file__).resolve().parents[1] / "web" / "src" / "styles" / "tokens.css"
    ).read_text()
    declared = set(re.findall(r"^\s*(--[a-zA-Z0-9-]+)\s*:", tokens, re.MULTILINE))

    for spec in get_ontology().entity_types.values():
        assert f"--entity-{spec.prefix}" in declared, f"missing --entity-{spec.prefix}"
    for extra in ("glossary", "undefined"):
        assert f"--entity-{extra}" in declared


# ── C1: assertions are evidence-bearing ──────────────────────────────


def test_every_assertion_carries_evidence(conn: db.Conn) -> None:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM assertions WHERE evidence_quote IS NULL OR evidence_quote = ''"
    ).fetchone()
    assert row["n"] == 0


def test_active_glossary_entries_are_grounded(conn: db.Conn) -> None:
    """Invariant 3: a definition exists only if traceable to a document."""
    rows = conn.execute(
        "SELECT surface FROM acronym_definitions "
        "WHERE status = 'active' AND (grounding_article_id IS NULL OR grounding_quote IS NULL)"
    ).fetchall()
    assert not rows, f"ungrounded active definitions: {[r['surface'] for r in rows]}"


# ── C8 / annotation behaviour ────────────────────────────────────────


def test_brief_assembles_and_exposes_all_reference_kinds(conn: db.Conn) -> None:
    brief_id = latest_brief_id(conn)
    assert brief_id
    brief = get_brief(conn, brief_id)
    assert brief is not None
    assert brief.sections and brief.entities

    kinds = {
        run.k
        for section in brief.sections
        for block in section.blocks
        for run in block.runs
        if run.k
    }
    assert "entity" in kinds
    assert "glossary" in kinds
    assert "undefined" in kinds, "the seed brief must exercise the undefined path"


def test_stoplisted_surface_does_not_match(conn: db.Conn) -> None:
    """`Army` alone is ordinary English; matching it globally would light up
    every paragraph."""
    index = build_index(conn)
    blocks = markdown_to_blocks("The Army moved. An army of consultants arrived.")
    tier_a = frozenset(r["id"] for r in conn.execute("SELECT id FROM entities"))
    blocks = annotate_blocks(blocks, index, tier_a=tier_a)
    matched = {blocks[0].text[s["start"] : s["end"]] for s in blocks[0].spans}
    assert "army" not in {m.lower() for m in matched} or "The Army" in matched


def test_ambiguous_surface_is_never_auto_matched(conn: db.Conn) -> None:
    """SDA and TITAN have two plausible owners each; the card disambiguates
    rather than the annotator guessing."""
    index = build_index(conn)
    blocks = markdown_to_blocks("SDA and TITAN were both discussed.")
    tier_a = frozenset(r["id"] for r in conn.execute("SELECT id FROM entities"))
    blocks = annotate_blocks(blocks, index, tier_a=tier_a)
    matched = {blocks[0].text[s["start"] : s["end"]] for s in blocks[0].spans}
    assert "SDA" not in matched
    assert "TITAN" not in matched


def test_predicate_kind_survives_to_the_projection(conn: db.Conn) -> None:
    """`PART_OF.kind` distinguishes an org chart from a corporate acquisition.

    Regression guard: the projector originally never read it, so all 11 seeded
    `org_hierarchy` edges reached Neo4j without it. Neo4j reported this as an
    UnknownPropertyKeyWarning on every path query -- a warning that was easy to
    ignore and that meant a declared ontology field was being silently dropped.
    """
    rows = conn.execute(
        "SELECT predicate_kind, COUNT(*) AS n FROM assertions "
        "WHERE kind = 'relation' AND predicate = 'PART_OF' GROUP BY predicate_kind"
    ).fetchall()
    kinds = {r["predicate_kind"]: r["n"] for r in rows}

    assert kinds, "seed declares PART_OF edges"
    assert None not in kinds, f"every PART_OF assertion needs a kind, got {kinds}"
    assert "org_hierarchy" in kinds


def test_declared_kinds_are_valid_for_their_predicate(conn: db.Conn) -> None:
    ontology = get_ontology()
    for row in conn.execute(
        "SELECT DISTINCT predicate, predicate_kind FROM assertions "
        "WHERE kind = 'relation' AND predicate_kind IS NOT NULL"
    ):
        spec = ontology.predicates.get(row["predicate"])
        assert spec is not None
        assert row["predicate_kind"] in spec.kinds, (
            f"{row['predicate']} kind={row['predicate_kind']!r} not in {list(spec.kinds)}"
        )


@pytest.mark.negative
def test_a_predicate_added_after_import_reaches_the_projection(
    conn: db.Conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C10 over HTTP: the whitelist must be read per call, not bound at import.

    `db/models.py` used to bind `PREDICATES` at import time and three modules
    imported the name directly. The ontology is editable from Settings now, and
    a run triggered from the UI executes IN the API process -- so adding a
    predicate produced a model that was told it could use it, a JSON-Schema enum
    (regenerated live) that accepted it, and then a silent drop.

    Asserted through `_edge_rows`, NOT `upsert_edges`. The projector filters
    first, so a writer-level test passes against a still-broken path -- which is
    exactly how the first version of this fix was going to be declared done.
    """
    from venona.graph import projector

    real = get_ontology()
    added = {**real.predicates, "SUPPLIES": real.predicates["MANAGES"]}
    extended = replace(real, predicates=added)
    monkeypatch.setattr(projector, "get_ontology", lambda: extended)

    row = conn.execute(
        "SELECT subject_id_raw, object_id_raw, article_id, created_at FROM assertions "
        "WHERE kind = 'relation' AND retracted_at IS NULL LIMIT 1"
    ).fetchone()
    assert row, "seed declares relation assertions"
    conn.execute(
        "INSERT INTO assertions (id, run_id, article_id, cluster_id, kind, predicate, "
        "subject_id_raw, object_id_raw, evidence_quote, confidence, method, created_at) "
        "VALUES ('a:supplies-1','run:seed-0001',?,'cluster:seed','relation','SUPPLIES',?,?,"
        "'A quote naming the supply relationship.',0.9,'seed',?)",
        (row["article_id"], row["subject_id_raw"], row["object_id_raw"], row["created_at"]),
    )

    by_predicate = projector._edge_rows(conn, {})
    assert "SUPPLIES" in by_predicate, (
        "the projector dropped a predicate the live ontology declares -- "
        "it is reading an import-time snapshot again"
    )


# ── C5: the token layer is legible, not just declared ────────────────
#
# `lint_tokens.py` checks STRUCTURE: no raw colour outside tokens.css, no tier-1
# reference from a component, no undeclared name. It is completely blind to
# VALUES -- a re-map is not a rename, so nothing fires.
#
# That blindness shipped real defects. A proposed `--text-quiet: --grey-500`
# measured 3.34:1 and would have been applied to every micro-label in the app;
# `--text-tertiary` was sitting at 4.38:1 on `--surface-overlay`; and
# `--surface-inset` was LIGHTER than `--surface-raised`, so the recessed token
# out-ranked the raised one. None of that is catchable by eye on a dark UI, and
# all of it is arithmetic.
#
# TWO PALETTES NOW, AND THE SWEEPS HAD TO BE REBUILT FOR IT.
#
# Every colour token is `light-dark(light, dark)`. The resolver here used to keep
# only values matching `#rrggbb`, so under `light-dark()` NO colour token would
# have matched -- and every sweep below would have quietly measured an empty
# dict while still passing. The guard against that was `assert surfaces and
# texts`, which one surviving token satisfies. A contrast suite that measures
# nothing and reports green is worse than no suite: it is the reason to stop
# looking.
#
# So: pick the side FIRST, then follow `var()`, and assert exact family counts
# rather than non-emptiness.

THEMES = ("light", "dark")

# What each family must contain, so a sweep that silently stopped seeing a
# family fails instead of passing over four tokens. These are the counts, not a
# minimum: adding a surface or an entity type is a deliberate act and should
# require touching this line.
# --entity-: 9 slots + 9 defense prefix aliases + glossary + undefined. The
# aliases resolve through their slots, so the sweep measures each hue twice --
# cheap, and it keeps the alias layer inside the contrast contract.
# --code-: THREE, not four. `--code-search-match` is a wash (`rgb(... / 16%)`),
# and `_resolve_tokens` keeps only 6-digit hex -- so it is invisible to this
# sweep by construction, which is correct for a translucent ground and is
# exactly why the count is pinned: without it a hue silently becoming a wash
# would shrink the sweep rather than fail it.
_FAMILY_SIZES = {"--surface-": 5, "--entity-": 20, "--code-": 3}


def _tokens_css() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[1] / "web" / "src" / "styles" / "tokens.css"
    ).read_text()


def _split_top_level(value: str) -> list[str]:
    """Split on commas that are not inside parentheses.

    `--scrim` is `light-dark(rgb(0 0 0 / 8%), rgb(0 0 0 / 68%))`, so a naive
    `split(",")` -- or a `[^,)]+` regex -- cuts inside the `rgb()` and hands back
    two fragments that are neither colour.
    """
    parts, depth, current = [], 0, ""
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    parts.append(current.strip())
    return parts


def _declared_tokens() -> dict[str, str]:
    """Every declaration in tokens.css, last one winning.

    Last-wins is correct for this file and is asserted to be a no-op:
    `test_no_colour_token_is_declared_twice` fails if a second block ever starts
    redeclaring colours, because that would be the two-block shape this design
    deliberately does not use.
    """
    return dict(re.findall(r"^\s*(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);", _tokens_css(), re.MULTILINE))


def _resolve_tokens(theme: str) -> dict[str, str]:
    """Every token flattened to a hex literal for one theme.

    Order matters: `light-dark()` is unwrapped BEFORE `var()` is followed,
    because the sides are themselves `var()` references
    (`light-dark(var(--grey-075), var(--grey-950))`). Resolving the other way
    round finds no `var(` at the head of the value and gives up.
    """
    assert theme in THEMES, theme
    side = 0 if theme == "light" else 1
    raw = _declared_tokens()
    resolved: dict[str, str] = {}

    for name in raw:
        value, seen = raw[name].strip(), {name}
        while True:
            if value.startswith("light-dark(") and value.endswith(")"):
                sides = _split_top_level(value[len("light-dark(") : -1])
                assert len(sides) == 2, f"{name}: light-dark() takes exactly two sides"
                value = sides[side]
                continue
            reference = re.fullmatch(r"var\(\s*(--[a-zA-Z0-9-]+)\s*\)", value)
            # `seen.add(ref)`, not `seen.add(name)`. The original added the token
            # it started from, so the loop guard could only ever fire once and a
            # genuine cycle would have spun. Harmless while tier-2 -> tier-1 is
            # one hop; not something to leave loaded.
            if reference and reference.group(1) in raw and reference.group(1) not in seen:
                seen.add(reference.group(1))
                value = raw[reference.group(1)].strip()
                continue
            break
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            resolved[name] = value
    return resolved


def _family(tokens: dict[str, str], prefix: str, theme: str) -> dict[str, str]:
    """One token family, with its size asserted.

    This is the anti-vacuity guard, and it replaces `assert surfaces and texts`
    -- which a single surviving token satisfied while the other four had been
    silently dropped by a parser that no longer understood the file.
    """
    found = {k: v for k, v in tokens.items() if k.startswith(prefix)}
    expected = _FAMILY_SIZES.get(prefix)
    if expected is not None:
        assert len(found) == expected, (
            f"[{theme}] {prefix}*: resolved {len(found)} tokens, expected {expected}. "
            "Either a token was added without updating _FAMILY_SIZES, or the "
            "resolver stopped understanding tokens.css and this sweep is measuring "
            "less than it looks like it is."
        )
    return found


def _luminance(hex_colour: str) -> float:
    """WCAG 2.x relative luminance."""

    def channel(component: int) -> float:
        srgb = component / 255
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    lo, hi = sorted((la, lb))
    return (hi + 0.05) / (lo + 0.05)


def _lstar(hex_colour: str) -> float:
    """Perceptual lightness. `visible` is a perceptual claim, not a photometric
    one, so every ladder assertion below is in L* rather than luminance."""
    return 116 * _luminance(hex_colour) ** (1 / 3) - 16


# Two text tokens are not "text on a surface" and are checked separately below
# rather than exempted: an exemption with no assertion behind it is how a token
# drifts. `--text-inverse` is for text on a FILLED ACCENT, and `Button` owns
# that pairing. `--text-quiet` is specified for exactly one ground.
_TEXT_TOKENS_CHECKED_ELSEWHERE = ("--text-inverse", "--text-quiet")


def test_both_palettes_declare_the_same_tokens() -> None:
    """A `light-dark()` with one side missing is not a CSS error.

    It is an invalid declaration, so the token falls back to whatever it
    inherited -- which on a fresh custom property is nothing at all, and the
    element renders transparent on ONE theme only. Nobody notices until they
    switch. The two key sets have to be identical, and any difference names the
    tokens rather than just failing.
    """
    light, dark = _resolve_tokens("light"), _resolve_tokens("dark")
    assert set(light) == set(dark), (
        "resolved on one theme and not the other: "
        f"light-only={sorted(set(light) - set(dark))} "
        f"dark-only={sorted(set(dark) - set(light))}"
    )


def test_no_colour_token_is_declared_twice() -> None:
    """One block, not two, and this is what holds the file to that shape.

    The alternative design was `:root` plus `:root[data-theme='light']`
    redeclaring every colour. It was rejected because two copies of a palette
    drift -- a token retuned in one block and not the other is invisible until
    somebody switches themes. `_declared_tokens()` is last-wins, so if that shape
    ever crept back the sweeps below would silently measure only the later block.

    A DIMENSION may be re-pointed inside an `@media` block -- the phone tier
    collapses `--edge` (ADR 0152), and a breakpoint is exactly what a media
    query is for. Colours stay single-declaration everywhere: a colour inside
    `@media` is the two-palette shape whichever query wraps it, so those fail
    even as the only declaration of their name.
    """
    colour = re.compile(r"light-dark\(|#[0-9a-fA-F]{3}|\brgba?\(|\boklch\(|\bhsla?\(")
    decl = re.compile(r"^\s*(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);")
    outside: list[str] = []
    in_media: list[tuple[str, str]] = []
    depth, media_floor = 0, None
    for line in _tokens_css().splitlines():
        m = decl.match(line)
        if m:
            if media_floor is None:
                outside.append(m.group(1))
            else:
                in_media.append((m.group(1), m.group(2)))
        if media_floor is None and re.match(r"\s*@media\b", line):
            media_floor = depth
        depth += line.count("{") - line.count("}")
        if media_floor is not None and depth <= media_floor:
            media_floor = None

    duplicates = sorted({name for name in outside if outside.count(name) > 1})
    assert not duplicates, (
        "declared more than once in tokens.css: "
        + ", ".join(duplicates)
        + " -- the light palette belongs inside light-dark(), not in a second block"
    )
    coloured = sorted(
        f"{name}: {value.strip()}" for name, value in in_media if colour.search(value)
    )
    assert not coloured, (
        "colour declared inside @media in tokens.css: "
        + ", ".join(coloured)
        + " -- a media block may re-point a dimension, never a colour"
    )
    orphans = sorted({name for name, _ in in_media} - set(outside))
    assert not orphans, (
        "declared ONLY inside @media in tokens.css: "
        + ", ".join(orphans)
        + " -- a media block re-points an existing token; the base block is the system of record"
    )


@pytest.mark.parametrize("theme", THEMES)
def test_every_text_token_is_legible_on_every_surface(theme: str) -> None:
    """WCAG AA for body text, on both palettes. See
    `_TEXT_TOKENS_CHECKED_ELSEWHERE` for the two tokens that are not
    text-on-a-surface and get their own assertions."""
    tokens = _resolve_tokens(theme)
    surfaces = _family(tokens, "--surface-", theme)
    texts = {
        k: v
        for k, v in tokens.items()
        if k.startswith("--text-") and k not in _TEXT_TOKENS_CHECKED_ELSEWHERE
    }
    assert len(texts) == 4, f"[{theme}] expected four text-on-surface tokens, got {sorted(texts)}"

    failures = [
        f"{text} on {surface}: {_contrast(tv, sv):.2f}:1"
        for text, tv in texts.items()
        for surface, sv in surfaces.items()
        if _contrast(tv, sv) < 4.5
    ]
    assert not failures, f"[{theme}] text below 4.5:1 (WCAG AA):\n" + "\n".join(sorted(failures))


@pytest.mark.parametrize("theme", THEMES)
def test_text_quiet_is_legible_where_it_is_used(theme: str) -> None:
    """`--text-quiet` is the ContextBar's route identity and nothing else, and
    the ContextBar is recessed onto `--surface-base` by design -- so AA is
    asserted there, on the ground it actually renders on, rather than across
    surfaces it never touches.

    The 3:1 floor everywhere else is not decoration: the moment a second
    consumer puts this token on a raised or overlay surface, that pairing has to
    be argued for here rather than discovered by a reader squinting at an 11px
    uppercase label. The design bundle specifies `--grey-500` for this token,
    which is 3.52:1 on base and 2.96:1 on overlay -- failing AA and missing the
    bundle's own stated floor -- so the dark value is one rung up at `--grey-450`.

    This is the token that has been caught twice, and it is the tightest
    constraint in the light palette too: at 4.53:1 on `--surface-base` it is what
    fixes how light the light page may be. There is no headroom here in either
    direction, on either theme.
    """
    tokens = _resolve_tokens(theme)
    quiet = tokens["--text-quiet"]
    surfaces = _family(tokens, "--surface-", theme)

    base = _contrast(quiet, tokens["--surface-base"])
    assert base >= 4.5, f"[{theme}] --text-quiet on --surface-base: {base:.2f}:1, below WCAG AA"

    floor = [
        f"{surface}: {_contrast(quiet, sv):.2f}:1"
        for surface, sv in surfaces.items()
        if _contrast(quiet, sv) < 3.0
    ]
    assert not floor, f"[{theme}] --text-quiet below the 3:1 floor:\n" + "\n".join(sorted(floor))


@pytest.mark.parametrize("theme", THEMES)
def test_every_entity_hue_is_legible_on_every_surface(theme: str) -> None:
    """docs/DESIGN.md commits to >=3:1 for entity hues. The commitment said
    "against `--surface-base`", but entity chips and mentions render on selected
    rows, rails and inside the hover card too -- so it has to hold everywhere,
    which is what caps how far `--surface-selected` can be tinted."""
    tokens = _resolve_tokens(theme)
    surfaces = _family(tokens, "--surface-", theme)
    hues = _family(tokens, "--entity-", theme)

    failures = [
        f"{hue} on {surface}: {_contrast(hv, sv):.2f}:1"
        for hue, hv in hues.items()
        for surface, sv in surfaces.items()
        if _contrast(hv, sv) < 3.0
    ]
    assert not failures, f"[{theme}] entity hue below 3:1:\n" + "\n".join(sorted(failures))


@pytest.mark.parametrize("theme", THEMES)
def test_every_code_token_is_legible_on_the_editor_ground(theme: str) -> None:
    """Syntax colour is body text, so it clears AA rather than the 3:1 entity bar.

    ONE SURFACE, not the ladder: `--code-*` is only ever drawn on the editor,
    and the editor is `--surface-inset` and nothing else. Sweeping every surface
    would be asserting a promise the palette does not make and does not need to.
    """
    tokens = _resolve_tokens(theme)
    ground = tokens["--surface-inset"]
    hues = _family(tokens, "--code-", theme)

    failures = [
        f"{name} on --surface-inset: {_contrast(value, ground):.2f}:1"
        for name, value in hues.items()
        if _contrast(value, ground) < 4.5
    ]
    assert not failures, f"[{theme}] code colour below 4.5:1 (WCAG AA):\n" + "\n".join(
        sorted(failures)
    )


@pytest.mark.parametrize("theme", THEMES)
def test_the_surface_ladder_is_monotonic(theme: str) -> None:
    """base -> raised -> overlay climbs; inset recesses; every step is visible.

    THE SHAPE IS NOT THE SAME ON BOTH THEMES, and asserting the dark one would
    fail light mode for being correct. At night the page is the darkest thing
    and a recess goes LIGHTER; on paper the page is a grey and a recess goes
    DARKER. So the direction is derived from `--surface-base`'s own lightness
    rather than declared per theme -- which also states the rule in the form a
    reader needs it: a recess moves away from white on paper and toward it at
    night.

    The two original defects both still fail, in both directions:
      * `--surface-inset` out-ranking `--surface-raised` -- the recessed token
        reading as more elevated than the raised one.
      * base -> raised at ~2.8 L*, under the just-noticeable difference for two
        large adjacent fields, which is why every region needed a border to read
        at all.

    `--surface-selected` is exempt: it is a tinted state, not a rung.
    """
    tokens = _resolve_tokens(theme)
    base, inset, raised, overlay = (
        _lstar(tokens[f"--surface-{name}"]) for name in ("base", "inset", "raised", "overlay")
    )

    # Elevation is always toward white, on both themes: a raised panel is lighter
    # than the page it sits on whether the page is near-black or near-white.
    assert raised > base, f"[{theme}] --surface-raised must be lighter than --surface-base"
    assert overlay > raised, f"[{theme}] --surface-overlay must be lighter than --surface-raised"
    assert raised - base >= 5.0, (
        f"[{theme}] base -> raised is only {raised - base:.1f} L*; borders will do all the work"
    )

    # The recess. Its DIRECTION follows the page: away from white on paper,
    # toward it at night.
    recess = inset - base
    page_is_dark = base < 50.0
    if page_is_dark:
        assert recess > 0, (
            f"[{theme}] --surface-inset must be LIGHTER than a dark page: {recess:+.1f} L*"
        )
    else:
        assert recess < 0, (
            f"[{theme}] --surface-inset must be DARKER than a paper page: {recess:+.1f} L*"
        )
    assert abs(recess) >= 3.0, (
        f"[{theme}] --surface-inset is {abs(recess):.1f} L* from base; the recess is invisible"
    )
    assert abs(recess) < raised - base, (
        f"[{theme}] --surface-inset sits {abs(recess):.1f} L* from base and --surface-raised "
        f"{raised - base:.1f}; the recessed token out-ranks the raised one"
    )


@pytest.mark.parametrize("theme", THEMES)
def test_a_filled_signal_is_legible_in_both_palettes(theme: str) -> None:
    """The fills `components/ui/Button.vue` paints a whole control with.

    Not covered by the text-on-surface sweep above: a signal token is not a
    surface, so `--signal-critical` under a destructive confirm was never
    measured against the text on top of it. It shipped with `--text-primary`
    from a 2026-08-06 reading and ADR 0133's palette rework inverted the answer
    -- 3.53:1 on paper, 3.92:1 at night, both under AA -- without anything
    failing.
    """
    tokens = _resolve_tokens(theme)
    ink = tokens["--text-inverse"]
    failures = [
        f"{name}: {_contrast(ink, fill):.2f}:1"
        for name, fill in _family(tokens, "--signal-", theme).items()
        if _contrast(ink, fill) < 4.5
    ]
    assert not failures, (
        f"[{theme}] a filled signal below 4.5:1 against `--text-inverse`:\n"
        + "\n".join(sorted(failures))
    )


def test_no_control_paints_a_signal_fill_with_body_ink() -> None:
    """The other half of the pair, in the file that chooses it.

    The token test above proves `--text-inverse` is legible on every signal
    fill; this proves the control asks for it. `bg-signal-critical` with
    `text-text-primary` measures 3.53:1 on paper, and nothing in the token
    layer can see that pairing.
    """
    from pathlib import Path

    button = (
        Path(__file__).resolve().parents[1] / "web" / "src" / "components" / "ui" / "Button.vue"
    )
    source = button.read_text()
    offenders = [
        line.strip()
        for line in source.splitlines()
        if "bg-signal-" in line and "text-text-primary" in line
    ]
    assert not offenders, "a signal fill under body ink:\n" + "\n".join(offenders)
