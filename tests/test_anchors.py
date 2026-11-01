"""Top-level structure: the promotion gate after a merge, and the anchor spine.

The graph was disconnected for three compounding reasons, and only the third was
a schema gap. These tests pin the first two, which are the ones that actually
moved the number: a merge that never told the corroboration gate, and a
declarative spine whose relationship keys were read by nothing.

Offline and free, like the rest of `make test`.
"""

from __future__ import annotations

import pytest
from helpers import NOW, entity
from venona.db import core as db


def _sighting(conn: db.Conn, entity_id: str, *, n: int, domain: str) -> None:
    """One article from `domain`, attributed to `entity_id`."""
    doc_id = f"doc:{entity_id.replace(':', '-')}-{n}-{domain}"
    db.upsert(
        conn,
        "documents",
        {
            "id": doc_id,
            "source_id": "breaking_defense",
            "site_domain": domain,
            "url": f"https://{domain}/{n}",
            "url_key": f"{domain}/{n}",
            "title": "T",
            "text_nfc": "body",
            "text_chars": 4,
            "content_hash": doc_id,
            "cluster_id": f"cluster:{n}",
            "fetched_at": NOW,
            "created_at": NOW,
        },
        key=["id"],
    )
    db.insert(
        conn,
        "assertions",
        {
            "id": f"assert:{doc_id}",
            "run_id": "run:test",
            "article_id": doc_id,
            "cluster_id": f"cluster:{n}",
            "kind": "entity",
            "entity_id_raw": entity_id,
            "evidence_quote": "They did a thing.",
            "confidence": 0.9,
            "method": "extraction",
            "created_at": NOW,
        },
    )


# ── a merge must tell the corroboration gate ─────────────────────────


def test_a_merge_promotes_the_survivor_it_just_corroborated(conn: db.Conn) -> None:
    """The bug that hid 81% of the graph's relationships.

    Consolidating evidence is the POINT of a merge -- `promote_provisionals`
    aggregates over `entity_mappings`, so two single-article entities become one
    two-article entity the moment the mapping lands. But the gate was called
    only from `pipeline/curate.py` and `analyze.py`, so a merge made from the
    CLI or the curation API consolidated the evidence and told nobody.

    Measured on the live database before the fix: 14 entities sat `provisional`
    while already clearing the gate, `company:palantir-technologies` among them at 7
    articles from 7 publishers -- 3.5x over, invisible in the graph, blocking
    six relationships.
    """
    from venona.services.curate import merge_entities

    entity(conn, "company:acme", "Acme Corp", "provisional")
    entity(conn, "company:acme-inc", "Acme Inc", "provisional")
    _sighting(conn, "company:acme", n=1, domain="one.com")
    _sighting(conn, "company:acme-inc", n=2, domain="two.com")
    conn.commit()

    # Apart, neither is corroborated: one article, one publisher each.
    assert conn.execute("SELECT status FROM entities WHERE id='company:acme'").fetchone()[0] == (
        "provisional"
    )

    merge_entities(
        conn,
        from_id="company:acme",
        to_id="company:acme-inc",
        merge_id="merge:1",
        reason="duplicate",
        now=NOW,
    )

    survivor = conn.execute("SELECT status FROM entities WHERE id='company:acme-inc'").fetchone()
    assert survivor["status"] == "active", (
        "the merged pair has two articles from two publishers; the gate must see it"
    )


def test_a_merge_never_demotes_a_bystander(conn: db.Conn) -> None:
    """Scoped to the survivor, and promotion-only.

    A merge changes the evidence of exactly one entity and can only ever add to
    it. Re-running the whole gate here would demote entities the merge said
    nothing about -- and a merge that silently demotes is the invisible kind of
    damage `CLAUDE.md` reserves its strongest language for.
    """
    from venona.services.curate import merge_entities

    entity(conn, "company:acme", "Acme Corp", "provisional")
    entity(conn, "company:acme-inc", "Acme Inc", "provisional")
    # An unrelated active entity with no evidence at all: the full gate would
    # demote it to `provisional`.
    entity(conn, "company:bystander", "Bystander Systems", "active")
    conn.commit()

    merge_entities(
        conn,
        from_id="company:acme",
        to_id="company:acme-inc",
        merge_id="merge:1",
        reason="duplicate",
        now=NOW,
    )

    bystander = conn.execute("SELECT status FROM entities WHERE id='company:bystander'").fetchone()
    assert bystander["status"] == "active", "a merge must not re-evaluate the whole table"

    # And the survivor itself, still uncorroborated, is not demoted either.
    survivor = conn.execute("SELECT status FROM entities WHERE id='company:acme-inc'").fetchone()
    assert survivor["status"] == "provisional"


def test_the_gate_can_be_scoped_and_made_promotion_only(conn: db.Conn) -> None:
    """`only` and `promote_only` are one gate with two knobs, not a second gate.

    The evidence arithmetic stays in `promote_provisionals`; two implementations
    that disagreed would be worse than none.
    """
    from venona.pipeline.resolve import promote_provisionals

    entity(conn, "company:in-scope", "In Scope", "provisional")
    entity(conn, "company:out-of-scope", "Out Of Scope", "active")
    for i, domain in enumerate(("one.com", "two.com")):
        _sighting(conn, "company:in-scope", n=i, domain=domain)
    conn.commit()

    promoted = promote_provisionals(conn, now=NOW, only={"company:in-scope"}, promote_only=True)

    assert promoted == ["company:in-scope"]
    assert (
        conn.execute("SELECT status FROM entities WHERE id='company:out-of-scope'").fetchone()[0]
        == "active"
    ), "an id outside `only` must not be touched"


# ── the anchor loader ────────────────────────────────────────────────


def _anchor_file(tmp_path, doc: str):
    """Write an anchors.yaml into a throwaway profile dir."""
    profile = tmp_path / "profile"
    profile.mkdir(exist_ok=True)
    (profile / "anchors.yaml").write_text(doc)
    return profile


def test_an_inline_part_of_becomes_an_edge(conn: db.Conn, tmp_path) -> None:
    """`part_of:` was live-looking DEAD CONFIG for the whole build.

    `_load_entities` read `id`, `type`, `name`, `aliases` and a few scalars, and
    none of the relationship keys -- so ~20 entities in `fixtures/seed/`
    declared a parent and silently produced an orphan node. Edges existed only
    because `edges.yaml` duplicated them by hand.
    """
    from venona.services.seed import load_anchors

    profile = _anchor_file(
        tmp_path,
        """
entities:
  - {id: company:parent, type: Company, name: Parent Corp}
  - {id: company:child, type: Company, name: Child Division, part_of: company:parent}
""",
    )
    load_anchors(conn, profile, quiet=True)

    row = conn.execute(
        "SELECT predicate, predicate_kind, subject_id_raw s, object_id_raw o FROM assertions "
        "WHERE kind='relation' AND run_id='run:anchors-0001'"
    ).fetchone()
    assert (row["s"], row["predicate"], row["o"]) == ("company:child", "PART_OF", "company:parent")
    assert row["predicate_kind"] == "org_hierarchy", (
        "declared in the INLINE_RELATIONS table, not left for each call site to remember"
    )


def test_a_reversed_shorthand_puts_the_declaring_entity_in_the_object_slot(
    conn: db.Conn, tmp_path
) -> None:
    """`managed_by:` and `produced_by:` read naturally on the child and assert
    the opposite direction. Getting one backwards inverts the org chart, which
    is why the direction lives in one table rather than at each call site."""
    from venona.services.seed import load_anchors

    profile = _anchor_file(
        tmp_path,
        """
entities:
  - {id: govt:office, type: GovernmentBody, name: Program Office}
  - {id: prog:thing, type: Program, name: The Thing, managed_by: govt:office}
""",
    )
    load_anchors(conn, profile, quiet=True)

    row = conn.execute(
        "SELECT subject_id_raw s, predicate p, object_id_raw o FROM assertions "
        "WHERE kind='relation' AND run_id='run:anchors-0001'"
    ).fetchone()
    assert (row["s"], row["p"], row["o"]) == ("govt:office", "MANAGES", "prog:thing")


def test_a_triple_declared_twice_is_written_once(conn: db.Conn, tmp_path) -> None:
    from venona.services.seed import load_anchors

    profile = _anchor_file(
        tmp_path,
        """
entities:
  - {id: company:parent, type: Company, name: Parent Corp}
  - {id: company:child, type: Company, name: Child Division, part_of: company:parent}
edges:
  - {src: company:child, predicate: PART_OF, dst: company:parent, kind: org_hierarchy}
""",
    )
    counts = load_anchors(conn, profile, quiet=True)
    assert counts["edges"] == 1


def test_a_dangling_endpoint_is_an_error_not_an_orphan(conn: db.Conn, tmp_path) -> None:
    """`profile/` is user-editable, so a typo must fail where the file is on
    screen -- not silently, and not later in a test nobody is running."""
    from venona.services.seed import load_anchors

    profile = _anchor_file(
        tmp_path,
        """
entities:
  - {id: company:parent, type: Company, name: Parent Corp}
edges:
  - {src: company:typo, predicate: PART_OF, dst: company:parent}
""",
    )
    with pytest.raises(ValueError, match="no such entity"):
        load_anchors(conn, profile, quiet=True)


def test_an_ill_typed_edge_is_refused_with_the_ontologys_own_message(
    conn: db.Conn, tmp_path
) -> None:
    from venona.services.seed import load_anchors

    profile = _anchor_file(
        tmp_path,
        """
entities:
  - {id: country:xx, type: Country, name: Example Country}
  - {id: company:body, type: Company, name: Some Body}
edges:
  - {src: country:xx, predicate: BASED_IN, dst: company:body}
""",
    )
    with pytest.raises(ValueError, match="BASED_IN subject must be"):
        load_anchors(conn, profile, quiet=True)


def test_loading_anchors_twice_is_a_no_op(conn: db.Conn, tmp_path) -> None:
    from venona.services.seed import load_anchors

    profile = _anchor_file(
        tmp_path,
        """
entities:
  - {id: company:parent, type: Company, name: Parent Corp}
  - {id: company:child, type: Company, name: Child Division, part_of: company:parent}
""",
    )
    load_anchors(conn, profile, quiet=True)
    before = conn.execute(
        "SELECT COUNT(*) FROM assertions WHERE run_id='run:anchors-0001'"
    ).fetchone()[0]
    load_anchors(conn, profile, quiet=True)
    after = conn.execute(
        "SELECT COUNT(*) FROM assertions WHERE run_id='run:anchors-0001'"
    ).fetchone()[0]
    assert before == after


def test_redeclaring_an_entity_preserves_its_provenance(conn: db.Conn, tmp_path) -> None:
    """`db.upsert` writes every non-key column from `excluded`, so declaring an
    id that already exists would reset `first_seen` to the loader's FrozenClock
    epoch and drop whatever the pipeline had learned into `props`."""
    from venona.services.seed import load_anchors

    conn.execute(
        "INSERT INTO entities (id, type, name, canonical_key, status, props, "
        "first_seen, last_seen, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "company:known",
            "Company",
            "Known Corp",
            "known corp",
            "active",
            db.jdump({"relevance_note": "learned from the corpus"}),
            "2020-01-01T00:00:00.000Z",
            "2020-01-01T00:00:00.000Z",
            "2020-01-01T00:00:00.000Z",
        ),
    )
    conn.commit()

    profile = _anchor_file(
        tmp_path,
        """
entities:
  - {id: company:known, type: Company, name: Known Corp, props: {anchor: true}}
""",
    )
    load_anchors(conn, profile, quiet=True)

    row = conn.execute(
        "SELECT first_seen, created_at, props FROM entities WHERE id='company:known'"
    ).fetchone()
    assert row["first_seen"] == "2020-01-01T00:00:00.000Z"
    assert row["created_at"] == "2020-01-01T00:00:00.000Z"
    assert db.jload(row["props"], {})["anchor"] is True, "the declared prop lands"


def test_an_anchor_name_can_opt_out_of_matching(conn: db.Conn, tmp_path) -> None:
    """An anchor is structure, not an annotation target.

    The default gives a name of eight characters or more strictness 3 -- global,
    case-insensitive. For "United States" that would win the highlight over
    `U.S. Army` in "the United States Army awarded...", across two thirds of the
    corpus, and it would read as a model error rather than a config one.
    """
    from venona.services.seed import load_anchors

    profile = _anchor_file(
        tmp_path,
        """
entities:
  - {id: country:example, type: Country, name: Example Country, name_strictness: 0}
  - {id: company:example-corp, type: Company, name: Example Corporation}
""",
    )
    load_anchors(conn, profile, quiet=True)

    strictness = {
        r["entity_id"]: r["strictness"]
        for r in conn.execute(
            "SELECT entity_id, strictness FROM aliases WHERE surface IN "
            "('Example Country', 'Example Corporation')"
        )
    }
    assert strictness["country:example"] == 0, "never auto-matched"
    assert strictness["company:example-corp"] == 3, "an ordinary entity keeps the default"


# ── proposals ────────────────────────────────────────────────────────


def _relation(conn: db.Conn, rid: str, src: str, predicate: str, dst: str) -> None:
    db.insert(
        conn,
        "assertions",
        {
            "id": rid,
            "run_id": "run:test",
            "article_id": "doc:test",
            "cluster_id": "cluster:test",
            "kind": "relation",
            "predicate": predicate,
            "subject_id_raw": src,
            "object_id_raw": dst,
            "evidence_quote": "They said so.",
            "confidence": 0.9,
            "method": "extraction",
            "created_at": NOW,
        },
    )


def test_an_entity_whose_only_neighbour_is_blocked_counts_as_isolated(
    conn: db.Conn,
) -> None:
    """ "Isolated" has to mean what the explorer draws, not what SQLite holds.

    An entity can carry three relationships and still float, because every one
    of them points at something uncorroborated and the projector's
    `MATCH (a), (b)` drops an edge with no node at one end.
    """
    from venona.pipeline.anchors import isolated_entities

    entity(conn, "company:visible", "Visible Corp", "active")
    entity(conn, "company:hidden", "Hidden Corp", "provisional")
    _relation(conn, "assert:r1", "company:visible", "PARTNERS_WITH", "company:hidden")
    conn.commit()

    isolated = {r["id"] for r in isolated_entities(conn)}
    assert "company:visible" in isolated, "its only edge cannot project, so it floats"


def test_the_transitive_lift_only_follows_part_of(conn: db.Conn) -> None:
    """Relations are not transitive, and a plausible wrong proposal is worse
    than none because a curator has to stop and think about it.

    An earlier version walked ANY two edges and produced `General Atomics
    PART_OF Northrop Grumman` -- they are competitors -- and `Collaborative
    Combat Aircraft PART_OF RTX`, a program supposedly part of a company, from
    nothing more than a shared neighbour.
    """
    from venona.pipeline.anchors import transitive_proposals

    entity(conn, "company:anchor", "Anchor Corp", "active")
    entity(conn, "company:middle", "Middle Corp", "provisional")

    # Sound: child PART_OF middle PART_OF anchor.
    entity(conn, "company:child", "Child Div", "active")
    _relation(conn, "assert:p1", "company:child", "PART_OF", "company:middle")
    _relation(conn, "assert:p2", "company:middle", "PART_OF", "company:anchor")

    # Unsound: a shared neighbour is not a parent.
    entity(conn, "company:rival", "Rival Corp", "active")
    _relation(conn, "assert:c1", "company:rival", "COMPETES_WITH", "company:middle")
    conn.commit()

    rows = list(
        conn.execute(
            "SELECT id, name, type, mention_count, 1 AS has_assertions FROM entities "
            "WHERE id IN ('company:child', 'company:rival')"
        )
    )
    proposals = transitive_proposals(conn, rows, {"company:anchor"})

    assert [(p.child_id, p.anchor_id) for p in proposals] == [("company:child", "company:anchor")]


def test_the_transitive_lift_requires_the_grandparent_to_be_an_anchor(
    conn: db.Conn,
) -> None:
    """The whole point of the pass is reaching the spine.

    The grandparent query filtered only on `status = 'active'`, so an isolated
    node could be proposed onto a grandparent that is itself floating -- a
    second orphan branch rather than an attachment, and a proposal a curator has
    to stop and think about for no gain. The `anchors` set was consulted only to
    skip when the PARENT was an anchor.
    """
    from venona.pipeline.anchors import transitive_proposals

    entity(conn, "company:floating", "Floating Corp", "active")  # active, but not an anchor
    entity(conn, "company:middle", "Middle Corp", "provisional")
    entity(conn, "company:orphan", "Orphan Div", "active")
    _relation(conn, "assert:o1", "company:orphan", "PART_OF", "company:middle")
    _relation(conn, "assert:o2", "company:middle", "PART_OF", "company:floating")
    conn.commit()

    rows = list(
        conn.execute(
            "SELECT id, name, type, mention_count, 1 AS has_assertions FROM entities "
            "WHERE id = 'company:orphan'"
        )
    )
    assert transitive_proposals(conn, rows, {"company:elsewhere"}) == [], (
        "the grandparent is floating too; attaching to it reaches nothing"
    )

    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM entities")}
    proposals = transitive_proposals(conn, rows, {"company:floating"}, names=names)
    assert [(p.child_id, p.anchor_id) for p in proposals] == [
        ("company:orphan", "company:floating")
    ]

    # The note is the whole of what a curator reads before pressing the key. It
    # used to render as "PART_OF company:middle, which is PART_OF this but
    # uncorroborated" -- raw ids, and "this" naming nothing.
    why = proposals[0].why
    assert "Middle Corp" in why and "Floating Corp" in why
    assert "company:" not in why, "names, not ids"


def test_a_proposal_is_an_open_row_and_never_an_assertion(conn: db.Conn) -> None:
    """The whole safety argument. Nothing reaches the graph without a keystroke."""
    from venona.pipeline.anchors import Proposal, file_proposal

    entity(conn, "company:child", "Child Div", "active")
    entity(conn, "company:parent", "Parent Corp", "active")
    conn.commit()

    before = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert file_proposal(
        conn,
        Proposal("company:child", "company:parent", "PART_OF", "a reason"),
        names={"company:child": "Child Div"},
        now=NOW,
    )
    assert conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0] == before

    row = conn.execute("SELECT * FROM alias_conflicts WHERE kind='anchor'").fetchone()
    assert row["resolved"] == 0
    assert row["proposed_entity_id"] == "company:child"
    assert row["existing_entity_id"] == "company:parent"
    # Read by MergeReview's watcher to orient the direction toggle.
    assert row["suggestion_child"] == "company:child"


def test_an_ill_typed_proposal_is_dropped_not_filed(conn: db.Conn) -> None:
    from venona.pipeline.anchors import Proposal, file_proposal

    entity(conn, "company:body", "Some Body", "active")
    db.upsert(
        conn,
        "entities",
        {
            "id": "country:xx",
            "type": "Country",
            "name": "Example Country",
            "canonical_key": "example country",
            "status": "active",
            "first_seen": NOW,
            "last_seen": NOW,
            "created_at": NOW,
        },
        key=["id"],
    )
    conn.commit()

    # BASED_IN goes Organization -> Country, never the other way.
    assert not file_proposal(
        conn,
        Proposal("country:xx", "company:body", "BASED_IN", "backwards"),
        names={},
        now=NOW,
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM alias_conflicts WHERE kind='anchor'").fetchone()[0] == 0
    )


def test_an_anchor_proposal_survives_the_classifier(conn: db.Conn) -> None:
    """`run_curate` stamps `kind` on every unresolved conflict. Without a guard
    it would reclassify an anchor proposal as a `duplicate` and offer to MERGE
    an entity into its own parent."""
    from venona.pipeline.anchors import Proposal, file_proposal
    from venona.pipeline.curate import _classify_open

    entity(conn, "company:child", "Child Div", "active")
    entity(conn, "company:parent", "Parent Corp", "active")
    conn.commit()
    file_proposal(
        conn,
        Proposal("company:child", "company:parent", "PART_OF", "a reason"),
        names={"company:child": "Child Div"},
        now=NOW,
    )

    _classify_open(conn)

    kind = conn.execute("SELECT kind FROM alias_conflicts WHERE id LIKE 'anchor|%'").fetchone()[
        "kind"
    ]
    assert kind == "anchor"


def test_withdrawing_a_declared_alias_removes_it(conn: db.Conn, tmp_path) -> None:
    """⭐ Loading is upsert-only, so a line taken OUT of the file used to remove
    nothing: the entity went on being called something a person had decided it is
    not.

    Measured in production. A curator ruled a pair `related`, an ADR withdrew the
    two alias lines that contradicted them, and a migration deleted the rows --
    then a stale copy of the document was applied and put both straight back,
    because withdrawal had no effect of its own.
    """
    from venona.services.seed import load_anchors

    declared = """
entities:
  - id: company:parent
    type: Company
    name: Parent Corp
    aliases:
      - {surface: PC, strictness: 2}
      - {surface: Parent Subsidiary Systems, strictness: 3}
"""
    load_anchors(conn, _anchor_file(tmp_path, declared), quiet=True)
    surfaces = {
        r["surface"]
        for r in conn.execute(
            "SELECT surface FROM aliases WHERE entity_id='company:parent' AND added_by='seed'"
        ).fetchall()
    }
    assert "Parent Subsidiary Systems" in surfaces

    withdrawn = """
entities:
  - id: company:parent
    type: Company
    name: Parent Corp
    aliases:
      - {surface: PC, strictness: 2}
"""
    counts = load_anchors(conn, _anchor_file(tmp_path, withdrawn), quiet=True)
    surfaces = {
        r["surface"]
        for r in conn.execute(
            "SELECT surface FROM aliases WHERE entity_id='company:parent' AND added_by='seed'"
        ).fetchall()
    }
    assert "Parent Subsidiary Systems" not in surfaces, "the withdrawn line must not survive"
    assert "PC" in surfaces, "a surface still declared stays"
    assert "Parent Corp" in surfaces, "the entity's own name is always declared"
    assert counts["withdrawn"] == 1, "and the count says so rather than hiding it in the total"


def test_a_withdrawal_touches_only_what_the_file_declared(conn: db.Conn, tmp_path) -> None:
    """The file speaks for its own declarations and for nothing else. An alias
    the corpus discovered, or one a curator attached by hand, is not the anchor
    document's to withdraw."""
    from venona.services.seed import load_anchors

    doc = """
entities:
  - id: company:parent
    type: Company
    name: Parent Corp
"""
    load_anchors(conn, _anchor_file(tmp_path, doc), quiet=True)
    db.upsert(
        conn,
        "aliases",
        {
            "id": "alias:from-the-corpus",
            "entity_id": "company:parent",
            "surface": "ParentCo",
            "normalized": "parentco",
            "strictness": 2,
            "is_acronym": 0,
            "added_by": "extraction",
            "created_at": NOW,
        },
        key=["entity_id", "surface"],
    )
    conn.commit()

    load_anchors(conn, _anchor_file(tmp_path, doc), quiet=True)
    kept = conn.execute(
        "SELECT added_by FROM aliases WHERE entity_id='company:parent' AND surface='ParentCo'"
    ).fetchone()
    assert kept is not None, "an extracted alias is not the document's to remove"
