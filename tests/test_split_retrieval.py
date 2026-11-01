"""What a split entity does to a reader, and what a merge is supposed to undo.

These assert at the level someone experiences, not the unit level. Every one of
them was RED before the read path was fixed, and each names the production
symptom it reproduces: a corroborated fact reading as uncorroborated.

Source counts are per id, so splitting an entity halves its evidence. That is
the whole reason the merge work matters, and it is also why merging has to
CONSOLIDATE evidence rather than discard the loser's -- which it did not.
"""

from __future__ import annotations

import helpers
import pytest
from helpers import NOW, document, entity, relation
from venona.db import core as db
from venona.db.models import normalize
from venona.services import seed
from venona.services.curate import merge_entities
from venona.services.entity_service import neighborhood
from venona.services.graph_service import edge_provenance

NGC2 = "prog:next-generation-command-and-control"
EDGE = f"company:palantir-technologies|COMPETES_FOR|{NGC2}"


@pytest.fixture
def corpus(make_database):
    """An EMPTY database, not the seeded template.

    These count publishers per edge, and the seeded corpus already holds
    `company:palantir-technologies` and NGC2 with assertions of their own. A
    count that silently includes the fixture corpus proves nothing about the
    code -- it measures the fixture.
    """
    connection = db.connect(make_database(seeded=False))
    yield connection
    connection.close()


def _split_corpus(conn: db.Conn) -> None:
    """One fact, two publishers, and a subject the resolver split in two."""
    entity(conn, "company:palantir", "Palantir")
    entity(conn, "company:palantir-technologies", "Palantir Technologies")
    entity(conn, NGC2, "Next Generation Command and Control")
    document(conn, "doc:scoop", domain="defensescoop.test")
    document(conn, "doc:breaking", domain="breakingdefense.test")
    relation(conn, "a:scoop", "company:palantir", "COMPETES_FOR", NGC2, doc="doc:scoop")
    relation(
        conn,
        "a:breaking",
        "company:palantir-technologies",
        "COMPETES_FOR",
        NGC2,
        doc="doc:breaking",
    )
    conn.commit()


def test_a_split_subject_makes_a_corroborated_fact_read_single_source(
    corpus: db.Conn,
) -> None:
    """The production symptom, stated as an assertion.

    Two independent outlets report the same relationship. Because the subject is
    two ids, each edge carries one of them, and both read SINGLE-SOURCE. That is
    the CORRECT answer for the data as it stands -- the defect is the split, not
    the counter -- and saying so here is what stops someone "fixing" this by
    loosening the count.
    """
    conn = corpus
    _split_corpus(conn)

    for subject in ("company:palantir", "company:palantir-technologies"):
        payload = edge_provenance(conn, f"{subject}|COMPETES_FOR|{NGC2}")
        assert payload["source_count"] == 1
        assert payload["single_source"] is True


def test_merging_the_split_consolidates_the_evidence(corpus: db.Conn) -> None:
    """THE BUG THIS SUITE EXISTS FOR.

    `edge_provenance` matched `subject_id_raw` against the edge_id's endpoints
    literally, with no `merge_closure`. Assertions keep the ids they were written
    with (invariant 1), so the survivor's edge kept only the survivor's own
    publisher and the loser's evidence vanished. The split made the fact read
    single-source and the merge did not cure it -- it made the loser's article
    unreachable instead.
    """
    conn = corpus
    _split_corpus(conn)
    merge_entities(
        conn,
        from_id="company:palantir",
        to_id="company:palantir-technologies",
        merge_id="merge:test",
        reason="auto: corporate form",
        now=NOW,
    )
    conn.commit()

    payload = edge_provenance(conn, EDGE)
    assert payload["source_count"] == 2, (
        "two independent domains assert this: a merge must consolidate the "
        "evidence across the closure, not discard the loser's"
    )
    assert payload["single_source"] is False
    assert {a["article_id"] for a in payload["assertions"]} == {"doc:scoop", "doc:breaking"}


def test_relations_and_evidence_report_the_same_strength(corpus: db.Conn) -> None:
    """`graph/writer.py` and `graph/queries.py` both carry a written promise that
    these two count the same thing, and name the failure if they diverge: "the
    canvas draws solid what this panel calls single-source".

    They diverged. `neighborhood` counted distinct ARTICLES and `edge_provenance`
    counted distinct DOMAINS, so two articles from one outlet printed `2 sources`
    in venona_relations and `SINGLE-SOURCE -- one domain` in venona_evidence, for
    the same edge, in the same agent turn.
    """
    conn = corpus
    entity(conn, "company:anduril-industries", "Anduril Industries")
    entity(conn, "plat:yfq-44a", "YFQ-44A")
    # Two articles, ONE publisher -- the case that discriminates the two counts.
    document(conn, "doc:asf-1", domain="airandspaceforces.test")
    document(conn, "doc:asf-2", domain="airandspaceforces.test")
    for index, doc in enumerate(("doc:asf-1", "doc:asf-2")):
        relation(
            conn,
            f"a:asf-{index}",
            "company:anduril-industries",
            "PRODUCES",
            "plat:yfq-44a",
            doc=doc,
        )
    conn.commit()

    hood = neighborhood(conn, "company:anduril-industries")
    edge = next(e for e in hood.edges if e.target == "plat:yfq-44a")
    payload = edge_provenance(conn, edge.id)

    assert edge.source_count == payload["source_count"], (
        "one tool calling this edge corroborated while the other calls it "
        "single-source is the divergence both modules promise not to have"
    )
    assert edge.source_count == 1, "two articles from one outlet is one publisher"


def test_the_footer_never_counts_edges_no_limit_can_reveal(corpus: db.Conn) -> None:
    """`total` was frozen before the endpoint filter, so `venona_relations` said
    "showing 3 of 5" and no `limit` value produced the other two: an endpoint
    held back for being provisional is absent at every page size. The footer told
    the reader to raise a limit that changed nothing."""
    conn = corpus
    entity(conn, "company:anduril-industries", "Anduril Industries")
    entity(conn, "plat:yfq-44a", "YFQ-44A")
    entity(conn, "plat:secret-thing", "Secret Thing", status="provisional")
    document(conn, "doc:one", domain="one.test")
    relation(conn, "a:1", "company:anduril-industries", "PRODUCES", "plat:yfq-44a", doc="doc:one")
    relation(
        conn,
        "a:2",
        "company:anduril-industries",
        "PRODUCES",
        "plat:secret-thing",
        doc="doc:one",
    )
    conn.commit()

    hood = neighborhood(conn, "company:anduril-industries", limit=50)
    assert hood.total == len(hood.edges), (
        "the footer promises what raising `limit` would show; a provisional "
        "endpoint is unreachable at every limit and must not be counted"
    )
    assert {e.target for e in hood.edges} == {"plat:yfq-44a"}


@pytest.mark.parametrize("asked_as", ["company:palantir", "company:palantir-technologies"])
def test_a_merged_id_answers_exactly_as_its_survivor_does(corpus: db.Conn, asked_as: str) -> None:
    """A merged id is a tombstone with a signpost, and every reader has to follow
    it. `_entity_row` resolves; the readers that follow it did not, so a merge
    made an entity LESS findable than it had been."""
    conn = corpus
    _split_corpus(conn)
    merge_entities(
        conn,
        from_id="company:palantir",
        to_id="company:palantir-technologies",
        merge_id="merge:test",
        reason="auto: corporate form",
        now=NOW,
    )
    conn.commit()

    hood = neighborhood(conn, asked_as)
    edges = [e for e in hood.edges if e.target == NGC2]
    assert len(edges) == 1, "the two pre-merge ids now name one entity and one edge"
    assert edges[0].source == "company:palantir-technologies"
    assert edges[0].source_count == 2


def test_the_alias_index_points_both_surfaces_at_the_survivor(corpus: db.Conn) -> None:
    """Aliases move with a merge, so both spellings keep annotating -- at the
    survivor. A merge that dropped the loser's surfaces would silently stop
    matching prose that had been matching the day before."""
    from venona.services.alias_index import build_index

    conn = corpus
    _split_corpus(conn)
    for entity_id, surface in (
        ("company:palantir", "Palantir"),
        ("company:palantir-technologies", "Palantir Technologies"),
    ):
        db.upsert(
            conn,
            "aliases",
            {
                "id": f"{entity_id}|{surface}",
                "entity_id": entity_id,
                "surface": surface,
                "normalized": surface.lower(),
                "strictness": 2,
                "is_acronym": 0,
                "added_by": "seed",
                "created_at": NOW,
            },
            key=["id"],
        )
    conn.commit()
    merge_entities(
        conn,
        from_id="company:palantir",
        to_id="company:palantir-technologies",
        merge_id="merge:test",
        reason="auto: corporate form",
        now=NOW,
    )
    conn.commit()

    matches = build_index(conn).find("Palantir Technologies bid, and Palantir won.")
    refs = {m.ref for m in matches}
    assert refs == {"company:palantir-technologies"}, (
        "both spellings must resolve to the survivor and neither to the tombstone"
    )


@pytest.mark.parametrize("asked_as", ["U.S. Navy", "US Navy", "us navy", "United States Navy"])
def test_search_finds_an_entity_by_a_declared_alias_however_it_is_punctuated(
    corpus: db.Conn, asked_as: str
) -> None:
    """The reader types the name the way a publication prints it.

    `aliases.normalized` has had its punctuation collapsed, so a search that
    compares the raw query against it answers "venona holds nothing matching
    that" for `U.S. Navy` while holding an alias that says exactly that -- and
    the tool that says it goes on to tell the reader the name has not been
    written about, which is a claim about the world drawn from a punctuation
    mismatch.
    """
    from venona.services.entity_service import search

    conn = corpus
    entity(conn, "unit:u-s-navy", "United States Navy")
    for surface in ("United States Navy", "U.S. Navy", "US Navy"):
        db.upsert(
            conn,
            "aliases",
            {
                "id": f"unit:u-s-navy|{surface}",
                "entity_id": "unit:u-s-navy",
                "surface": surface,
                "normalized": normalize(surface),
                "strictness": 3,
                "is_acronym": 0,
                "added_by": "seed",
                "created_at": NOW,
            },
            key=["id"],
        )
    conn.commit()

    hits = search(conn, asked_as, limit=5)
    assert [row["id"] for row in hits] == ["unit:u-s-navy"], (
        f"{asked_as!r} is a surface this entity declares; search has to reach it"
    )


def test_search_does_not_answer_a_query_that_normalises_to_nothing(corpus: db.Conn) -> None:
    """`?` normalises to the empty string, and an empty pattern would match
    every alias in the corpus -- a punctuation key returning the whole graph."""
    from venona.services.entity_service import search

    conn = corpus
    entity(conn, "unit:u-s-navy", "United States Navy")
    db.upsert(
        conn,
        "aliases",
        {
            "id": "unit:u-s-navy|U.S. Navy",
            "entity_id": "unit:u-s-navy",
            "surface": "U.S. Navy",
            "normalized": normalize("U.S. Navy"),
            "strictness": 3,
            "is_acronym": 0,
            "added_by": "seed",
            "created_at": NOW,
        },
        key=["id"],
    )
    conn.commit()

    assert search(conn, "?", limit=5) == []


def test_helpers_document_carries_the_domain_the_counts_rest_on() -> None:
    """`document()` exists because three modules hand-rolled it, each
    rediscovering the NOT NULL columns -- and the one column that matters for
    every assertion above is `site_domain`."""
    assert "site_domain" in helpers.document.__doc__


def test_a_declaration_does_not_revive_the_entity_it_lost_a_merge_to(
    corpus: db.Conn, tmp_path
) -> None:
    """A split that heals itself and then comes back on the next Apply.

    `profile/anchors.yaml` and `fixtures/seed/entities.yaml` are re-read every
    time an operator presses Apply, and the loader upserts every column it
    knows. It does not know `entity_mappings`, so writing the declared `active`
    over a tombstone restored the loser while the merge stayed in force: two
    live ids, one mapping folding them together, and the two read paths
    disagreeing in front of a reader from then on.

    Found on the live corpus as `govt:space-development-agency` and
    `tech:modular-open-systems-approach`, both `active`, both merged away.
    """
    conn = corpus
    entity(conn, "govt:agency", "Agency")
    entity(conn, "govt:agency-glossed", "Agency (AGY)")
    conn.commit()
    merge_entities(
        conn,
        from_id="govt:agency-glossed",
        to_id="govt:agency",
        merge_id="merge:test",
        reason="auto: same name",
        now=NOW,
    )
    conn.commit()

    doc = {
        "entities": [
            {"id": "govt:agency", "type": "GovernmentBody", "name": "Agency"},
            {"id": "govt:agency-glossed", "type": "GovernmentBody", "name": "Agency (AGY)"},
        ]
    }
    seed._load_entities(conn, doc, now=NOW)
    conn.commit()

    statuses = {
        row["id"]: row["status"]
        for row in conn.execute(
            "SELECT id, status FROM entities WHERE id IN ('govt:agency', 'govt:agency-glossed')"
        ).fetchall()
    }
    assert statuses["govt:agency-glossed"] == "merged", (
        "a declaration says what a thing IS, never that it survived a merge -- "
        "reviving the loser leaves two live ids under one active mapping"
    )
    assert statuses["govt:agency"] == "active"


def test_a_split_the_queue_already_ruled_on_can_be_asked_again(corpus: db.Conn) -> None:
    """The state nothing could see: two live entities and a settled `merged` row.

    `_file_entity_duplicates` will not file a pair that already has a row, which
    is right for `kept_apart` and wrong for `merged`. `merged` is not a standing
    verdict, it is a claim that the two ids ARE one entity, and once they are
    both live again the claim is simply false. Left alone, the pair is unaskable
    for good: it shows in `graph_health` and reaches no queue and no curator.
    """
    from venona.pipeline.curate import _file_entity_duplicates

    conn = corpus
    entity(conn, "govt:agency", "Agency")
    entity(conn, "govt:agency-glossed", "Agency (AGY)")
    conn.commit()
    merge_entities(
        conn,
        from_id="govt:agency-glossed",
        to_id="govt:agency",
        merge_id="merge:test",
        reason="auto: same name",
        now=NOW,
    )
    conn.commit()

    # What the loader used to do, reduced to the one write that matters.
    conn.execute("UPDATE entities SET status = 'active' WHERE id = 'govt:agency-glossed'")
    conn.commit()

    filed = _file_entity_duplicates(conn, corporate_form=set(), generic_tails=set(), now=NOW)
    conn.commit()
    assert filed == 1, "a merged verdict that no longer holds must not silence the pair"

    open_rows = conn.execute("SELECT id FROM alias_conflicts WHERE resolved = 0").fetchall()
    assert open_rows, "the pair is back in front of a curator"


def test_a_kept_apart_pair_is_never_asked_twice(corpus: db.Conn) -> None:
    """The other half, and the one that must not regress.

    A human who ruled two entities apart has answered permanently. Re-filing it
    nightly would make the queue argue with them.
    """
    from venona.pipeline.curate import _file_entity_duplicates
    from venona.services.curate import dismiss_conflict

    conn = corpus
    entity(conn, "govt:agency", "Agency")
    entity(conn, "govt:agency-glossed", "Agency (AGY)")
    conn.commit()

    assert _file_entity_duplicates(conn, corporate_form=set(), generic_tails=set(), now=NOW) == 1
    conn.commit()
    filed = conn.execute("SELECT id FROM alias_conflicts").fetchone()["id"]
    dismiss_conflict(conn, filed, now=NOW)
    conn.commit()

    assert _file_entity_duplicates(conn, corporate_form=set(), generic_tails=set(), now=NOW) == 0
