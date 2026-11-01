"""Graph query surfaces: edge ids, provenance, and readable path chains.

The Cypher traversals themselves need a live Neo4j and are covered by the
acceptance script; what is tested here is everything that decides whether the
graph is *trustworthy* — can you get from an edge on the canvas back to the
sentence that asserted it.
"""

from __future__ import annotations

import pytest
from helpers import FakeNeo4jDriver
from venona.db import core as db
from venona.graph import queries
from venona.services.graph_service import describe_path, edge_provenance, parse_edge_id
from venona.services.seed import DECLARED_ARTICLE_ID

# ── edge ids ─────────────────────────────────────────────────────────


def test_edge_id_roundtrip() -> None:
    """Deterministic, so the frontend can address an edge without a round trip."""
    assert parse_edge_id("govt:peo-c3n|PART_OF|unit:u-s-army") == (
        "govt:peo-c3n",
        "PART_OF",
        "unit:u-s-army",
    )


@pytest.mark.negative
def test_edge_id_rejects_unknown_predicates() -> None:
    """An edge id is caller-supplied. A predicate outside the ontology must not
    reach a query."""
    assert parse_edge_id("a|NOT_A_PREDICATE|b") is None
    assert parse_edge_id("garbage") is None
    assert parse_edge_id("a|PART_OF|b|extra") is None


# ── provenance ───────────────────────────────────────────────────────


def test_provenance_returns_the_asserting_sentence(conn: db.Conn) -> None:
    """The reason to trust an edge is being able to read what asserted it."""
    result = edge_provenance(conn, "govt:peo-c3n|PART_OF|unit:u-s-army")
    assert result is not None
    assert result["source"]["id"] == "govt:peo-c3n"
    assert result["target"]["id"] == "unit:u-s-army"
    assert result["assertions"], "an edge with no evidence should not exist"
    assert result["assertions"][0]["quote"]


def test_provenance_is_direction_agnostic(conn: db.Conn) -> None:
    """Symmetric edges are stored once in lexicographic order, so a click on
    either endpoint must find the same assertions."""
    forward = edge_provenance(conn, "govt:peo-c3n|PART_OF|unit:u-s-army")
    backward = edge_provenance(conn, "unit:u-s-army|PART_OF|govt:peo-c3n")
    assert forward and backward
    assert forward["assertion_count"] == backward["assertion_count"]


def test_single_source_edges_are_flagged(conn: db.Conn) -> None:
    """The canvas draws these dashed. The panel must agree — epistemic strength
    is shown the same way in both places."""
    result = edge_provenance(conn, "company:anduril-industries|PRODUCES|plat:lattice")
    assert result is not None
    assert not result["declared"], "this edge cites an article; pick another if that changes"
    assert result["single_source"] == (result["source_count"] <= 1)


def test_the_projection_carries_names_seen_once_and_not_dormant_ones(conn: db.Conn) -> None:
    """ADR 0247. The canvas cannot draw hollow what was never projected.

    Asserted on the repository rather than through Neo4j because this is the
    decision: `select_entities_for_projection` is the one place the statuses
    that may be DRAWN are chosen, and it is one clause away from the statuses
    that are CONFIRMED. Promotion is untouched by any of it.
    """
    from helpers import entity
    from venona.db.repositories import projector as projector_repo

    entity(conn, "company:drawn-co", "Drawn Co")
    entity(conn, "plat:hollow", "Hollow", status="provisional")
    entity(conn, "plat:quiet", "Quiet", status="dormant")
    conn.commit()

    got = {r["id"]: r["status"] for r in projector_repo.select_entities_for_projection(conn)}
    assert got.get("company:drawn-co") == "active"
    assert got.get("plat:hollow") == "provisional", (
        "a name one source gave us is drawn hollow, not withheld"
    )
    assert "plat:quiet" not in got, (
        "dormancy is what stops the provisional pool growing forever (ADR 0054)"
    )


def test_coverage_counts_against_the_statuses_the_canvas_drew(conn: db.Conn) -> None:
    """The line and the canvas answer one question, at BOTH settings.

    A coverage line always counted against `active` alone would report
    thousands hidden beside a canvas that had just drawn them -- the same
    contradiction ADR 0063's note records, arriving from the other side.
    """
    from helpers import document, entity, relation
    from venona.api.routers.graph import _coverage

    entity(conn, "company:confirmed-co", "Confirmed Co")
    entity(conn, "plat:seen-once", "Seen Once", status="provisional")
    document(conn, "doc:cov", domain="one.example")
    relation(conn, "a:cov", "company:confirmed-co", "PRODUCES", "plat:seen-once", doc="doc:cov")
    conn.commit()

    closed = _coverage(conn)
    opened = _coverage(conn, include_unconfirmed=True)

    pair = ("company:confirmed-co", "PRODUCES", "plat:seen-once")
    assert closed.relationships_known == opened.relationships_known, (
        "what is KNOWN does not change with a display setting; only what is shown"
    )
    assert opened.relationships_shown == closed.relationships_shown + 1, (
        f"{pair} rests on a name seen once and must be counted as shown once asked for"
    )
    assert opened.relationships_hidden == closed.relationships_hidden - 1
    assert opened.includes_unconfirmed and not closed.includes_unconfirmed, (
        "the client renders a different sentence per setting and reads this to choose"
    )


def test_a_dormant_name_is_hidden_at_both_settings(conn: db.Conn) -> None:
    """Dormancy is the drain on a pool that otherwise only grows (ADR 0054).

    `include_unconfirmed` reveals `provisional`, never `dormant` -- an entity
    that was uncorroborated AND silent for the whole window. Revealing it would
    undo the mechanism, and the count has to agree with the projection or the
    line promises rows no query can return.
    """
    from helpers import document, entity, relation
    from venona.api.routers.graph import _coverage

    entity(conn, "company:awake-co", "Awake Co")
    entity(conn, "plat:gone-quiet", "Gone Quiet", status="dormant")
    document(conn, "doc:dorm", domain="one.example")
    relation(conn, "a:dorm", "company:awake-co", "PRODUCES", "plat:gone-quiet", doc="doc:dorm")
    conn.commit()

    before = _coverage(conn).relationships_shown
    assert _coverage(conn, include_unconfirmed=True).relationships_shown == before, (
        "a dormant endpoint stays hidden at both settings"
    )


def test_a_declared_edge_is_never_called_single_source(conn: db.Conn) -> None:
    """A declared edge names no article, so its `source_count` falls back to one
    and every corroboration surface would report a publisher nobody named. The
    trunk is the whole graph's spine; dashing it says the org chart is a rumour.

    Written against a hand-inserted row rather than the fixture, because every
    `fixtures/seed/edges.yaml` row carries a real `evidence:` and so is read
    rather than declared. `profile/anchors.yaml` is the file that cites nothing.
    """
    db.insert(
        conn,
        "assertions",
        {
            "id": "assert:declared-probe",
            "run_id": "run:anchors-0001",
            "article_id": DECLARED_ARTICLE_ID,
            "cluster_id": "cluster:seed",
            "kind": "relation",
            "predicate": "PART_OF",
            "subject_id_raw": "govt:pm-tactical-network",
            "object_id_raw": "unit:u-s-army",
            "evidence_quote": (
                "PM Tactical Network PART_OF United States Army. "
                "Part of the starting structure, not read from an article."
            ),
            "confidence": 0.9,
            "method": "seed",
            "created_at": "2026-01-01T00:00:00Z",
        },
    )
    conn.commit()

    result = edge_provenance(conn, "govt:pm-tactical-network|PART_OF|unit:u-s-army")
    assert result is not None
    assert result["declared"]
    assert result["source_count"] <= 1
    assert not result["single_source"], "a declaration makes no claim about publishers"
    # The quote is what the provenance panel shows. An id triple is what it
    # showed before, for every edge in the trunk.
    quote = result["assertions"][0]["quote"]
    assert "govt:pm-tactical-network" not in quote
    assert "unit:u-s-army" not in quote


def test_a_curator_accepting_a_placement_is_a_declaration_too(conn: db.Conn) -> None:
    """`relate` falls back to `doc:curated` when the conflict names no article,
    which is every placement proposal. Judging that edge by publisher count would
    dash the curator's own ruling and drop it from the corroborated view
    entirely: they do the work and their answer disappears."""
    db.insert(
        conn,
        "assertions",
        {
            "id": "assert:curated-probe",
            "run_id": "curation:anchor|govt:peo-soldier|unit:u-s-army",
            "article_id": "doc:curated",
            "cluster_id": "cluster:curated",
            "kind": "relation",
            "predicate": "PART_OF",
            "subject_id_raw": "govt:peo-soldier",
            "object_id_raw": "unit:u-s-army",
            "evidence_quote": "Curated: PEO Soldier PART_OF United States Army.",
            "confidence": 1.0,
            "method": "manual",
            "created_at": "2026-01-01T00:00:00Z",
        },
    )
    conn.commit()

    result = edge_provenance(conn, "govt:peo-soldier|PART_OF|unit:u-s-army")
    assert result is not None
    assert result["declared"]
    assert not result["single_source"]


def test_an_extracted_edge_whose_document_vanished_is_not_a_declaration(
    conn: db.Conn,
) -> None:
    """The counterexample that keeps the rule honest. An extracted assertion
    demonstrably HAD evidence, so a missing document row is a gap in the record,
    not a statement by a person. It stays single-source."""
    db.insert(
        conn,
        "assertions",
        {
            "id": "assert:orphaned-extraction",
            "run_id": "run:probe",
            "article_id": "doc:gone",
            "cluster_id": "cluster:probe",
            "kind": "relation",
            "predicate": "PART_OF",
            "subject_id_raw": "govt:peo-iews",
            "object_id_raw": "unit:u-s-army",
            "evidence_quote": "PEO IEWS, part of the Army, awarded the contract.",
            "confidence": 0.8,
            "method": "extraction",
            "created_at": "2026-01-01T00:00:00Z",
        },
    )
    conn.commit()

    result = edge_provenance(conn, "govt:peo-iews|PART_OF|unit:u-s-army")
    assert result is not None
    assert not result["declared"]
    assert result["single_source"]


@pytest.mark.negative
def test_provenance_for_a_nonexistent_edge_is_none(conn: db.Conn) -> None:
    assert edge_provenance(conn, "company:nope|PART_OF|company:also-nope") is None


# ── readable chains ──────────────────────────────────────────────────


def test_path_chain_shows_direction(conn: db.Conn) -> None:
    """`NGC2 ←MANAGES— PEO C3N —PART_OF→ US Army`.

    Arrow direction is what makes a chain readable: it says whether each
    relationship runs with or against the way you are walking the path. A
    highlighted subgraph alone does not tell you that.
    """
    node_ids = [
        "prog:next-generation-command-and-control",
        "govt:peo-c3n",
        "unit:u-s-army",
    ]
    edges = [
        {
            "source": "govt:peo-c3n",
            "target": "prog:next-generation-command-and-control",
            "predicate": "MANAGES",
        },
        {"source": "govt:peo-c3n", "target": "unit:u-s-army", "predicate": "PART_OF"},
    ]

    chain = describe_path(conn, node_ids, edges)

    assert "Next Generation Command and Control" in chain
    assert "United States Army" in chain
    # Walking NGC2 -> PEO C3N goes AGAINST the MANAGES edge...
    assert "←MANAGES—" in chain
    # ...and PEO C3N -> Army goes WITH the PART_OF edge.
    assert "—PART_OF→" in chain


def test_path_chain_handles_a_missing_edge(conn: db.Conn) -> None:
    """Render something rather than raising: a partial chain is still useful."""
    chain = describe_path(conn, ["govt:peo-c3n", "unit:u-s-army"], [])
    assert "—?—" in chain


def test_empty_path_renders_empty(conn: db.Conn) -> None:
    assert describe_path(conn, [], []) == ""


# ── the writer reports what it wrote, not what it tried ──────────────


async def test_edge_count_comes_from_the_query_not_the_input() -> None:
    """`MATCH (a), (b)` silently drops a row whose endpoint has no node — which
    is every edge touching a provisional entity, since only `active` entities
    are projected.

    Returning `len(params)` reported the size of the INPUT: live runs logged
    `edges_written: 520` and `435` while Neo4j held 97. A counter that
    overstates by 4.5x is the number you reach for to answer "did the graph
    grow?", and it was answering "did we try?".
    """
    from venona.graph.writer import EdgeRow, GraphWriter

    driver = FakeNeo4jDriver(single={"written": 2})
    writer = GraphWriter(driver)

    rows = [
        EdgeRow(
            src=f"company:a{i}",
            dst=f"company:b{i}",
            confidence=0.9,
            article_id=f"doc:{i}",
            observed_at="t",
        )
        for i in range(7)
    ]
    written = await writer.upsert_edges("PART_OF", rows)

    assert written == 2, "must report what the MERGE reached, not the 7 submitted"
    assert "count(DISTINCT e)" in driver.queries[0], (
        "count(e) would tally write attempts — several assertions MERGE the same edge"
    )


# ── the brief-scoped subgraph ────────────────────────────────────────
#
# These are SHAPE tests, which is what `helpers.FakeNeo4jDriver` is for: `make
# test` is offline and there is no Neo4j to answer a query, so what can be
# verified is what the Cypher SAYS -- an undirected pattern, a missing id bound
# on the second endpoint, a node query driven by the edge set.


def _node_row(entity_id: str) -> dict:
    return {
        "id": entity_id,
        "name": entity_id,
        "type": "Company",
        "org_type": None,
        "relevance": 0.5,
        "summary": None,
        "mention_count": 1,
    }


async def test_the_subgraph_binds_both_endpoints_to_the_id_set() -> None:
    """The one line that separates an induced subgraph from a neighbourhood.

    With only `a.id IN $ids` the query walks OUT of the brief, and the canvas
    quietly stops matching the thing it claims to be showing.
    """
    driver = FakeNeo4jDriver([], [], [{"n": 0}])
    await queries.subgraph(driver, ["company:a", "company:b"])

    edge_query = driver.calls[0][0]
    assert "a.id IN $ids" in edge_query
    assert "b.id IN $ids" in edge_query


async def test_the_subgraph_pattern_is_directed() -> None:
    """`-[r]-` binds each relationship twice whenever both endpoints are in the
    set -- which here is always -- and `_edge` keys on `source|predicate|target`,
    so the duplicate reaches Cytoscape as a second element with an id it already
    has. The directed pattern avoids it by construction rather than by a later
    `DISTINCT`."""
    driver = FakeNeo4jDriver([], [], [{"n": 0}])
    await queries.subgraph(driver, ["company:a", "company:b"])

    edge_query = driver.calls[0][0]
    assert "-[r]->" in edge_query
    assert "-[r]-(" not in edge_query


async def test_the_subgraph_includes_isolated_members() -> None:
    """A brief that named three entities which connect to nothing else it named
    is a real finding. An overview that drops them shows a tidier brief than the
    one that was written."""
    nodes = [_node_row("company:a"), _node_row("company:b"), _node_row("company:c")]
    driver = FakeNeo4jDriver([], nodes, [{"n": 0}])

    payload = await queries.subgraph(driver, ["company:a", "company:b", "company:c"])

    assert [n["id"] for n in payload.nodes] == ["company:a", "company:b", "company:c"]
    assert payload.edges == []


async def test_the_subgraph_caps_the_id_set() -> None:
    """The cap lives in the function that owns the `IN` list, not at the router
    -- exactly as `NEIGHBOR_LIMIT` does."""
    ids = [f"company:e{i}" for i in range(queries.SUBGRAPH_ID_CAP + 50)]
    driver = FakeNeo4jDriver([], [], [{"n": 0}])

    await queries.subgraph(driver, ids)

    assert len(driver.calls[0][1]["ids"]) == queries.SUBGRAPH_ID_CAP


async def test_the_subgraph_drops_an_edge_whose_endpoint_the_payload_lacks() -> None:
    """The `kept` invariant `overview` maintains: an edge to a node that is not
    in the payload would arrive at Cytoscape as a dangling reference."""
    edges = [
        {
            "source": "company:a",
            "target": "company:gone",
            "predicate": "PART_OF",
            "source_count": 2,
            "confidence": 0.9,
            "kind": None,
            "first_seen": None,
            "last_seen": None,
        }
    ]
    driver = FakeNeo4jDriver(edges, [_node_row("company:a")], [{"n": 1}])

    payload = await queries.subgraph(driver, ["company:a", "company:gone"], types=["Company"])

    assert payload.edges == []
    assert [n["id"] for n in payload.nodes] == ["company:a"]


# ── the overview's two bounds ────────────────────────────────────────
#
# Shape tests again. Both of these were live defects: the node query had no
# `LIMIT` at all -- the one unbounded result set in the read path -- and
# `hide_isolated` was applied client-side only, so 61% of the payload was
# serialised, sent, and set to `display: none` on arrival.


def _edge_row(source: str, target: str) -> dict:
    return {
        "source": source,
        "target": target,
        "predicate": "PART_OF",
        "source_count": 2,
        "confidence": 0.9,
        "kind": None,
        "first_seen": None,
        "last_seen": None,
    }


async def test_the_overview_node_query_is_bounded() -> None:
    """It was not, and nothing else in the read path is unbounded."""
    driver = FakeNeo4jDriver([], [], [{"n": 0}], [{"n": 0}])

    await queries.overview(driver)

    node_query, params = driver.calls[1]
    assert "LIMIT $node_limit" in node_query
    assert params["node_limit"] == queries.OVERVIEW_NODE_LIMIT


async def test_hide_isolated_bounds_the_nodes_to_the_edge_endpoints() -> None:
    """The server's definition of isolated is the CANVAS'S definition: not an
    endpoint of any edge in this result. Deriving it from the returned edges is
    what stops the two drifting apart."""
    edges = [_edge_row("company:a", "company:b")]
    nodes = [_node_row("company:a"), _node_row("company:b")]
    driver = FakeNeo4jDriver(edges, nodes, [{"n": 1}])

    payload = await queries.overview(driver, hide_isolated=True)

    node_query, params = driver.calls[1]
    assert "NOT $hide_isolated OR n.id IN $connected" in node_query
    assert params["hide_isolated"] is True
    assert params["connected"] == ["company:a", "company:b"]
    assert payload.truncated is False


async def test_the_withheld_isolated_entities_are_counted() -> None:
    """The canvas says "183 unconnected hidden · show" off this number.

    It used to count them itself, because it was the one hiding them. Moving the
    filter to the server without moving the count would have made the app stop
    admitting it was hiding anything -- a view showing 115 of 298 entities with
    nothing on screen saying so.
    """
    edges = [_edge_row("company:a", "company:b")]
    nodes = [_node_row("company:a"), _node_row("company:b")]
    driver = FakeNeo4jDriver(edges, nodes, [{"n": 1}], [{"n": 50}])

    payload = await queries.overview(driver, hide_isolated=True)

    assert payload.isolated_hidden == 48
    assert [n["id"] for n in payload.nodes] == ["company:a", "company:b"]


@pytest.mark.negative
async def test_nothing_is_reported_withheld_when_nothing_was() -> None:
    """`isolated_hidden` describes the FILTER, not the graph. With the filter
    off, an isolated entity is on the canvas -- reporting it as hidden would put
    a "show" link over a view already showing it."""
    driver = FakeNeo4jDriver([], [_node_row("company:a")], [{"n": 0}], [{"n": 9}])

    payload = await queries.overview(driver, hide_isolated=False)

    assert payload.isolated_hidden == 0


async def test_a_clipped_node_set_says_so() -> None:
    """`truncated` covers BOTH caps. A node cap that reported nothing would draw
    a subset of the corpus while the readout claimed it was the whole thing."""
    driver = FakeNeo4jDriver([], [_node_row("company:a")], [{"n": 0}], [{"n": 900}])

    payload = await queries.overview(driver)

    assert payload.truncated is True


@pytest.mark.negative
async def test_an_empty_id_set_never_opens_a_session() -> None:
    """A brief that names no projected entity is a normal outcome, not a query."""
    driver = FakeNeo4jDriver()

    payload = await queries.subgraph(driver, [])

    assert driver.calls == []
    assert payload.nodes == [] and payload.edges == []


async def test_the_brief_scope_counts_add_up(conn: db.Conn) -> None:
    """`mentioned >= resolved >= present`, and `missing` accounts for the gap.

    This is the assertion that stops `scope` becoming another `entity_count` --
    a field whose name and contents disagree, which this codebase already
    carries one of and documents as a misnomer.
    """
    from types import SimpleNamespace

    from venona.api.routers.graph import graph_brief
    from venona.api.schemas.graph import BriefSubgraphRequest
    from venona.services.brief_service import brief_entities

    entities = brief_entities(conn, "brief:2026-08-04")
    assert entities is not None

    # Only the first two of the brief's entities are in the projection.
    present = entities.resolved[:2]
    driver = FakeNeo4jDriver([], [_node_row(eid) for eid in present], [{"n": 0}])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=driver)))
    request.app.state.graph = SimpleNamespace(driver=driver)

    response = await graph_brief(BriefSubgraphRequest(brief_id="brief:2026-08-04"), request, conn)
    scope = response.scope

    assert scope.mentioned >= scope.resolved >= scope.present
    assert scope.present == len(present)
    assert len(scope.missing) == scope.resolved - scope.present
    assert set(scope.missing).isdisjoint(present)
    assert scope.id_cap_reached is False


@pytest.mark.negative
async def test_the_brief_subgraph_404s_for_an_unknown_brief(conn: db.Conn) -> None:
    from types import SimpleNamespace

    from fastapi import HTTPException
    from venona.api.routers.graph import graph_brief
    from venona.api.schemas.graph import BriefSubgraphRequest

    driver = FakeNeo4jDriver()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=None)))
    request.app.state.graph = SimpleNamespace(driver=driver)

    with pytest.raises(HTTPException) as raised:
        await graph_brief(BriefSubgraphRequest(brief_id="brief:1999-01-01:nope"), request, conn)
    assert raised.value.status_code == 404


# ── traversals that could run away ───────────────────────────────────


async def test_path_finding_walks_only_ontology_predicates() -> None:
    """⭐ An untyped variable-length pattern walks EVERY relationship in the
    database, including any a future writer adds for another purpose -- and an
    unbounded `allShortestPaths` over a hub is the query most likely to hang
    the explorer.

    Interpolated for the same reason the depth bound is: a relationship type
    cannot be a parameter in a pattern. Safe only because `parse_ontology`
    validates every name as UPPER_SNAKE first.
    """
    from venona.ontology import get_ontology

    driver = FakeNeo4jDriver([])
    await queries.shortest_paths(driver, "company:a", "company:b")

    # A `neo4j.Query`, not a string: that wrapper is what carries the timeout.
    cypher = str(driver.calls[0][0].text)
    assert "allShortestPaths" in cypher
    ontology = get_ontology()
    for name in ontology.path_predicate_names:
        assert f"`{name}`" in cypher, f"{name} must be in the type restriction"
    # An excluded predicate is a hub every member of its domain shares, so a path
    # through it is true and uninformative and hides the route that is not.
    excluded = set(ontology.predicate_names) - set(ontology.path_predicate_names)
    assert excluded, "the ontology declares none, so this test proves nothing"
    for name in excluded:
        assert f"`{name}`" not in cypher, f"{name} is declared excluded_from_paths"
    assert "n:Entity" in cypher, "every node on the path must be an Entity"


async def test_path_predicates_can_be_narrowed_by_the_caller() -> None:
    """An explicit list overrides the ontology's default, and a name the ontology
    does not know never reaches the pattern -- interpolation is only safe because
    `parse_ontology` validated every name it emits."""
    from venona.ontology import get_ontology

    known = get_ontology().predicate_names[0]
    driver = FakeNeo4jDriver([])
    await queries.shortest_paths(
        driver, "company:a", "company:b", predicates=[known, "DROP_EVERYTHING"]
    )
    cypher = str(driver.calls[0][0].text)
    assert f"`{known}`" in cypher
    assert "DROP_EVERYTHING" not in cypher


async def test_the_runaway_queries_carry_a_deadline() -> None:
    """A query with no timeout does not fail -- it holds a request open until
    something else gives up."""
    driver = FakeNeo4jDriver([])
    await queries.shortest_paths(driver, "company:a", "company:b")

    sent = driver.calls[0][0]
    assert getattr(sent, "timeout", None) == queries.QUERY_TIMEOUT_SECONDS, (
        "path-finding is exponential in depth over a hub; it needs a deadline"
    )


async def test_the_neighbourhood_count_applies_the_same_filters_as_the_page() -> None:
    """⭐ Counting without the filters makes `total` a different question from
    the one `edges` answers, so `truncated` promises more for edges the caller
    asked to exclude -- a "show more" that produces nothing."""
    driver = FakeNeo4jDriver([], [], [{"n": 0}])
    await queries.neighborhood(driver, ["company:a"], min_source_count=2, min_confidence=0.7)

    count_query = driver.calls[2][0]
    assert "count(DISTINCT r)" in count_query
    for clause in (
        "type(r) IN $predicates",
        "r.source_count",
        "r.confidence",
        "a.type IN $types OR b.type IN $types",
    ):
        assert clause in count_query, f"the count must apply {clause} like the page query does"


# ── a type filter is a spotlight, not a scissors ─────────────────────


def _typed_node_row(entity_id: str, entity_type: str) -> dict:
    return {**_node_row(entity_id), "type": entity_type}


async def test_a_type_filter_keeps_the_edges_that_leave_the_selected_type() -> None:
    """⭐ The defect this rule exists to prevent, reported 2026-09-11: selecting
    one type emptied the canvas.

    Almost every predicate this ontology declares runs between two DIFFERENT
    types, so requiring both endpoints to match cut every edge a single-type
    selection could have had. The isolated pass then dropped each node for
    having none, and a reader who clicked `Technology` was told no name survived
    the filters while thirty-two did.
    """
    driver = FakeNeo4jDriver([], [], [{"n": 0}], [{"n": 0}])

    await queries.overview(driver, types=["Technology"])

    edge_query = driver.calls[0][0]
    assert "a.type IN $types OR b.type IN $types" in edge_query
    assert "a.type IN $types AND b.type IN $types" not in edge_query


async def test_the_far_end_of_a_kept_edge_is_returned_whatever_its_type() -> None:
    """An edge to a node the payload does not carry is dropped by the `kept`
    tail, so keeping cross-type edges without returning their far endpoints
    would put the canvas back where it started -- with extra steps."""
    edges = [
        {**_edge_row("prog:x", "tech:y"), "predicate": "USES_TECH"},
    ]
    nodes = [_typed_node_row("prog:x", "Program"), _typed_node_row("tech:y", "Technology")]
    driver = FakeNeo4jDriver(edges, nodes, [{"n": 1}], [{"n": 2}])

    payload = await queries.overview(driver, types=["Technology"])

    node_query, params = driver.calls[1]
    assert "n.type IN $types OR n.id IN $connected" in node_query
    assert params["connected"] == ["prog:x", "tech:y"]
    assert [n["id"] for n in payload.nodes] == ["prog:x", "tech:y"]
    assert len(payload.edges) == 1, "the cross-type edge is the answer, not a violation of it"


async def test_the_matching_count_admits_context_nodes_too() -> None:
    """`isolated_hidden` is `matching - connected`. Count the selected types
    alone against a `connected` set that also holds their neighbours and the
    subtraction goes NEGATIVE -- which `max(0, ...)` renders as "nothing is
    hidden" on a view that is hiding plenty."""
    edges = [_edge_row("prog:x", "tech:y")]
    nodes = [_typed_node_row("prog:x", "Program"), _typed_node_row("tech:y", "Technology")]
    driver = FakeNeo4jDriver(edges, nodes, [{"n": 1}], [{"n": 40}])

    payload = await queries.overview(driver, types=["Technology"], hide_isolated=True)

    count_query, params = driver.calls[3]
    assert "n.type IN $types OR n.id IN $connected" in count_query
    assert params["connected"] == ["prog:x", "tech:y"]
    assert payload.isolated_hidden == 38


async def test_the_neighbourhood_admits_an_edge_through_its_seed() -> None:
    """The seed is one of the two endpoints, so a seed of a selected type keeps
    every neighbour it has. Testing the far endpoint alone made the same chips
    mean one thing on the overview and another on an expansion."""
    driver = FakeNeo4jDriver([], [], [{"n": 0}])

    await queries.neighborhood(driver, ["tech:y"], types=["Technology"])

    assert "a.type IN $types OR b.type IN $types" in driver.calls[0][0]


# ── the traced route is walked over what the reader can see ──────────


async def test_the_path_walk_applies_the_source_floor() -> None:
    """⭐ A route the canvas will not draw is not an answer.

    The rail hides an edge below the floor, so a path built on one reports a
    connection and then shows a gap where its middle was.
    """
    driver = FakeNeo4jDriver([])

    await queries.shortest_paths(driver, "company:a", "company:b", min_source_count=2)

    cypher = str(driver.calls[0][0].text)
    assert "r.source_count, 1) >= $min_sources" in cypher
    assert driver.calls[0][1]["min_sources"] == 2
    assert "coalesce(r.declared, false)" in cypher, (
        "a declaration names no article, so its source count is a floor artefact"
    )


async def test_the_path_route_hands_the_rail_s_filters_to_the_walk() -> None:
    """⭐ The route never passed them, so `TRACE A CONNECTION` walked predicates
    the reader had deselected and edges below their source floor -- and
    `applyVisibility` then hid the answer it had just returned."""
    from types import SimpleNamespace

    from venona.api.routers.graph import graph_path
    from venona.ontology import get_ontology

    known = get_ontology().path_predicate_names[0]
    driver = FakeNeo4jDriver([])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=None)))
    request.app.state.graph = SimpleNamespace(driver=driver)

    await graph_path(
        request,
        source="company:a",
        target="company:b",
        max_depth=4,
        predicates=[known],
        min_source_count=3,
        conn=None,
    )

    cypher = str(driver.calls[0][0].text)
    assert f"`{known}`" in cypher
    excluded = [n for n in get_ontology().path_predicate_names if n != known]
    for name in excluded:
        assert f"`{name}`" not in cypher, "a deselected predicate must not be walked"
    assert driver.calls[0][1]["min_sources"] == 3


async def test_a_declared_edge_on_a_path_says_so() -> None:
    """`_edge` reads `declared` off the projection, and the path's relationship
    map is the one place that did not supply it -- so every trunk edge on a
    traced route arrived as unconfirmed and drew dashed."""
    driver = FakeNeo4jDriver([])

    await queries.shortest_paths(driver, "company:a", "company:b")

    assert "declared: r.declared" in str(driver.calls[0][0].text)


# ── the overview caps are derived, not chosen ────────────────────────


def test_the_two_overview_caps_bite_at_the_same_corpus_size() -> None:
    """⭐ The invariant that was broken, and the reason it went unnoticed.

    The caps are a PAIR bounding one payload, so they should run out together.
    At 3,000 edges and 4,000 nodes they did not: under the measured shape the
    edge cap clipped at ~7,600 total entities and the node cap at ~10,400, so
    the edge cap alone decided what a reader saw and the node cap was decoration.
    A mismatch is invisible -- the canvas says `capped` either way.

    The shape is `docs/GRAPH-PERF.md`'s, re-checked against a live
    `/graph/overview` on 2026-08-16: 61.4% of active entities have no surviving
    edge, and the rest carry 1.0174 edges each.
    """
    isolated_fraction = 0.614
    edges_per_connected = 1.0174

    def total_entities_at(connected: float) -> float:
        return connected / (1 - isolated_fraction)

    edge_cap_bites = total_entities_at(queries.OVERVIEW_EDGE_LIMIT / edges_per_connected)
    node_cap_bites = total_entities_at(queries.OVERVIEW_NODE_LIMIT)

    assert abs(edge_cap_bites - node_cap_bites) / node_cap_bites < 0.05, (
        f"the edge cap bites at ~{edge_cap_bites:,.0f} entities and the node cap at "
        f"~{node_cap_bites:,.0f}; one of them is doing nothing"
    )


def test_the_overview_caps_are_not_below_what_the_browser_draws() -> None:
    """The caps exist because SOMETHING falls over first, and measurement says
    it is the browser's layout rather than the server (21-32ms and 80KB today,
    tens of milliseconds at 50x).

    `docs/GRAPH-PERF.md`'s last measured row is 7,720 visible nodes and 7,854
    edges laid out in 755ms on the `draft` tier. A cap below that clips a graph
    the reader's machine would have drawn -- which is the state these were found
    in, because the draft tier and the position cache moved the ceiling out
    without anyone re-deriving them.
    """
    assert queries.OVERVIEW_EDGE_LIMIT >= 7854
    assert queries.OVERVIEW_NODE_LIMIT >= 7720
