"""Read queries against the Neo4j projection.

This is what Neo4j is FOR. The hover card reads neighbours from Postgres because
it is on the hot path for every mouse-over and must survive Neo4j being down;
the explorer's traversals and path-finding go through Cypher, where recursive
queries are the native operation.

Every traversal here has an ORDER BY and a LIMIT. `govt:defense-department`
and `unit:u-s-army`
are hubs, and an unbounded `MATCH (n)-[r]-(m)` on a hub hangs the UI. There is
deliberately no "expand all".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from neo4j import AsyncDriver, Query

from venona.ontology import get_ontology

# A supernode expansion must never be able to flood the canvas.
NEIGHBOR_LIMIT = 50

# The whole graph, when the whole graph is the question.
#
# Sized from the MEASURED shape of the projection rather than from a snapshot of
# its size, which is what the previous rationale did -- it was anchored to "184
# active entities, 908 relationships" and the corpus overtook it without anything
# noticing. `docs/GRAPH-PERF.md` (2026-08-16) measured 61.4% of active entities
# with no surviving edge and a mean degree of 2.03 over the rest, so 3,000 edges
# name roughly 3,000 distinct endpoints rather than 6,000, and the unconnected
# tail is about 1.6x whatever the connected set is.
#
# The node cap is the point of the pair. There was NO node limit at all: every
# active entity came back on every call, and this was the only unbounded result
# set in the read path.
#
# SET AT WHAT THE BROWSER CAN ACTUALLY DRAW, which is not where they started.
# The arithmetic, from the shape above (61.4% isolated, 1.0174 edges per
# connected node) and the layout table in `docs/GRAPH-PERF.md`:
#
#   total entities   connected (sent)   edges     draft layout
#      5,000              1,930          1,964        142ms
#     10,000              3,860          3,927        301ms
#     20,000              7,720          7,854        755ms
#
# Inverted, the ORIGINAL caps bit at 3,000 edges = ~7,640 total entities and
# 4,000 nodes = ~10,360 -- so the edge cap clipped the graph while the browser
# was still laying the same corpus out in about three hundred milliseconds. The
# server was never the constraint (21-32ms and 80KB, tens of milliseconds at 50x)
# and the `draft` quality tier and the position cache moved the client's ceiling
# out from under these numbers without anyone re-deriving them.
#
# So they sit at the LAST MEASURED ROW rather than an extrapolated one: 8,000 of
# each is 20,000 total entities, 755ms cold and ~20ms from cache. Past that the
# answer is not a bigger dump -- it is opening on a seed the reader expands,
# which is a different product and a different decision.
OVERVIEW_EDGE_LIMIT = 8000
OVERVIEW_NODE_LIMIT = 8000
QUERY_ELEMENT_CAP = 500
MAX_PATH_DEPTH = 6

# How long any one read may hold a Neo4j session. The two traversals that can
# genuinely run away are path-finding, whose cost is exponential in depth over
# a hub, and the substring search, which has no index behind it -- and a query
# with no deadline does not fail, it holds a request open until something else
# gives up. Generous enough that an honest slow query on a large corpus still
# answers; short enough that a runaway is an error a reader can see.
QUERY_TIMEOUT_SECONDS = 15.0

# An induced subgraph can never be larger than the overview it is a subset of,
# so it shares the ceiling -- one number to reason about rather than two that
# have to agree.
SUBGRAPH_EDGE_LIMIT = OVERVIEW_EDGE_LIMIT

# NOT a seed cap. `neighborhood` caps seeds at 50 because 50 seeds is 50 hub
# expansions; here the ids only bound an `IN` list, and a dense brief
# legitimately names well over 50 entities. Sized to "the largest brief that can
# exist" with headroom, so a normal one is never silently clipped.
SUBGRAPH_ID_CAP = 400


@dataclass
class GraphPayload:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    total: int = 0
    #: Active entities the `hide_isolated` filter left out. Counted and reported
    #: rather than silently dropped: the canvas has always said "183 unconnected
    #: hidden · show", and it could only say that while IT was the one hiding
    #: them. Serving the filter here without serving the count would have made
    #: the app stop admitting it was hiding anything at all.
    isolated_hidden: int = 0


#: The statuses a canvas query admits. `provisional` is a name one source has
#: given us, drawn hollow rather than withheld (ADR 0247); `dormant` is never
#: here, because it is uncorroborated AND silent for the whole window and
#: dormancy is what stops the pool growing forever (ADR 0054).
CONFIRMED_ONLY = ["active"]
WITH_UNCONFIRMED = ["active", "provisional"]


def _statuses(include_unconfirmed: bool) -> list[str]:
    return WITH_UNCONFIRMED if include_unconfirmed else CONFIRMED_ONLY


def _node(record: Any) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record.get("name") or record["id"],
        "type": record.get("type"),
        "org_type": record.get("org_type"),
        "relevance": record.get("relevance") or 0.0,
        "summary": record.get("summary"),
        "mention_count": record.get("mention_count") or 0,
        # What the canvas draws hollow (ADR 0247). A node is here at all only
        # because the projection admitted its status, so this says which of the
        # two admitted kinds it is -- not whether it should be drawn.
        "status": record.get("status") or "active",
    }


def _edge(record: Any) -> dict[str, Any]:
    source = record["source"]
    target = record["target"]
    predicate = record["predicate"]
    return {
        # Deterministic and URL-safe, so the frontend can address an edge
        # without a server round trip.
        "id": f"{source}|{predicate}|{target}",
        "source": source,
        "target": target,
        "predicate": predicate,
        # Distinct PUBLISHERS, not articles. `useCytoscape` dashes an edge at
        # `source_count <= 1` and `EdgeProvenancePanel` labels it single-source
        # from `graph_service`, which has always counted publishers -- serving
        # the article count here made the two disagree about the same edge.
        "source_count": record.get("source_count") or 1,
        # A DECLARATION, not a reading. It names no article, so `source_count`
        # falls back to one and every corroboration surface would call it
        # single-source -- which is a claim about publishers that nobody made.
        # The canvas leaves it solid and the floor above lets it through.
        "declared": bool(record.get("declared")),
        "confidence": record.get("confidence") or 0.5,
        "kind": record.get("kind"),
        "first_seen": record.get("first_seen"),
        "last_seen": record.get("last_seen"),
    }


async def neighborhood(
    driver: AsyncDriver,
    seed_ids: list[str],
    *,
    limit: int = NEIGHBOR_LIMIT,
    types: list[str] | None = None,
    predicates: list[str] | None = None,
    min_source_count: int = 1,
    min_confidence: float = 0.0,
    include_unconfirmed: bool = False,
) -> GraphPayload:
    """One hop out from the seeds, filtered server-side.

    `include_unconfirmed` must match whatever the overview was drawn with, or
    expanding a node the canvas drew hollow returns nothing and reads as an
    entity with no neighbours (ADR 0247).

    Filtering here rather than client-side means we never fetch what we would
    immediately hide -- which matters when the seed is a hub.

    The type rule is `overview`'s, spelled against this pattern: an edge is kept
    when either end carries a selected type. The seed is one of those ends, so a
    seed of a selected type keeps every neighbour it has.
    """
    known = get_ontology().predicate_names
    allowed = [p for p in (predicates or known) if p in known]
    if not allowed or not seed_ids:
        return GraphPayload()

    # The pattern is UNDIRECTED on purpose -- one hop out of a seed means either
    # direction -- but that makes it bind twice whenever both endpoints are
    # seeds, once from each end. `_edge` keys on `source|predicate|target`, so
    # the duplicate arrived at Cytoscape as a second element with an id it
    # already had, which it throws on; and the duplicates counted against
    # `LIMIT`, halving what a two-seed query could return. `DISTINCT r` below
    # collapses the pair before either happens.
    query = """
    MATCH (a:Entity)-[r]-(b:Entity)
    WHERE a.id IN $seeds
      AND type(r) IN $predicates
      AND a.status IN $statuses AND b.status IN $statuses
      AND (coalesce(r.declared, false) OR coalesce(r.source_count, 1) >= $min_sources)
      AND coalesce(r.confidence, 0.5) >= $min_confidence
      AND ($types IS NULL OR a.type IN $types OR b.type IN $types)
    WITH DISTINCT r, startNode(r) AS s, endNode(r) AS e
    ORDER BY coalesce(r.mention_count, 1) DESC, coalesce(r.confidence, 0) DESC, type(r)
    LIMIT $limit
    RETURN s.id AS source, e.id AS target, type(r) AS predicate,
           r.source_count AS source_count, r.declared AS declared,
           r.confidence AS confidence,
           r.kind AS kind, r.first_seen AS first_seen, r.last_seen AS last_seen
    """

    statuses = _statuses(include_unconfirmed)
    async with driver.session() as session:
        result = await session.run(
            query,
            seeds=seed_ids,
            statuses=statuses,
            predicates=allowed,
            types=types,
            limit=limit,
            min_sources=min_source_count,
            min_confidence=min_confidence,
        )
        edges = [_edge(record) for record in await result.data()]

        node_ids = set(seed_ids)
        for edge in edges:
            node_ids.add(edge["source"])
            node_ids.add(edge["target"])

        node_result = await session.run(
            "MATCH (n:Entity) WHERE n.id IN $ids AND n.status IN $statuses "
            "RETURN n.id AS id, n.name AS name, n.type AS type, n.org_type AS org_type, "
            "n.relevance AS relevance, n.summary AS summary, "
            "n.mention_count AS mention_count, n.status AS status",
            ids=list(node_ids),
            statuses=statuses,
        )
        nodes = [_node(record) for record in await node_result.data()]

        # THE SAME FILTERS THE PAGE QUERY APPLIED. Counting without them makes
        # `total` a different question from the one `edges` answers, so
        # `truncated` reports "there is more" for edges the caller explicitly
        # asked to exclude -- a "show more" that produces nothing.
        total_result = await session.run(
            "MATCH (a:Entity)-[r]-(b:Entity) WHERE a.id IN $seeds "
            "AND type(r) IN $predicates "
            "AND a.status IN $statuses AND b.status IN $statuses "
            "AND (coalesce(r.declared, false) OR coalesce(r.source_count, 1) >= $min_sources) "
            "AND coalesce(r.confidence, 0.5) >= $min_confidence "
            "AND ($types IS NULL OR a.type IN $types OR b.type IN $types) "
            "RETURN count(DISTINCT r) AS n",
            seeds=seed_ids,
            statuses=statuses,
            predicates=allowed,
            types=types,
            min_sources=min_source_count,
            min_confidence=min_confidence,
        )
        total_record = await total_result.single()
        total = total_record["n"] if total_record else len(edges)

    kept = {n["id"] for n in nodes}
    edges = [e for e in edges if e["source"] in kept and e["target"] in kept]

    return GraphPayload(nodes=nodes, edges=edges, truncated=total > len(edges), total=total)


async def overview(
    driver: AsyncDriver,
    *,
    limit: int = OVERVIEW_EDGE_LIMIT,
    node_limit: int = OVERVIEW_NODE_LIMIT,
    types: list[str] | None = None,
    predicates: list[str] | None = None,
    min_source_count: int = 1,
    min_confidence: float = 0.0,
    hide_isolated: bool = False,
    include_unconfirmed: bool = False,
) -> GraphPayload:
    """Every entity the filters admit and every edge between them.

    `include_unconfirmed` widens the statuses drawn to names one source has
    given us (ADR 0247). It is the reader's switch and defaults off: the
    corroborated graph is the one worth landing on, and the revealed set roughly
    doubles both result sets.

    `neighborhood` requires a seed and walks one hop, which answers "what is
    near this?" but never "what is here at all?" -- so the shape of the graph,
    its clusters and its isolated nodes were unreachable from the UI. At this
    scale that restriction buys nothing: the entire projection is smaller than a
    single hop out of a hub like `govt:defense-department`.

    Isolated entities are INCLUDED by default. A node with no surviving edge is a
    real finding -- something the corpus corroborated but never connected -- and
    an overview that silently drops it shows a tidier graph than the one that
    exists. `hide_isolated` is the CLIENT'S filter arriving where it can be
    served: the canvas hides them by default, and it was hiding them after the
    server had already serialised them -- 61% of the payload and 61% of the
    constructed elements, drawn never (`docs/GRAPH-PERF.md`). It stays a filter
    with a default of False, so "show unconnected" is still one refetch away.

    A TYPE FILTER IS A SPOTLIGHT, NOT A SCISSORS. An edge survives when EITHER
    endpoint carries a selected type, and its far endpoint is returned whatever
    its own type -- so `types=[Technology]` answers "the technologies and who
    uses them" rather than "technologies joined to technologies". Requiring both
    endpoints is the same query with an empty answer on this ontology: almost
    every predicate it defines runs between two different types (`USES_TECH`,
    `PRODUCES`, `WORKS_FOR`, `BASED_IN`), so a single-type selection cut every
    edge, the isolated pass then dropped every node for having none, and the
    canvas went blank while the rail insisted nine types existed. ADR 0226.

    Both result sets are BOUNDED, and `truncated` covers both. The node query had
    no `LIMIT` at all, which made it the one unbounded result set in the read
    path.
    """
    known = get_ontology().predicate_names
    allowed = [p for p in (predicates or known) if p in known]
    if not allowed:
        return GraphPayload()
    statuses = _statuses(include_unconfirmed)

    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (a:Entity)-[r]->(b:Entity)
            WHERE type(r) IN $predicates
              AND a.status IN $statuses AND b.status IN $statuses
              AND (coalesce(r.declared, false) OR coalesce(r.source_count, 1) >= $min_sources)
              AND coalesce(r.confidence, 0.5) >= $min_confidence
              AND ($types IS NULL OR a.type IN $types OR b.type IN $types)
            RETURN a.id AS source, b.id AS target, type(r) AS predicate,
                   r.source_count AS source_count, r.declared AS declared,
           r.confidence AS confidence,
                   r.kind AS kind, r.first_seen AS first_seen, r.last_seen AS last_seen
            ORDER BY coalesce(r.mention_count, 1) DESC, coalesce(r.confidence, 0) DESC
            LIMIT $limit
            """,
            predicates=allowed,
            statuses=statuses,
            types=types,
            limit=limit,
            min_sources=min_source_count,
            min_confidence=min_confidence,
        )
        edges = [_edge(record) for record in await result.data()]

        # What "isolated" means, defined ONCE and here: an entity that is not an
        # endpoint of any edge in THIS result. That is exactly what the canvas
        # means by it -- `applyVisibility` runs its isolated pass last, over the
        # edges that survived the filters -- so the two cannot drift into
        # disagreeing about which nodes are unconnected. Deriving it from the
        # returned edges rather than from a second Cypher predicate is what makes
        # that identity structural rather than a coincidence maintained by hand.
        connected = sorted({e["source"] for e in edges} | {e["target"] for e in edges})

        node_result = await session.run(
            """
            MATCH (n:Entity)
            WHERE n.status IN $statuses
              AND ($types IS NULL OR n.type IN $types OR n.id IN $connected)
              AND (NOT $hide_isolated OR n.id IN $connected)
            // Connected FIRST, so the cap can never strand an edge whose
            // endpoint it clipped -- an edge with a missing endpoint is dropped
            // by the tail below, and a graph that loses edges before it loses
            // isolated nodes is the wrong graph to show.
            WITH n, CASE WHEN n.id IN $connected THEN 0 ELSE 1 END AS rank
            ORDER BY rank, coalesce(n.relevance, 0.0) DESC,
                     coalesce(n.mention_count, 0) DESC, n.id
            LIMIT $node_limit
            RETURN n.id AS id, n.name AS name, n.type AS type, n.org_type AS org_type,
                   n.relevance AS relevance, n.summary AS summary,
                   n.mention_count AS mention_count, n.status AS status
            """,
            statuses=statuses,
            types=types,
            connected=connected,
            hide_isolated=hide_isolated,
            node_limit=node_limit,
        )
        nodes = [_node(record) for record in await node_result.data()]

        total_result = await session.run(
            "MATCH (a:Entity)-[r]->(b:Entity) "
            "WHERE a.status IN $statuses AND b.status IN $statuses RETURN count(r) AS n",
            statuses=statuses,
        )
        total_record = await total_result.single()
        total = total_record["n"] if total_record else len(edges)

        # Every active entity the filters admit, whether or not it was returned.
        # Two different questions are answered off it: how many nodes the caller
        # WOULD have got (so a clipped view can say so), and how many the
        # isolated filter withheld (so the canvas can offer to show them).
        node_total_result = await session.run(
            "MATCH (n:Entity) WHERE n.status IN $statuses "
            "AND ($types IS NULL OR n.type IN $types OR n.id IN $connected) "
            "RETURN count(n) AS n",
            statuses=statuses,
            types=types,
            connected=connected,
        )
        node_total_record = await node_total_result.single()
        matching = node_total_record["n"] if node_total_record else len(nodes)

    nodes_total = len(connected) if hide_isolated else matching

    kept = {n["id"] for n in nodes}
    edges = [e for e in edges if e["source"] in kept and e["target"] in kept]

    return GraphPayload(
        nodes=nodes,
        edges=edges,
        # EITHER cap clips the view, and the canvas says "capped" off this one
        # flag. A node cap that reported nothing would draw a subset of the
        # corpus while the readout claimed it was the whole thing.
        truncated=total > len(edges) or len(nodes) < nodes_total,
        total=total,
        isolated_hidden=max(0, matching - len(connected)) if hide_isolated else 0,
    )


async def subgraph(
    driver: AsyncDriver,
    entity_ids: list[str],
    *,
    limit: int = SUBGRAPH_EDGE_LIMIT,
    types: list[str] | None = None,
    predicates: list[str] | None = None,
    min_source_count: int = 1,
    min_confidence: float = 0.0,
) -> GraphPayload:
    """The subgraph INDUCED on an id set: both endpoints must be in the set.

    The id set is the induction, and the type filter is not part of it -- it
    spotlights within the set exactly as it does in `overview`, admitting the
    far endpoint of any edge it keeps.

    Derived from `overview`, which is this query without the id bound -- same
    filters, same `truncated`/`total` arithmetic, same tail that drops an edge
    whose endpoint the node cap removed.

    DIRECTED (`->`), not undirected, and that is not a stylistic choice. An
    induced query must bind each relationship exactly once; `neighborhood`'s
    `-[r]-` binds twice whenever both endpoints are in the seed set, which here
    is ALWAYS. That is the duplicate-edge-id bug `neighborhood` had to fix with
    `DISTINCT r`; the directed pattern avoids it by construction.

    Isolated members are INCLUDED, for the reason `overview` gives and more
    strongly: "this brief named Anduril, and nothing else it named connects to
    it" is precisely the finding a brief-scoped view exists to surface. Dropping
    it would show a tidier brief than the one that was written.
    """
    known = get_ontology().predicate_names
    allowed = [p for p in (predicates or known) if p in known]
    # An empty id set must not issue Cypher at all -- a brief that names no
    # projected entity is a normal outcome, not a query.
    if not allowed or not entity_ids:
        return GraphPayload()

    # Clamped HERE, in the function that owns the `IN` list, exactly as
    # `NEIGHBOR_LIMIT` lives here rather than at the router.
    ids = entity_ids[:SUBGRAPH_ID_CAP]

    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (a:Entity)-[r]->(b:Entity)
            WHERE a.id IN $ids AND b.id IN $ids
              AND type(r) IN $predicates
              AND a.status = 'active' AND b.status = 'active'
              AND (coalesce(r.declared, false) OR coalesce(r.source_count, 1) >= $min_sources)
              AND coalesce(r.confidence, 0.5) >= $min_confidence
              AND ($types IS NULL OR a.type IN $types OR b.type IN $types)
            RETURN a.id AS source, b.id AS target, type(r) AS predicate,
                   r.source_count AS source_count, r.declared AS declared,
           r.confidence AS confidence,
                   r.kind AS kind, r.first_seen AS first_seen, r.last_seen AS last_seen
            ORDER BY coalesce(r.mention_count, 1) DESC, coalesce(r.confidence, 0) DESC
            LIMIT $limit
            """,
            ids=ids,
            predicates=allowed,
            types=types,
            limit=limit,
            min_sources=min_source_count,
            min_confidence=min_confidence,
        )
        edges = [_edge(record) for record in await result.data()]

        # Driven by the ID SET, not by the edge set -- this is what includes the
        # members nothing connects to. `connected` widens it the other way: an
        # endpoint of a surviving edge is drawn whatever its type, which is what
        # makes the type filter a spotlight rather than a scissors.
        connected = sorted({e["source"] for e in edges} | {e["target"] for e in edges})

        node_result = await session.run(
            "MATCH (n:Entity) WHERE n.id IN $ids AND n.status = 'active' "
            "AND ($types IS NULL OR n.type IN $types OR n.id IN $connected) "
            "RETURN n.id AS id, n.name AS name, n.type AS type, n.org_type AS org_type, "
            "n.relevance AS relevance, n.summary AS summary, "
            "n.mention_count AS mention_count, n.status AS status",
            ids=ids,
            types=types,
            connected=connected,
        )
        nodes = [_node(record) for record in await node_result.data()]

        total_result = await session.run(
            "MATCH (a:Entity)-[r]->(b:Entity) WHERE a.id IN $ids AND b.id IN $ids "
            "AND a.status='active' AND b.status='active' RETURN count(r) AS n",
            ids=ids,
        )
        total_record = await total_result.single()
        total = total_record["n"] if total_record else len(edges)

    kept = {n["id"] for n in nodes}
    edges = [e for e in edges if e["source"] in kept and e["target"] in kept]

    return GraphPayload(nodes=nodes, edges=edges, truncated=total > len(edges), total=total)


async def shortest_paths(
    driver: AsyncDriver,
    source: str,
    target: str,
    *,
    max_depth: int = 4,
    predicates: Sequence[str] | None = None,
    min_source_count: int = 1,
    include_unconfirmed: bool = False,
) -> dict[str, Any]:
    """`allShortestPaths` between two entities.

    Note what this returns: ALL paths of the MINIMUM length, not the k shortest
    paths of increasing length. Those are different things, and the API does not
    pretend to offer the latter -- a `k` parameter would be unimplementable
    without a weighted traversal.

    `predicates` narrows the traversal, and it defaults to every predicate EXCEPT
    the nationality one. The ontology splits that out of the org chart precisely
    so a country cannot sit in the middle of every route -- but the split only
    made it filterable, and this query is the caller that never filtered. With a
    home country on every organisation, the shortest path between any two of them
    is "both are based in the same country", which is true, two hops, and buries
    the org-chart route the reader asked for under `LIMIT 5`.

    `predicates`, `min_source_count` and `include_unconfirmed` ARRIVE FROM THE
    FILTER RAIL, and they have to: a route is only an answer if the reader can
    see it. Walking an edge they filtered out returns a chain the canvas then
    refuses to draw -- the trace reports a connection and points at nothing. The
    reverse costs as much: with unconfirmed names drawn, a traversal that still
    refused them would report no route between two nodes the canvas has just
    joined. The weakness is expressible either way, because a path's nodes land
    on that same canvas and a name one source gave us draws hollow there.

    The source floor is spelled
    the way every other surface spells it, with a declared edge exempt, because
    a declaration names no article and its `source_count` is a floor artefact.
    """
    depth = max(1, min(int(max_depth), MAX_PATH_DEPTH))

    # UNTYPED, this walks every relationship in the database -- including any a
    # future writer adds for a different purpose -- and an unbounded
    # `allShortestPaths` over a hub is the query most likely to hang the
    # explorer. Restricted to the ontology's own predicates, interpolated for
    # the same reason the depth is: a relationship type cannot be a parameter
    # in a pattern. Safe because `parse_ontology` validates every name as
    # UPPER_SNAKE before it ever reaches here -- never interpolate a raw one.
    # Read at the point of use, never bound at import: the ontology is editable
    # over HTTP and a UI-triggered run executes in this process.
    ontology = get_ontology()
    allowed = list(predicates) if predicates is not None else list(ontology.path_predicate_names)
    # Never interpolate a name the ontology does not know: the safety argument
    # above is that `parse_ontology` validated it as UPPER_SNAKE.
    allowed = [name for name in allowed if name in ontology.predicate_names]
    pattern = "|".join(f"`{name}`" for name in allowed)
    if not pattern:
        return []

    # The depth bound cannot be parameterised in a variable-length pattern, so
    # it is interpolated from a clamped integer -- never from user text.
    query = f"""
    MATCH (a:Entity {{id: $source}}), (b:Entity {{id: $target}})
    MATCH p = allShortestPaths((a)-[:{pattern}*..{depth}]-(b))
    WHERE all(n IN nodes(p) WHERE n:Entity AND n.status IN $statuses)
      AND all(r IN relationships(p)
              WHERE coalesce(r.declared, false)
                 OR coalesce(r.source_count, 1) >= $min_sources)
    RETURN [n IN nodes(p) | n.id] AS node_ids,
           [r IN relationships(p) | {{
              source: startNode(r).id, target: endNode(r).id,
              predicate: type(r), source_count: r.source_count,
              declared: r.declared,
              confidence: r.confidence, kind: r.kind
           }}] AS rels
    LIMIT 5
    """

    async with driver.session() as session:
        result = await session.run(
            Query(query, timeout=QUERY_TIMEOUT_SECONDS),
            source=source,
            target=target,
            statuses=_statuses(include_unconfirmed),
            min_sources=min_source_count,
        )
        records = await result.data()

        paths: list[dict[str, Any]] = []
        node_ids: set[str] = set()
        edges: dict[str, dict[str, Any]] = {}

        for record in records:
            node_ids.update(record["node_ids"])
            edge_ids = []
            for rel in record["rels"]:
                edge = _edge(rel)
                edges[edge["id"]] = edge
                edge_ids.append(edge["id"])
            paths.append({"node_ids": record["node_ids"], "edge_ids": edge_ids})

        nodes: list[dict[str, Any]] = []
        if node_ids:
            node_result = await session.run(
                "MATCH (n:Entity) WHERE n.id IN $ids RETURN n.id AS id, n.name AS name, "
                "n.type AS type, n.org_type AS org_type, n.relevance AS relevance, "
                "n.summary AS summary, n.mention_count AS mention_count",
                ids=list(node_ids),
            )
            nodes = [_node(record) for record in await node_result.data()]

    return {
        "paths": paths,
        "nodes": nodes,
        "edges": list(edges.values()),
        "found": bool(paths),
        "max_depth": depth,
    }


async def search_entities(driver: AsyncDriver, query: str, *, limit: int = 10):
    """Find an entity by name or alias, ranked so the first hit is usually right.

    Returns `degree` and `mention_count` alongside the match, because a name
    alone does not let a searcher choose. Typing "army" matches a dozen
    entities; "United States Army · Organization · 41 connections" identifies
    the one that matters, and "Australian Army Research Centre · 1 connection"
    identifies the one that does not.

    Fulltext first with a CONTAINS fallback -- the fulltext index rejects some
    punctuation people actually type ("MQ-9", "C4ISR"), and a search box that
    500s on a hyphen is worse than a slower scan.
    """
    enrich = (
        "OPTIONAL MATCH (node)-[r]-(:Entity) "
        "WITH node, score, count(r) AS degree "
        "RETURN node.id AS id, node.name AS name, node.type AS type, "
        "       node.summary AS summary, coalesce(node.mention_count, 0) AS mention_count, "
        "       degree, score "
        "ORDER BY score DESC, degree DESC LIMIT $limit"
    )

    async with driver.session() as session:
        try:
            result = await session.run(
                Query(
                    "CALL db.index.fulltext.queryNodes('entity_search', $q) "
                    "YIELD node, score WHERE node.status = 'active' " + enrich,
                    timeout=QUERY_TIMEOUT_SECONDS,
                ),
                q=f"{query}~",
                limit=limit,
            )
            rows = await result.data()
            if rows:
                return rows
        except Exception:  # noqa: BLE001 - fall back rather than fail a search box
            pass

        result = await session.run(
            Query(
                "MATCH (node:Entity) WHERE node.status='active' AND "
                "(toLower(node.name) CONTAINS toLower($q) OR "
                " toLower(coalesce(node.aliases_text,'')) CONTAINS toLower($q)) "
                "WITH node, coalesce(node.relevance, 0.0) AS score " + enrich,
                timeout=QUERY_TIMEOUT_SECONDS,
            ),
            q=query,
            limit=limit,
        )
        return await result.data()
