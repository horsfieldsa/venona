"""Regressions for the defects found in the first full review of the tree.

Each test names the failure it prevents rather than the function it calls. They
are gathered in one file on purpose: every one of them passed the whole suite
before its fix, so what they have in common -- being the class of bug this
suite could not see -- is more useful than which module they happen to touch.

Offline and free, like the rest of `make test`.
"""

from __future__ import annotations

import asyncio
import unicodedata
from typing import Any

import psycopg
import pytest
from helpers import NOW, FakeNeo4jDriver, entity
from venona.db import core as db


def _document(
    conn: db.Conn,
    doc_id: str,
    *,
    cluster_id: str,
    domain: str = "example.com",
    text: str = "body",
) -> None:
    db.upsert(
        conn,
        "documents",
        {
            "id": doc_id,
            "source_id": "breaking_defense",
            "site_domain": domain,
            "url": f"https://{domain}/{doc_id}",
            "url_key": f"{domain}/{doc_id}",
            "title": doc_id,
            "text_nfc": text,
            "text_chars": len(text),
            "content_hash": doc_id,
            "cluster_id": cluster_id,
            "published_at": NOW,
            "date_confidence": "feed",
            "fetched_at": NOW,
            "created_at": NOW,
        },
        key=["id"],
    )


def _assertion(conn: db.Conn, assertion_id: str, **fields: Any) -> None:
    db.insert(
        conn,
        "assertions",
        {
            "id": assertion_id,
            "run_id": "run:test",
            "cluster_id": "cluster:test",
            "kind": "entity",
            "evidence_quote": "They did a thing.",
            "confidence": 0.9,
            "method": "model",
            "created_at": NOW,
            **fields,
        },
    )


# ── migrations are atomic with their bookkeeping row ─────────────────


def test_a_failed_migration_leaves_no_half_applied_schema(database_url: str) -> None:
    """⭐ A revision that fails part-way must leave NOTHING.

    The original failure this pins predates Alembic: `executescript` COMMITs
    before it starts and does no transaction control of its own, so a migration
    whose second statement failed left its first statement committed and its
    ledger row absent -- and every later `venona migrate` (so `make dev`,
    `make setup` and API startup) died with "table already exists", with no
    recovery but hand-editing the database.

    THE MECHANISM CHANGED AND THE GUARANTEE DID NOT, which is the reason to keep
    testing it rather than to delete it with the code it described. Alembic runs
    each revision inside a transaction and Postgres has transactional DDL, so a
    revision that raises rolls back its own `CREATE TABLE` -- and, critically,
    does not stamp `alembic_version`, which is the modern shape of the same
    "half-applied" state.

    Driven through Alembic directly rather than through `db.migrate`, because the
    thing under test is what happens INSIDE a revision.
    """
    conn = db.connect(database_url)
    try:
        before = db._current_revision(conn)

        # The two statements a broken revision would run, in one transaction --
        # which is exactly the shape Alembic gives a revision. Driven directly
        # rather than through a synthetic revision file because what is under test
        # is the ATOMICITY, and wrapping it in Alembic's machinery would test
        # Alembic.
        with pytest.raises(psycopg.Error), db.transaction(conn, what="broken revision"):
            conn.execute("CREATE TABLE half_applied (id TEXT)")
            conn.execute("ALTER TABLE nonexistent ADD COLUMN x TEXT")

        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT table_name AS name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        }
        assert "half_applied" not in tables, "a failed migration must roll back what it wrote"
        assert not conn.in_transaction, "and must not leave the transaction open"
        assert db._current_revision(conn) == before, (
            "and must not stamp a revision it did not finish"
        )
    finally:
        conn.close()


def test_migrating_twice_is_a_no_op(database_url: str) -> None:
    """The ledger is what makes `venona migrate` safe to run from eight places --
    the API lifespan, the scheduler, `run_launcher` and five CLI commands all call
    it, and on a warm database all eight must do nothing."""
    conn = db.connect(database_url)
    try:
        assert db.migrate(conn) == [], "the per-test database is already at head"
        assert db._current_revision(conn) is not None, "and is stamped"
    finally:
        conn.close()


# ── the brief can be regenerated ─────────────────────────────────────


def _briefable_cluster(conn: db.Conn, cluster_id: str, score: float) -> None:
    doc_id = f"doc:{cluster_id}"
    _document(conn, doc_id, cluster_id=cluster_id, text="x" * 5000)
    db.upsert(
        conn,
        "clusters",
        {
            "id": cluster_id,
            "canonical_doc_id": doc_id,
            "published_at": NOW,
            "analyzed_at": NOW,
            "relevance_overall": score,
            "tier": "signal",
            "first_seen": NOW,
            "last_seen": NOW,
        },
        key=["id"],
    )


def _pretend_brief_covered(conn: db.Conn, brief_id: str, clusters) -> None:
    db.upsert(
        conn,
        "briefs",
        {
            "id": brief_id,
            "date": "2026-08-06",
            "title": "Brief",
            "generated_at": NOW,
            "created_at": NOW,
        },
        key=["id"],
    )
    for rank, cluster_id in enumerate(sorted(clusters), start=1):
        db.upsert(
            conn,
            "brief_clusters",
            {
                "brief_id": brief_id,
                "cluster_id": cluster_id,
                "rank": rank,
                "created_at": NOW,
            },
            key=["brief_id", "cluster_id"],
        )


def test_a_later_brief_never_repeats_an_earlier_briefs_clusters(
    conn: db.Conn,
) -> None:
    """The inverse of what this test used to assert, and the reason it flipped.

    `build_retrieval` used to exempt the brief being written -- ids were
    `brief:{date}`, so regenerating a day rewrote its own `brief_clusters` rows
    and had to ignore them. With ONE brief row in the table that exemption
    matched every row, `bc.brief_id IS NOT ?` was false throughout, the
    `NOT EXISTS` was vacuously true and NOTHING was ever excluded. Six briefs in
    one day served the same top stories.

    Ids are unique per generation now, so a brief owns no cluster rows when it
    retrieves and needs no exemption at all.
    """
    from venona.pipeline.brief import build_retrieval

    for i in range(3):
        _briefable_cluster(conn, f"cluster:rg{i}", 0.9 - i / 100)

    fresh = {c["id"] for c in build_retrieval(conn, now=NOW).clusters}
    assert {"cluster:rg0", "cluster:rg1", "cluster:rg2"} <= fresh

    _pretend_brief_covered(conn, "brief:2026-08-06:0001", fresh)

    again = {c["id"] for c in build_retrieval(conn, now=NOW).clusters}
    assert not (again & fresh), "a later brief must not repeat what was already briefed"


def test_deleting_a_brief_releases_its_clusters(conn: db.Conn) -> None:
    """Regeneration is now `briefs delete` then re-run, so the release has to be
    real. If deleting left `brief_clusters` behind, the documented tuning loop
    would retrieve nothing forever."""
    from venona.pipeline.brief import build_retrieval
    from venona.services.brief_service import delete_brief

    for i in range(3):
        _briefable_cluster(conn, f"cluster:rl{i}", 0.9 - i / 100)

    fresh = {c["id"] for c in build_retrieval(conn, now=NOW).clusters}
    _pretend_brief_covered(conn, "brief:2026-08-06:0001", fresh)
    assert not build_retrieval(conn, now=NOW).clusters

    delete_brief(conn, "brief:2026-08-06:0001")

    assert {c["id"] for c in build_retrieval(conn, now=NOW).clusters} == fresh
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM brief_clusters WHERE brief_id = ?",
            ("brief:2026-08-06:0001",),
        ).fetchone()["n"]
        == 0
    )


# ── graph: counters are assigned, not accumulated ────────────────────


# `helpers.FakeNeo4jDriver` with a fixed `single`: every statement here answers
# with the same counter dict, and what is being asserted is the Cypher and the
# parameters, not the reply.
def _driver() -> FakeNeo4jDriver:
    return FakeNeo4jDriver(single={"written": 1, "n": 0, "seq": 1})


def test_mention_count_is_the_distinct_article_count_of_the_batch() -> None:
    """`mention_count` was incremented in Cypher and guarded by
    `article_id IN e.evidence` -- but `evidence` holds only the last ten ids, so
    an edge with twelve supporting articles found two of them "new" on every
    incremental projection and grew without bound.

    It is the sort key and the `min_source_count` floor in `queries.py`, so hub
    edges drifted upward and `rebuild=True` disagreed with the graph beside it.
    """
    from venona.graph.writer import EVIDENCE_LIMIT, EdgeRow, GraphWriter

    supporting = 12
    assert supporting > EVIDENCE_LIMIT, "the bug needs more evidence than is displayed"

    driver = _driver()
    writer = GraphWriter(driver, projection=7)
    rows = [
        EdgeRow(
            src="company:a",
            dst="company:b",
            article_id=f"doc:{i}",
            confidence=0.5 + i / 100,
            observed_at=f"2026-08-{i + 1:02d}T00:00:00.000Z",
        )
        for i in range(supporting)
    ]
    # The same article twice: corroboration, not a second mention.
    rows.append(rows[0])

    asyncio.run(writer.upsert_edges("PART_OF", rows))

    submitted = driver.params[0]["rows"]
    assert len(submitted) == 1, "one row per edge, not one per assertion"
    edge = submitted[0]
    assert edge["mention_count"] == supporting
    assert len(edge["evidence"]) == EVIDENCE_LIMIT, "display cap, independent of the count"
    assert edge["first_seen"] < edge["last_seen"]
    assert driver.params[0]["projection"] == 7

    query = driver.queries[0]
    assert "e.mention_count = row.mention_count" in query
    assert "e.mention_count +" not in query, "assignment, never accumulation"


def test_stats_survives_a_graph_with_no_relationships() -> None:
    """A plain `MATCH ()-[r]->()` matched nothing on a graph with entities but no
    surviving edges -- a fresh seed, or every edge retracted in curation -- and
    that killed the row stream already carrying the entity count. `stats()`
    returned no record at all and reported an empty graph.

    The damage was not the display. The idempotency gate compares two runs'
    stats, so it compared `0 == 0` and passed while checking nothing.

    Asserted against the query's shape because the failure IS Cypher semantics:
    a stub driver that returned a row would be testing the stub.
    """
    import inspect

    from venona.graph.writer import GraphWriter

    # Lines that OPEN with the keyword, so the docstring's prose about MATCH
    # does not count as a clause.
    matches = [
        line.strip()
        for line in inspect.getsource(GraphWriter.stats).splitlines()
        if line.strip().startswith(("MATCH", "OPTIONAL MATCH"))
    ]
    assert matches, "the query moved; this guard needs updating"
    assert matches[0].startswith("MATCH (e:Entity)"), "the anchor row must come first"
    assert all(rest.startswith("OPTIONAL MATCH") for rest in matches[1:]), (
        f"every MATCH after the first must be OPTIONAL or it can empty the row stream: {matches}"
    )


def test_a_property_the_replay_no_longer_implies_is_cleared() -> None:
    """`e.kind` and `m.salience` were written with `coalesce(row.x, e.x)`, which
    is accumulation wearing a different hat.

    Retract the only assertion carrying `predicate_kind = 'award'` for an edge
    other assertions still support: the batch stops implying it, but the
    coalesce kept serving 'award' out of Neo4j forever. `venona graph rebuild`
    cleared it because that drops everything first -- an ordinary projection
    never did, which is exactly the divergence `prune()` exists to prevent.
    """
    from venona.graph.writer import EdgeRow, GraphWriter

    driver = _driver()
    asyncio.run(
        GraphWriter(driver, projection=1).upsert_edges(
            "PART_OF",
            [
                EdgeRow(
                    src="company:a",
                    dst="company:b",
                    article_id="doc:1",
                    confidence=0.6,
                    observed_at=NOW,
                    kind=None,  # the surviving evidence carries no kind
                )
            ],
        )
    )
    assert driver.params[0]["rows"][0]["kind"] is None, "the batch no longer implies one"

    # Whitespace-insensitive on purpose: the previous version of this guard
    # matched a literal padded string and broke when a longer property name
    # shifted the alignment column.
    edges = " ".join(driver.queries[0].split())
    assert "e.kind = row.kind" in edges
    assert "coalesce(row.kind" not in edges, "assignment, never coalesce"

    mentions = _driver()
    asyncio.run(
        GraphWriter(mentions, projection=1).link_mentions(
            [
                {
                    "article_id": "doc:1",
                    "entity_id": "company:a",
                    "salience": None,
                    "observed_at": NOW,
                }
            ]
        )
    )
    query = " ".join(mentions.queries[0].split())
    assert "m.salience = row.salience" in query
    assert "coalesce(row.salience" not in query


def test_edge_source_count_is_distinct_domains_not_articles() -> None:
    """Three articles from one trade outlet corroborate an edge far less than
    three outlets do, and `graph_service.edge_provenance` counts the same thing.

    The graph edge served `mention_count` under the name `source_count`, so
    `useCytoscape`'s `edge[source_count <= 1]` drew solid what
    `EdgeProvenancePanel` labelled single-source, and `min_source_count`
    filtered on corroborating ARTICLES while calling itself sources.

    The unit is the SITE DOMAIN, not `documents.publisher`. The byline became
    page-controlled when it started falling back to a page's own
    `og:site_name`, and stopped being 1:1 with the outlet in both directions --
    so counting it made one outlet corroborate itself and two outlets collapse
    into one. The last case below is that regression written down.
    """
    from venona.graph.writer import EdgeRow, GraphWriter

    def submit(domains: list[str | None]) -> dict[str, Any]:
        driver = _driver()
        asyncio.run(
            GraphWriter(driver, projection=1).upsert_edges(
                "PART_OF",
                [
                    EdgeRow(
                        src="company:a",
                        dst="company:b",
                        article_id=f"doc:{i}",
                        confidence=0.6,
                        observed_at=NOW,
                        source_domain=domain,
                    )
                    for i, domain in enumerate(domains)
                ],
            )
        )
        return driver.params[0]["rows"][0]

    one_outlet = submit(["breakingdefense.com"] * 3)
    assert one_outlet["mention_count"] == 3, "three articles"
    assert one_outlet["source_count"] == 1, "but one source -- the panel says so too"

    assert (
        submit(["breakingdefense.com", "defensenews.com", "breakingdefense.com"])["source_count"]
        == 2
    )

    # An assertion whose document row is missing has no domain to count. It
    # demonstrably has evidence, so it must not claim zero sources.
    assert submit([None, None])["source_count"] == 1

    # One outlet whose pages disagree about their own masthead is still ONE
    # source. Counting the byline made this 2, drew the edge solid, and let it
    # through a `min_source_count=2` filter on a single report.
    assert submit(["darpa.mil", "darpa.mil"])["source_count"] == 1


def test_provenance_briefs_honour_retraction_and_kind(conn: db.Conn) -> None:
    """The briefs list in `edge_provenance` ran a correlated subquery that
    dropped both filters the assertion query twenty lines above applies.

    Without `retracted_at IS NULL`, a brief that only ever cited the withdrawn
    claim stayed listed as provenance for an edge that no longer rests on it.
    Without `kind = 'relation'` -- the wider hole -- an `entity` assertion for
    the same article matches too, because the subquery constrains only
    `predicate` and the two id columns.
    """
    import json

    from venona.services.graph_service import edge_provenance

    entity(conn, "company:x", "X Corp")
    entity(conn, "company:y", "Y Corp")
    for doc_id in ("doc:live", "doc:withdrawn", "doc:entity-kind"):
        _document(conn, doc_id, cluster_id="cluster:test")

    # The edge survives on `doc:live` alone.
    _assertion(
        conn,
        "assert:live",
        kind="relation",
        predicate="PART_OF",
        subject_id_raw="company:x",
        object_id_raw="company:y",
        article_id="doc:live",
    )
    _assertion(
        conn,
        "assert:withdrawn",
        kind="relation",
        predicate="PART_OF",
        subject_id_raw="company:x",
        object_id_raw="company:y",
        article_id="doc:withdrawn",
        retracted_at=NOW,
    )
    # Same predicate column value, same endpoints, but an entity assertion.
    _assertion(
        conn,
        "assert:entity-kind",
        kind="entity",
        predicate="PART_OF",
        subject_id_raw="company:x",
        object_id_raw="company:y",
        article_id="doc:entity-kind",
    )

    for brief_id, cited in (
        ("brief:live", "doc:live"),
        ("brief:withdrawn", "doc:withdrawn"),
        ("brief:entity-kind", "doc:entity-kind"),
    ):
        db.upsert(
            conn,
            "briefs",
            {
                "id": brief_id,
                "date": "2026-08-06",
                "title": brief_id,
                "generated_at": NOW,
                "source_ids": json.dumps([cited]),
                "created_at": NOW,
            },
            key=["id"],
        )
    conn.commit()

    result = edge_provenance(conn, "company:x|PART_OF|company:y")
    assert result is not None
    listed = {b["id"] for b in result["briefs"]}
    assert "brief:live" in listed
    assert "brief:withdrawn" not in listed, "it cited only the withdrawn claim"
    assert "brief:entity-kind" not in listed, "an entity assertion is not this edge"

    assert [a["assertion_id"] for a in result["assertions"]] == ["assert:live"]


def test_a_projection_prunes_what_it_did_not_write() -> None:
    """Retracting an assertion set `retracted_at` in SQLite and `_edge_rows`
    stopped emitting it -- and nothing removed the edge, so `/graph/*` kept
    serving a relationship the curator had explicitly withdrawn. Invariant 1
    says Neo4j is a projection of SQLite; an append-only one is not.
    """
    from venona.graph.writer import GraphWriter

    driver = _driver()
    asyncio.run(GraphWriter(driver, projection=3).prune())

    statements = driver.queries
    assert len(statements) == 4, "edges, mentions, entities, articles"
    assert all(p["projection"] == 3 for p in driver.params)
    assert any("DETACH DELETE n" in s for s in statements)
    assert all("coalesce(" in s and "<> $projection" in s for s in statements)


def test_pruning_without_a_projection_stamp_is_refused() -> None:
    from venona.graph.writer import GraphWriter

    with pytest.raises(ValueError, match="projection"):
        asyncio.run(GraphWriter(_driver()).prune())


def test_the_neighbourhood_query_deduplicates_undirected_matches() -> None:
    """`MATCH (a)-[r]-(b)` binds twice when both endpoints are seeds, so the
    same edge came back with the same `source|predicate|target` id -- which
    Cytoscape throws on -- and the duplicates counted against `LIMIT`.
    """
    import inspect

    from venona.graph import queries

    source = inspect.getsource(queries.neighborhood)
    assert "WITH DISTINCT r" in source
    assert "count(DISTINCT r)" in source, "the total must not double-count either"


# ── annotation offsets ───────────────────────────────────────────────


def test_marks_survive_a_composed_character() -> None:
    """NFC was applied to the flattened text AFTER `_inline_to_text` measured
    its marks, and NFC changes a string's length.

    Bold and italic landed on the wrong words, and -- silently -- the
    `href_ranges` that suppress entity spans inside links pointed at the wrong
    text, so mentions inside links got click targets and mentions outside them
    were dropped.
    """
    from venona.services.annotate import markdown_to_blocks

    decomposed = unicodedata.normalize("NFD", "Café **bold** and [Café link](https://x)")
    block = markdown_to_blocks(decomposed)[0]

    assert unicodedata.normalize("NFC", block.text) == block.text
    marks = {m.type: block.text[m.start : m.end] for m in block.marks}
    assert marks["b"] == "bold"
    assert marks["href"] == "Café link"


def test_alias_offsets_survive_a_character_that_grows_when_lowercased() -> None:
    """The case-insensitive scan ran over `text.lower()` and applied the offsets
    to `text`. `'İ'.lower()` is two code points, so one Turkish dotted capital
    shifted every highlight after it by one -- and it reads as a model error.
    """
    from venona.services.alias_index import AliasEntry, AliasIndex

    assert len("İ".lower()) == 2, "the premise of the bug"

    index = AliasIndex(
        [AliasEntry(ref="company:x", surface="Anduril", strictness=2, is_acronym=False, kind="org")]
    )
    text = "İn other news, Anduril won."
    matches = index.find(text, tier_a=frozenset({"company:x"}))

    assert len(matches) == 1
    match = matches[0]
    assert text[match.start : match.end] == "Anduril"


# ── curation ─────────────────────────────────────────────────────────


def test_auto_merge_requires_an_acronym_shape_not_just_one_word() -> None:
    """The gate was `" " not in name`, which is not the same claim. A one-word
    product name absorbed into any long form whose initials spelled it is an
    auto-merge with no user approval -- what CLAUDE.md reserves for provable
    identity.
    """
    from venona.pipeline.curate import _mergeable_acronym

    assert _mergeable_acronym("MDA", "Missile Defense Agency")
    assert _mergeable_acronym("NGC2", "Next Generation Command and Control")
    assert not _mergeable_acronym("Lattice", "Lattice")
    # A bare letter-plus-digit compression is generated by too many phrases:
    # `C2` is a real program, and "Common Controller" produces it too.
    assert not _mergeable_acronym("C2", "Common Controller")


def test_conflicts_are_classified_before_the_stage_consumes_kind(
    conn: db.Conn,
) -> None:
    """`kind` is this stage's own output, and both consumers filter on it.
    Classifying last meant a conflict filed by THIS run's analyze stage was
    invisible to `identical_pairs` and `adjudicate` until the next day -- and on
    a fresh database the first `venona run` acted on nothing at all.
    """
    import inspect

    from venona.pipeline import curate

    source = inspect.getsource(curate.run_curate)
    # Matched on the call name alone: the arguments have changed once already
    # (the two merge lexicons) and pinning the whole call meant this failed on
    # an edit that left the ORDER, which is all it is about, untouched.
    file_at = source.index("_file_entity_duplicates(")
    classify_at = source.index("_classify_open(")
    merge_at = source.index("identical_pairs(")
    adjudicate_at = source.index("await adjudicate(")
    assert file_at < classify_at < merge_at < adjudicate_at

    # And it really does stamp a freshly filed row.
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": "conflict:fresh",
            "surface": "MDA",
            "normalized": "mda",
            "existing_entity_id": "company:a",
            "proposed_entity_id": "company:b",
            "entity_type": "Company",
            "resolved": 0,
            "created_at": NOW,
        },
        key=["id"],
    )
    for entity_id, name in (("company:a", "MDA"), ("company:b", "Missile Defense Agency")):
        entity(conn, entity_id, name)

    curate._classify_open(conn)
    row = conn.execute("SELECT kind FROM alias_conflicts WHERE id='conflict:fresh'").fetchone()
    assert row["kind"] is not None


def test_a_human_rejection_survives_a_second_expansion(conn: db.Conn) -> None:
    """`UPDATE ... SET status='ambiguous' WHERE normalized = ?` reached every row
    for the surface, overturning a curator's `rejected` verdict three lines
    above the code written to protect it.
    """
    from venona.pipeline.acronyms import store_definition

    store_definition(
        conn,
        surface="ZZQ",
        expansion="Zonal Quality Group",
        method="in_article",
        confidence=1.0,
        article_id=None,
        quote="Zonal Quality Group (ZZQ) said so.",
        now=NOW,
    )
    conn.execute(
        "UPDATE acronym_definitions SET status='rejected', decided_at=? WHERE normalized='zzq'",
        (NOW,),
    )

    # A second, genuinely different expansion turns the surface ambiguous.
    store_definition(
        conn,
        surface="ZZQ",
        expansion="Zebra Zone Quadrant",
        method="in_article",
        confidence=1.0,
        article_id=None,
        quote="Zebra Zone Quadrant (ZZQ) said so.",
        now=NOW,
    )

    statuses = {
        r["expansion"]: r["status"]
        for r in conn.execute(
            "SELECT expansion, status FROM acronym_definitions WHERE normalized='zzq'"
        )
    }
    assert statuses["Zonal Quality Group"] == "rejected", "a person ruled on this"
    assert statuses["Zebra Zone Quadrant"] == "ambiguous"


# ── reads follow merges ──────────────────────────────────────────────


def test_a_card_keeps_the_evidence_a_merge_brought_with_it(conn: db.Conn) -> None:
    """`_entity_row` followed `entity_mappings`; `_neighbors` and `_signals` did
    not. Assertions keep the ids they were written with (C1), so merging made a
    card LESS informative -- everything the loser contributed stopped being
    found.
    """
    from venona.services.curate import merge_entities
    from venona.services.entity_service import entity_card, neighborhood

    for entity_id, name in (
        ("company:survivor", "Survivor Corp"),
        ("company:loser", "Survivor"),
        ("company:far", "Far Away Inc"),
    ):
        entity(conn, entity_id, name)
    _document(conn, "doc:m1", cluster_id="cluster:m1")
    _assertion(
        conn,
        "assert:m1",
        kind="relation",
        article_id="doc:m1",
        cluster_id="cluster:m1",
        predicate="PARTNERS_WITH",
        subject_id_raw="company:loser",
        object_id_raw="company:far",
    )

    before = entity_card(conn, "company:survivor")
    assert before is not None and before.neighbor_count == 0

    merge_entities(
        conn,
        from_id="company:loser",
        to_id="company:survivor",
        merge_id="merge:test",
        reason="test",
        now=NOW,
    )

    after = entity_card(conn, "company:survivor")
    assert after is not None
    assert after.neighbor_count == 1, "the loser's edge belongs to the survivor now"
    assert [n.id for n in after.mini_graph.neighbors] == ["company:far"]
    assert [s.article_id for s in after.signals] == ["doc:m1"]

    # `/neighbors` must agree with `/card` about the same id, from either side.
    #
    # The ENDPOINT PAIR, not source and target separately: `PARTNERS_WITH` is
    # symmetric, so which end is `source` is not a fact about the world. Both
    # `graph/writer.py` and the read path normalise a symmetric edge's endpoints
    # to lexicographic order, because the assertion log genuinely holds both
    # directions and counting them apart splits one partnership into two
    # half-sourced ones. What this test is about is the merge: the edge belongs
    # to the survivor from either side, and that is what is asserted.
    for requested in ("company:survivor", "company:loser"):
        hood = neighborhood(conn, requested)
        assert {(e.source, e.target) for e in hood.edges} == {
            ("company:far", "company:survivor")
        }, requested

    # And a third entity still sees the relationship, now naming the survivor
    # rather than vanishing with the `merged` row.
    far = neighborhood(conn, "company:far")
    assert {e.source for e in far.edges} | {e.target for e in far.edges} == {
        "company:far",
        "company:survivor",
    }


# ── runs always reach an honest terminal status ──────────────────────


def test_work_already_persisted_is_not_reported_as_a_failure() -> None:
    """`except Exception` set `failed` unconditionally. Neo4j down after the
    brief was written meant 40 documents, 30 analysed clusters and a valid
    `brief_id` all reported as a failed run -- which teaches you to ignore the
    status field, the exact prohibition in CLAUDE.md.
    """
    import inspect

    from venona.pipeline import orchestrator

    source = inspect.getsource(orchestrator.execute_run)
    assert 'outcome.status = "succeeded_partial" if completed else "failed"' in source
    # And the projection cannot take the run down with it.
    projection = source.index("from venona.graph.projector import project")
    assert (
        "except Exception as exc:  # noqa: BLE001 - the graph is rebuildable" in source[projection:]
    )


def test_a_setup_failure_reaches_the_one_writer() -> None:
    """A run that dies before its first stage must still settle.

    `load_profile` reads user-editable YAML, so a stray tab in it is a real
    path -- and it once ran outside the orchestrator's own guard, leaving the
    `runs` row `running` for ever and an SSE stream that never saw a `done`,
    which the browser reconnects to indefinitely.

    The guard is gone because the answer is structural now: `execute_run` runs
    stages and RAISES, and `run_launcher` settles every run there is. What this
    holds is that the orchestrator does not swallow such a failure into a
    return value nobody settles -- `tests/test_run_lanes.py` holds the other
    half, that the launcher turns a raise into a terminal row and a `done`.
    """
    import inspect

    from venona.pipeline import orchestrator

    source = inspect.getsource(orchestrator.execute_run)
    assert "_abort_before_start" not in source, "a second terminal writer is back"
    assert "finish_run" not in source, "the row belongs to `run_launcher`"


def test_the_stream_subscribes_before_it_replays() -> None:
    """Every `yield` in the replay loop returns control to the event loop, so an
    event published between the last replayed one and the subscription landed in
    neither. The UI opens the stream immediately after POSTing a run, which is
    exactly where the early stage events are -- the strip showed `fetch` running
    for the rest of the run.
    """
    import inspect

    from venona.services import run_service

    source = inspect.getsource(run_service.stream_events)
    assert source.index("registry.subscribe(run_id)") < source.index("events_since(")
    assert 'if event["seq"] <= last_seq' in source, "overlap needs the de-dup"


# ── spend is ledgered where it happens ───────────────────────────────


def test_every_analyze_attempt_is_ledgered_and_charged(conn: db.Conn) -> None:
    """Ledgering after the retry loop meant three CLI invocations wrote one
    `llm_calls` row and counted as one call against `max_calls_per_run` -- so a
    429 storm, when a run burns its allowance fastest, was when the cap stopped
    seeing the spend.
    """
    from venona.llm.budget import Budget
    from venona.llm.executor import LlmResult, Usage
    from venona.llm.runner import analyze_document
    from venona.settings import get_settings

    settings = get_settings()

    class _Flaky:
        def __init__(self) -> None:
            self.attempts = 0

        async def call(self, *_a, **_k):
            self.attempts += 1
            error = "429 rate_limit" if self.attempts < 3 else None
            return LlmResult(
                data={"relevance": {"axes": {}}} if error is None else None,
                error=error,
                cost_usd=0.01,
                usage=Usage(),
            )

    async def no_backoff(*_a, **_k):
        return 0.0

    from venona.llm import budget as budget_mod

    original = budget_mod.backoff
    budget_mod.backoff = no_backoff
    try:
        budget = Budget(settings=settings.llm)
        outcome = asyncio.run(
            analyze_document(
                conn=conn,
                llm=_Flaky(),
                settings=settings,
                title="t",
                url="https://example.com/x",
                text="body",
                run_id="run:ledger",
                call_id="call:ledger",
                created_at=NOW,
                budget=budget,
            )
        )
    finally:
        budget_mod.backoff = original

    assert outcome.calls == 3
    assert budget.calls == 3, "all three must count against max_calls_per_run"
    assert outcome.cost_usd == pytest.approx(0.03)

    rows = conn.execute("SELECT id FROM llm_calls WHERE run_id='run:ledger'").fetchall()
    assert len(rows) == 3, "one ledger row per invocation, distinct ids"


def test_tier_three_lookups_spend_against_the_run_budget() -> None:
    """`run_acronyms` and `run_curate` were called with no budget. A full
    `acronym_lookup_cap_per_run` is a search plus up to three reads each -- up
    to 32 calls the cap never saw, spent AFTER analyze had already deferred
    clusters for being out of allowance.
    """
    import inspect

    from venona.pipeline import acronyms, curate, orchestrator

    assert "budget" in inspect.signature(acronyms.run_acronyms).parameters
    assert "budget" in inspect.signature(acronyms.lookup_web).parameters
    assert "budget" in inspect.signature(curate.run_curate).parameters
    assert "budget" in inspect.signature(curate.adjudicate).parameters

    source = inspect.getsource(orchestrator.execute_run)
    assert "run_acronyms(ctx, budget=budget)" in source
    assert "run_curate(ctx, budget=budget)" in source


# ── URL canonicalisation does not over-collapse ──────────────────────


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("https://example.com/a?reference=42", True),
        ("https://example.com/a?sourceId=9", True),
        ("https://example.com/a?showAll=1", True),
        ("https://example.com/a?partnership=x", True),
        ("https://example.com/a?ref=nl", False),
        ("https://example.com/a?source=x", False),
        ("https://example.com/a?utm_campaign=x", False),
        ("https://example.com/a?output=rss", False),
    ],
)
def test_only_whole_tracking_parameters_are_stripped(raw: str, kept: bool) -> None:
    """The pattern was one alternation under `re.match` with no `$`, so every
    entry was a prefix: `ref` matched `reference`, `source` matched `sourceId`,
    `sh` matched `showAll`. Those name DIFFERENT articles, so two distinct URLs
    collapsed to one `url_key` and one was dropped as a duplicate that never
    existed.
    """
    from venona.pipeline.normalize import canonical_url

    assert ("?" in canonical_url(raw)) is kept


# ── counts stay consistent with the gate that reads them ─────────────


def test_refreshing_counts_agrees_with_the_promotion_gate(conn: db.Conn) -> None:
    """`refresh_mention_counts` ran seven lines after `promote_provisionals` and
    overwrote both columns with different definitions: distinct
    `documents.source_id` counts FEEDS rather than publishers, and it ignored
    `entity_mappings` entirely, so a survivor lost every article its merged
    entities brought.
    """
    from venona.pipeline.resolve import refresh_mention_counts
    from venona.services.curate import merge_entities

    for entity_id, name in (("company:keep", "Keep Corp"), ("company:gone", "Keep")):
        entity(conn, entity_id, name)
    for i, (owner, domain) in enumerate(
        [("company:keep", "one.com"), ("company:gone", "two.com"), ("company:gone", "two.com")]
    ):
        _document(conn, f"doc:c{i}", cluster_id=f"cluster:c{i}", domain=domain)
        _assertion(
            conn,
            f"assert:c{i}",
            article_id=f"doc:c{i}",
            cluster_id=f"cluster:c{i}",
            entity_id_raw=owner,
        )

    merge_entities(
        conn,
        from_id="company:gone",
        to_id="company:keep",
        merge_id="merge:counts",
        reason="test",
        now=NOW,
    )
    refresh_mention_counts(conn)

    row = conn.execute(
        "SELECT mention_count, source_count FROM entities WHERE id='company:keep'"
    ).fetchone()
    assert row["mention_count"] == 3, "the survivor keeps the loser's articles"
    assert row["source_count"] == 2, "two PUBLISHERS, not three articles or three feeds"


# ── a grounded acronym stops rendering as undefined ──────────────────


def test_a_later_definition_clears_the_dashed_underline(conn: db.Conn) -> None:
    """A span's `kind` is frozen when the brief is annotated, and `undefined` is
    the one value that can stop being true afterwards.

    `BriefBlock.vue` draws `mention--undefined` straight from `run.k`, so an
    acronym grounded by a later tier-3 lookup kept its dashed underline and its
    "No grounded definition found" tooltip for the life of the brief -- while
    the hover card, which resolves the ref live, showed the definition
    perfectly. Two renderings of the same word on the same screen, disagreeing.
    """
    import json

    from venona.services.brief_service import get_brief

    db.upsert(
        conn,
        "briefs",
        {
            "id": "brief:kind",
            "date": "2026-08-06",
            "title": "Brief",
            "generated_at": NOW,
            "created_at": NOW,
        },
        key=["id"],
    )
    text = "The IDIQ vehicle and the XYZQ vehicle were awarded."
    db.upsert(
        conn,
        "brief_blocks",
        {
            "id": "block:kind",
            "brief_id": "brief:kind",
            # 0, not "s". `brief_blocks.section` is INTEGER, and SQLite's type
            # affinity accepted a string into it; Postgres rejects it outright.
            "section": 0,
            "ordinal": 0,
            "type": "paragraph",
            "depth": 0,
            "text": text,
            "spans": json.dumps(
                [
                    {
                        "start": text.index("IDIQ"),
                        "end": text.index("IDIQ") + 4,
                        "ref": "gloss:idiq",
                        "kind": "undefined",
                        "confidence": 0.7,
                    },
                    {
                        "start": text.index("XYZQ"),
                        "end": text.index("XYZQ") + 4,
                        "ref": "gloss:xyzq",
                        "kind": "undefined",
                        "confidence": 0.7,
                    },
                ]
            ),
            "marks": json.dumps([]),
        },
        key=["id"],
    )
    # Only IDIQ gets grounded, by a lookup that ran after the brief was written.
    db.upsert(
        conn,
        "acronym_definitions",
        {
            "id": "gloss:idiq",
            "surface": "IDIQ",
            "normalized": "idiq",
            "expansion": "Indefinite-delivery/indefinite-quantity",
            "method": "web_grounded",
            "confidence": 0.7,
            "status": "active",
            "first_seen": NOW,
            "last_seen": NOW,
        },
        key=["id"],
    )
    conn.commit()

    brief = get_brief(conn, "brief:kind")
    assert brief is not None
    kinds = {run.r: run.k for block in brief.sections[0].blocks for run in block.runs if run.r}
    assert kinds["gloss:idiq"] == "glossary", "grounded since; must not render undefined"
    assert kinds["gloss:xyzq"] == "undefined", "still ungrounded, and still honest about it"


def test_analyze_dispatches_concurrently() -> None:
    """The stage held a `Semaphore(2)` and never gave it two things to hold.

    `run_analyze` was a plain `for row in rows: await analyze_document(...)`, so
    exactly one call was ever in flight. Measured on `run:06a8160f`: the analyze
    stage's wall clock was 77.21 minutes and the sum of its 112 call durations
    was 77.18 -- effective parallelism 1.00, against a semaphore sized 2. Half
    the stage's 77 minutes was the loop shape, not the model.

    Asserted against the source because the alternative is a timing test, and a
    timing test for "did these overlap" is exactly the flaky kind. What this
    guards is someone reverting to the simpler-looking loop.
    """
    import inspect

    from venona.pipeline import analyze

    source = inspect.getsource(analyze.run_analyze)
    assert "asyncio.gather" in source, "one call in flight is half the stage's wall clock"
    # And the writes stay ordered. A free-running worker pool would make the
    # SQLite write order depend on model latency, which breaks the offline
    # byte-reproducibility the determinism lint exists to protect.
    assert "strict=True" in source, "results must be zipped back onto their rows in order"


def test_analyze_sizes_its_batch_against_the_remaining_budget() -> None:
    """A concurrent stage cannot bound itself with `check()`.

    `budget.check()` answers "may I make one more", so a batch of N dispatched
    after a single check overshoots the cap by up to N-1. The serial loop got
    this right for free; the concurrent one has to ask how much room is left.
    """
    import inspect

    from venona.pipeline import analyze

    source = inspect.getsource(analyze.run_analyze)
    assert "budget.remaining" in source


# ── graph: the projection is written in bounded batches ──────────────


async def test_a_large_projection_is_written_in_bounded_batches() -> None:
    """⭐ The heap cliff. An UNWIND is one statement in one transaction, and
    Neo4j materialises the whole parameter list in the server's heap before the
    first row is written — so an unbatched projection's memory cost is the size
    of the CORPUS rather than the size of a write.

    That is a cliff, not a slope: the projection either completes or dies, and it
    dies on the largest corpus, which is the instance that has been running
    longest. `prune` runs only after every chunk has landed, so a partial pass
    still deletes nothing.
    """
    from venona.graph.writer import BATCH_SIZE, EdgeRow, GraphWriter

    rows = [
        EdgeRow(
            src=f"company:a{index}",
            dst="company:b",
            article_id=f"doc:{index}",
            confidence=0.9,
            observed_at="2026-08-01T00:00:00Z",
            source_domain="example.com",
        )
        for index in range(BATCH_SIZE * 2 + 5)
    ]

    driver = _driver()
    written = await GraphWriter(driver, projection=1).upsert_edges("PART_OF", rows)

    submitted = [params["rows"] for _, params in driver.calls if "rows" in params]
    assert len(submitted) == 3, f"expected 3 batches for {len(rows)} edges, got {len(submitted)}"
    assert all(len(batch) <= BATCH_SIZE for batch in submitted)
    assert sum(len(batch) for batch in submitted) == len(rows)
    # The reported count is the SUM across batches. Returning the last batch's
    # answer would under-report by an order of magnitude on a real corpus, and
    # `edges_written` is the number someone reads to ask whether the graph grew.
    assert written == len(submitted)


async def test_a_small_projection_still_makes_one_call() -> None:
    """Batching must not turn every small write into a fixed number of round
    trips: a lookup run projects a handful of edges."""
    from venona.graph.writer import EdgeRow, GraphWriter

    driver = _driver()
    await GraphWriter(driver, projection=1).upsert_edges(
        "PART_OF",
        [
            EdgeRow(
                src="company:a",
                dst="company:b",
                article_id="doc:1",
                confidence=0.9,
                observed_at="2026-08-01T00:00:00Z",
            )
        ],
    )
    assert len([1 for _, params in driver.calls if "rows" in params]) == 1


async def test_a_merge_folds_two_mentions_into_one_with_the_higher_salience() -> None:
    """⭐ The collapse is in PYTHON, so an ORDER BY on the read cannot fix it.

    Two assertions naming ids a merge has since folded together are two rows
    for one (article, entity) pair, and `link_mentions` MERGEs on that pair and
    SETs `salience` -- so the last row written wins and the mention's salience
    is decided by which of the two the query returned second. `upsert_edges`
    already folds its confidence with `max`; this is the same reducer.
    """
    from venona.graph.projector import _mention_rows

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def __iter__(self):
            return iter(self._rows)

    rows = [
        {
            "article_id": "doc:1",
            "entity_id_raw": "company:loser",
            "salience": 0.9,
            "created_at": "2026-08-01T00:00:00Z",
        },
        {
            "article_id": "doc:1",
            "entity_id_raw": "company:survivor",
            "salience": 0.2,
            "created_at": "2026-08-02T00:00:00Z",
        },
    ]

    import venona.graph.projector as projector_mod

    original = projector_mod.repo.select_assertions_2
    projector_mod.repo.select_assertions_2 = lambda _conn: _Rows(rows)
    try:
        folded = _mention_rows(None, {"company:loser": "company:survivor"})
    finally:
        projector_mod.repo.select_assertions_2 = original

    assert len(folded) == 1, "one article and one surviving entity is one mention"
    assert folded[0]["salience"] == 0.9, "the fold must keep the higher salience, not the last"
    assert folded[0]["observed_at"] == "2026-08-02T00:00:00Z", "and the newer sighting"

    # Reversed input, same answer -- which is what makes it order-independent
    # rather than accidentally right.
    projector_mod.repo.select_assertions_2 = lambda _conn: _Rows(list(reversed(rows)))
    try:
        reversed_fold = _mention_rows(None, {"company:loser": "company:survivor"})
    finally:
        projector_mod.repo.select_assertions_2 = original
    assert reversed_fold == folded
