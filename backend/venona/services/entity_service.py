"""Card assembly (contract C8) and neighbourhood queries.

The mini-graph is computed HERE so the card renders inline SVG with no graph
library on the reading surface, and neighbours come back in a total order, so
the same card always draws the same picture.
"""

from __future__ import annotations

from typing import Any

from venona.api.schemas.entity import (
    Card,
    GlossaryAlternative,
    Grounding,
    MiniGraph,
    MiniGraphNeighbor,
    Signal,
)
from venona.db import core as db
from venona.db.models import normalize
from venona.db.repositories import entity as repo
from venona.ontology import get_ontology

MINI_GRAPH_LIMIT = 10
SIGNAL_LIMIT = 4

#: The merge map: every raw entity id to its surviving id. Passed from caller
#: to helper so that one request collapses the chains once.
Mappings = dict[str, str]


def _ordered(predicate: str, src: str, dst: str) -> tuple[str, str]:
    """Endpoints of a symmetric edge, in the one order it is counted under.

    `graph/writer.py` does exactly this before it MERGEs, with the comment
    "otherwise every pair accumulates two mirrored edges" -- and nothing in
    `pipeline/` normalises, so `assertions` really does hold both directions of
    a `PARTNERS_WITH`. Reading without the swap splits one partnership into two
    rows carrying half the publishers each, above an evidence panel that matches
    both directions and reports the full count (ADR 0232).

    The ontology is editable over HTTP and a run executes in the API process,
    so the predicate set is read per call and never bound at import (C10).
    """
    if src > dst and predicate in get_ontology().symmetric_predicates:
        return dst, src
    return src, dst


# How many relation rows one card's neighbourhood may read. A hub entity sits on
# tens of thousands of relations and the card draws ten, so the bound is what
# keeps picking ten from costing the whole hub; `neighbour_assertions` orders
# newest assertion first, so the cut keeps the recent neighbourhood rather than
# whichever rows the heap returned.
NEIGHBOUR_ASSERTION_LIMIT = 5000

#: How much of a name's provenance one read returns. Generous, because the panel
#: is a single screen an operator scrolls and a chained merge legitimately has
#: dozens of surfaces -- but a bound, because both lists grow with the corpus and
#: a hub name has no natural ceiling. `EntityRecord` carries the true counts.
RECORD_LIMIT = 200


def _merge_map(conn: db.Conn, mappings: Mappings | None) -> Mappings:
    """The merge map, computed once per REQUEST rather than once per use.

    Every card resolves the id it was asked for, then its aliases, neighbours
    and signals across the whole closure, and `cards:batch` builds a brief's
    worth of cards in one call. Reading `entity_mappings` and collapsing its
    chains once per helper per card is the cost this parameter exists to remove.
    """
    if mappings is not None:
        return mappings
    from venona.graph.projector import resolve_mappings

    return resolve_mappings(conn)


def _entity_row(
    conn: db.Conn, entity_id: str, *, mappings: Mappings | None = None
) -> db.Row | None:
    """Follow merge chains, so a request for a merged-away id returns its
    SURVIVOR rather than a stale card.

    A merge resolves on READ and is never rewritten (ADR 0085), so every read
    path has to follow it: a merge visible in the graph and invisible in the
    hover card, search result and provenance panel is worse than not merging at
    all. `brief_blocks.spans` hold the raw ids a brief was written with, so this
    is also what repairs them without a rewrite.
    """
    resolved = _merge_map(conn, mappings).get(entity_id, entity_id)
    return repo.select_entities_by_id(conn, resolved).fetchone()


def _closure(mappings: dict[str, str], entity_id: str) -> set[str]:
    """Every raw id that resolves to `entity_id`'s survivor, itself included.

    Takes the already-computed map rather than the connection so a request that
    needs both the map and the closure pays for one traversal.
    """
    survivor = mappings.get(entity_id, entity_id)
    return {survivor} | {raw for raw, to in mappings.items() if to == survivor}


def _aliases(conn: db.Conn, entity_id: str, *, mappings: Mappings | None = None) -> list[str]:
    """Every surface this name goes by, across its whole merge closure.

    `merge_entities` does not re-point `aliases.entity_id` -- the merge is a row
    in `entity_mappings` -- and `graph/projector.py` folds the loser's surfaces
    onto the survivor when it projects. Reading the survivor's id alone lists
    fewer spellings than graph search finds for the same name, which is the
    graph-and-reader disagreement `_entity_row` exists to prevent.
    """
    members = _closure(_merge_map(conn, mappings), entity_id)
    return [r["surface"] for r in repo.alias_surfaces_for_members(conn, sorted(members))]


def _neighbors(
    conn: db.Conn, entity_id: str, *, curation: bool = False, mappings: Mappings | None = None
) -> tuple[list[MiniGraphNeighbor], int]:
    """Read neighbours from assertions rather than Neo4j.

    The card is on the hot path for every hover; going through Postgres keeps it
    a single local query and means the card still works if Neo4j is down.

    `curation` relaxes the active-only filter, for the reason `entity_card`
    gives.

    MERGE-AWARE ON BOTH SIDES. Assertions store the ids they were written with,
    so querying the survivor alone loses every neighbour that arrived via the
    loser -- merging would make a card *less* informative. The far endpoint is
    resolved too, because `status = 'active'` excludes the row a merge leaves
    behind as `merged`: a third entity with an edge to the loser would drop it
    silently rather than show the survivor (ADR 0175).
    """
    mappings = _merge_map(conn, mappings)
    members = _closure(mappings, entity_id)

    # (predicate, resolved other, direction) -> distinct supporting articles.
    grouped: dict[tuple[str, str, str], set[str]] = {}
    for row in repo.neighbour_assertions(conn, list(members), limit=NEIGHBOUR_ASSERTION_LIMIT):
        outgoing = row["src"] in members
        other_raw = row["dst"] if outgoing else row["src"]
        if other_raw is None:
            continue
        other_id = mappings.get(other_raw, other_raw)
        if other_id in members:
            continue  # both ends fold onto this entity; that is not a neighbour
        # A symmetric predicate is stored from both sides, so without this the
        # same neighbour arrives once as `out` and once as `in` -- two spokes to
        # one name, each at half its real weight.
        direction = "out" if outgoing else "in"
        if row["predicate"] in get_ontology().symmetric_predicates:
            direction = "out"
        key = (row["predicate"], other_id, direction)
        grouped.setdefault(key, set()).add(row["article_id"])

    # One read for every distinct neighbour rather than one per neighbour. A
    # hub entity has hundreds, and each was its own round trip.
    lookups = {
        row["id"]: row
        for row in repo.neighbour_entities(
            conn, sorted({other for _, other, _ in grouped}), active_only=not curation
        )
    }
    resolved: list[MiniGraphNeighbor] = []
    for (predicate, other_id, direction), articles in grouped.items():
        other = lookups.get(other_id)
        if other is None:
            continue
        resolved.append(
            MiniGraphNeighbor(
                id=other["id"],
                name=other["name"],
                type=other["type"],
                predicate=predicate,
                dir=direction,
                weight=float(len(articles) or 1) + float(other["relevance"] or 0),
            )
        )

    # Deterministic: weight desc, then id. Same card, same picture, every time.
    resolved.sort(key=lambda n: (-n.weight, n.id))
    return resolved[:MINI_GRAPH_LIMIT], max(0, len(resolved) - MINI_GRAPH_LIMIT)


def _signals(conn: db.Conn, entity_id: str, *, mappings: Mappings | None = None) -> list[Signal]:
    """Recent articles behind this entity, across its whole merge closure.

    Same reason as `_neighbors`: the survivor's id alone finds only the
    assertions written before the merge under that exact id.
    """
    members = _closure(_merge_map(conn, mappings), entity_id)
    return [
        Signal(
            article_id=r["id"],
            title=r["title"],
            url=r["url"],
            publisher=r["publisher"],
            published_at=r["published_at"],
            score=r["relevance_overall"],
        )
        for r in repo.signal_articles(conn, list(members), SIGNAL_LIMIT)
    ]


def entity_card(
    conn: db.Conn, entity_id: str, *, curation: bool = False, mappings: Mappings | None = None
) -> Card | None:
    """`curation=True` keeps non-active neighbours in the mini-graph.

    A conflict pair is usually two provisional entities whose neighbours are
    provisional too, so the default view -- correct for a reader, who must only
    see corroborated things -- renders an empty graph for both sides exactly
    when a curator is trying to tell them apart.
    """
    mappings = _merge_map(conn, mappings)
    row = _entity_row(conn, entity_id, mappings=mappings)
    if row is None:
        return None

    # The SURVIVOR's id, not the one asked for. `_entity_row` follows merge
    # chains, so passing `entity_id` on to the helpers below would give the
    # survivor's name and status carrying the LOSER's aliases, signals and
    # graph -- a card that belongs to neither entity. Only reachable by
    # requesting a merged-away id, which is precisely what a curation UI does
    # the instant after it merges something.
    resolved_id = row["id"]
    neighbors, more = _neighbors(conn, resolved_id, curation=curation, mappings=mappings)
    return Card(
        id=row["id"],
        kind="entity",
        type=row["type"],
        name=row["name"],
        expansion=row["expansion"],
        aliases=_aliases(conn, resolved_id, mappings=mappings),
        status=row["status"],
        # `entities.summary` is deliberately NOT read. Nothing writes it, and a
        # value there would carry no method, no article and no quote -- so
        # nothing downstream could tell a grounded definition from a guess.
        # ADR 0084.
        signals=_signals(conn, resolved_id, mappings=mappings),
        mini_graph=MiniGraph(center=resolved_id, neighbors=neighbors, more=more),
        neighbor_count=len(neighbors) + more,
        **_definition(conn, resolved_id),
    )


def _definition(conn: db.Conn, entity_id: str) -> dict[str, Any]:
    """The entity's grounded definition, as Card fields.

    Fills the fields a glossary card already fills -- `definition`, `method`,
    `confidence`, `grounding` -- so the three card kinds carry one shape and one
    component renders all of them, which is the whole point of C8's single
    payload discriminated by `kind` (ADR 0084).

    The caller passes an id `_entity_row` has already merge-resolved, so a
    merged-away id gets the survivor's definition rather than none.
    """
    row = repo.select_entity_definitions_by_entity_id(conn, entity_id).fetchone()
    if row is None:
        return {}

    grounding = None
    if row["grounding_quote"]:
        article = repo.select_documents_by_id(conn, row["grounding_article_id"]).fetchone()
        grounding = Grounding(
            article_id=row["grounding_article_id"],
            article_title=article["title"] if article else None,
            url=row["grounding_url"] or (article["url"] if article else None),
            quote=row["grounding_quote"],
        )

    # Tier 2 is a SYNTHESIS of several sentences, never a quotation, so it
    # carries article ids and no quote. The card renders "drawn from N
    # articles" off this rather than a blockquote -- presenting a written
    # sentence as a quote is the failure invariant 3 exists to prevent.
    ids = db.jload(row["source_article_ids"], []) or []
    sources: list[Signal] = []
    if ids:
        sources = [
            Signal(
                article_id=r["id"],
                title=r["title"],
                url=r["url"],
                publisher=r["publisher"],
                published_at=r["published_at"],
            )
            for r in repo.documents_by_ids(conn, list(ids))
        ]

    return {
        "definition": row["definition"],
        "method": row["method"],
        "confidence": row["confidence"],
        "grounding": grounding,
        "definition_sources": sources,
        "last_attempt_at": row["last_attempt_at"],
        "last_attempt_run_id": row["last_attempt_run_id"],
    }


def _col(row: db.Row, name: str):
    """Tolerate a row from a database that predates a column.

    `SELECT *` here means the shape follows the schema, so a card read against a
    database not yet migrated up to the attempt columns would raise rather than
    simply not knowing when it last tried.
    """
    return row[name] if name in row.keys() else None


def glossary_card(conn: db.Conn, gloss_id: str) -> Card | None:
    row = repo.select_acronym_definitions_by_id(conn, gloss_id).fetchone()
    if row is None:
        # Ambiguous surfaces are stored with a suffixed id per expansion, so the
        # bare id has no row; fall back to the surface.
        row = repo.select_acronym_definitions_by_normalized_2(
            conn, gloss_id.split(":", 1)[-1].replace("-", " ")
        ).fetchone()
        if row is None:
            return None

    alternatives: list[GlossaryAlternative] = []
    # `status != 'rejected'` -- a reading a curator threw out must not come back
    # as an "alternative" on the hover card. The row is kept for its evidence
    # ("why is FCC not a fire control center here" must still have an answer),
    # which is exactly why it has to be filtered on the way out rather than
    # deleted on the way in.
    siblings = repo.select_acronym_definitions_by_normalized(
        conn, row["normalized"], row["id"]
    ).fetchall()
    for sib in siblings:
        if not sib["expansion"]:
            continue
        alternatives.append(
            GlossaryAlternative(
                expansion=sib["expansion"],
                confidence=sib["confidence"],
                method=sib["method"],
                grounding=_grounding(conn, sib),
            )
        )

    undefined = row["status"] == "undefined" or not row["expansion"]
    return Card(
        id=row["id"],
        kind="undefined" if undefined else "glossary",
        surface=row["surface"],
        definition=row["expansion"],
        method=row["method"],
        confidence=row["confidence"],
        grounding=None if undefined else _grounding(conn, row),
        alternatives=alternatives,
        sighting_count=row["sighting_count"] or 0,
        last_attempt_at=_col(row, "last_attempt_at"),
        last_attempt_run_id=_col(row, "last_attempt_run_id"),
    )


def _grounding(conn: db.Conn, row: db.Row) -> Grounding | None:
    if not row["grounding_quote"] and not row["grounding_article_id"]:
        return None
    title = url = published = None
    if row["grounding_article_id"]:
        doc = repo.select_documents_by_id_2(conn, row["grounding_article_id"]).fetchone()
        if doc:
            title, url, published = doc["title"], doc["url"], doc["published_at"]
    return Grounding(
        quote=row["grounding_quote"],
        url=row["grounding_url"] or url,
        article_id=row["grounding_article_id"],
        article_title=title,
        published_at=published,
    )


def cards_batch(conn: db.Conn, ids: list[str]) -> tuple[dict[str, Card], list[str]]:
    """One round trip for every reference in a brief: called once on mount, and
    every hover after that is a client-side Map lookup (C8).

    The merge map is built ONCE here and threaded through every card, so a brief
    collapses merge chains once rather than once per helper per card -- and the
    same map for every card is also what keeps `/card` and `/neighbors` from
    disagreeing about an entity that was merged mid-request.
    """
    mappings = _merge_map(conn, None)
    cards: dict[str, Card] = {}
    missing: list[str] = []
    for ref in dict.fromkeys(ids):  # de-dupe, preserve order
        card = (
            glossary_card(conn, ref)
            if ref.startswith("gloss:")
            else entity_card(conn, ref, mappings=mappings)
        )
        if card is None:
            missing.append(ref)
        else:
            cards[ref] = card
    return cards, missing


def _ranked_neighbourhood(
    conn: db.Conn,
    entity_id: str,
    *,
    mappings: Mappings | None = None,
    types: set[str] | None = None,
    min_confidence: float = 0.0,
) -> tuple[str, list[tuple[tuple[str, str, str], dict[str, Any]]], dict[str, db.Row]]:
    """One entity's one-hop neighbourhood, aggregated and ranked, once.

    Split out so the graph explorer's node-and-edge shape and the entity page's
    flat connection list are two renderings of ONE traversal rather than two
    traversals that rank differently. Returns the resolved centre, the reachable
    edges in rank order, and the endpoint rows the caller needs to name them.
    """
    mappings = _merge_map(conn, mappings)
    members = _closure(mappings, entity_id)
    center = mappings.get(entity_id, entity_id)

    # Aggregate AFTER resolution: two pre-merge ids that now name one entity
    # are one edge, and grouping in SQL on the raw ids would emit both.
    aggregated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in repo.neighbourhood_assertions(conn, list(members), limit=NEIGHBOUR_ASSERTION_LIMIT):
        src = mappings.get(row["src"], row["src"])
        dst = mappings.get(row["dst"], row["dst"])
        if src == dst:
            continue  # a self-loop the merge created
        src, dst = _ordered(row["predicate"], src, dst)
        confidence = row["confidence"] or 0.5
        agg = aggregated.get((src, row["predicate"], dst))
        if agg is None:
            aggregated[(src, row["predicate"], dst)] = {
                "confidence": confidence,
                "articles": {row["article_id"]},
                "domains": {row["site_domain"]} if row["site_domain"] else set(),
                "first_seen": row["created_at"],
                "last_seen": row["created_at"],
            }
            continue
        agg["confidence"] = max(agg["confidence"], confidence)
        agg["articles"].add(row["article_id"])
        if row["site_domain"]:
            agg["domains"].add(row["site_domain"])
        agg["first_seen"] = min(agg["first_seen"], row["created_at"])
        agg["last_seen"] = max(agg["last_seen"], row["created_at"])

    # Rank by publishers first, articles second: one outlet running the same
    # story twice is not more corroborated than two outlets running it once
    # (ADR 0175). The last term is the whole key, which makes the order TOTAL;
    # the first four tie constantly, since most edges have one publisher and one
    # article, and a window over an ambiguous order disagrees with itself about
    # page two (docs/EXTENDING.md).
    ranked = sorted(
        (item for item in aggregated.items() if item[1]["confidence"] >= min_confidence),
        key=lambda item: (
            -len(item[1]["domains"]),
            -len(item[1]["articles"]),
            -item[1]["confidence"],
            item[0],
        ),
    )

    # Resolve the endpoints of EVERY ranked edge, not just the first page, so
    # the drop below happens before the slice. Filtering after slicing counts
    # edges into `total` that no `limit` can ever reveal -- an endpoint held
    # back for being provisional is absent at every page size -- and the footer
    # then tells the reader to raise a limit that changes nothing (ADR 0175).
    endpoints: set[str] = {center}
    for (src, _, dst), _agg in ranked:
        endpoints.add(src)
        endpoints.add(dst)

    known: dict[str, db.Row] = {}
    for node in repo.active_nodes_by_ids(conn, list(endpoints)):
        if types and node["type"] not in types:
            continue
        known[node["id"]] = node

    reachable = [item for item in ranked if item[0][0] in known and item[0][2] in known]
    return center, reachable, known


def neighborhood(
    conn: db.Conn,
    entity_id: str,
    *,
    limit: int = 25,
    types: set[str] | None = None,
    min_confidence: float = 0.0,
):
    """One-hop expansion for the graph explorer.

    Hard-capped and always ordered: an unbounded traversal on a hub like
    `govt:defense-department` would hang the UI.

    Resolved through `entity_mappings` exactly as `_neighbors` is, and for the
    same reason: a path that does not follow merges makes `/neighbors` and
    `/card` answer differently about the same id, one showing the pre-merge world
    and the other the post-merge one.
    """
    from venona.api.schemas.entity import GraphEdge, GraphNode, Neighborhood

    center, reachable, known = _ranked_neighbourhood(
        conn, entity_id, types=types, min_confidence=min_confidence
    )
    total = len(reachable)

    node_ids: set[str] = {center} & known.keys()
    edges: list[GraphEdge] = []
    for (src, predicate, dst), agg in reachable[:limit]:
        node_ids.add(src)
        node_ids.add(dst)
        edges.append(
            GraphEdge(
                id=f"{src}|{predicate}|{dst}",
                source=src,
                target=dst,
                predicate=predicate,
                # `or 1` matches `edge_provenance`: an edge whose only document
                # carries no domain is still one sighting, not zero.
                source_count=len(agg["domains"]) or 1,
                confidence=agg["confidence"],
                first_seen=agg["first_seen"],
                last_seen=agg["last_seen"],
            )
        )

    nodes = [
        GraphNode(
            id=known[node_id]["id"],
            name=known[node_id]["name"],
            type=known[node_id]["type"],
            relevance=known[node_id]["relevance"] or 0.0,
            status=known[node_id]["status"],
        )
        for node_id in sorted(node_ids)
    ]

    return Neighborhood(nodes=nodes, edges=edges, returned=len(edges), total=total)


def search(conn: db.Conn, query: str, *, limit: int = 10, include_provisional: bool = False):
    """Alias-aware prefix search. Backs the path finder inputs.

    `include_provisional` is OFF by default and must stay that way for the
    palette and the path finder: a provisional entity is one the resolver has
    seen once and not yet corroborated, and offering those as destinations
    would fill the finder with noise nothing can navigate to.

    The definitions queue's "this surface is an entity" picker turns it on,
    because the entity it most needs to find is usually provisional -- that is
    the whole reason the surface never resolved. Measured 2026-08-09: `BAE
    Systems` is provisional in this corpus while two of its subsidiaries are
    active, so an active-only search offers the subsidiaries and hides the
    company.

    `status` is returned whenever provisionals are admitted, so the caller can
    show what it is about to promote rather than promoting it silently.
    """
    like = f"%{query.lower()}%"
    # A query of pure punctuation normalises to nothing, and `%%` would match
    # every alias in the corpus, so it falls back to the literal pattern.
    normalized = normalize(query)
    like_normalized = f"%{normalized}%" if normalized else like
    statuses = ("active", "provisional") if include_provisional else ("active",)
    rows = [
        dict(r)
        for r in repo.search_entities(
            conn, like=like, like_normalized=like_normalized, statuses=statuses, limit=limit
        )
    ]
    if not include_provisional:
        # Unchanged shape for every existing caller.
        for row in rows:
            row.pop("status", None)
    return rows


#: How deep a reader may page into one name's connections. Past this the answer
#: is a FILTER, not a bigger window: the traversal behind a page is the whole
#: neighbourhood, so offset 5000 costs exactly what offset 0 costs and returns
#: rows nobody scrolled to. `predicate` and `type` narrow instead.
CONNECTION_OFFSET_CEILING = 200


def connections(
    conn: db.Conn,
    entity_id: str,
    *,
    limit: int = 25,
    offset: int = 0,
    predicate: str | None = None,
    types: set[str] | None = None,
    mappings: Mappings | None = None,
):
    """One name's connections as a FLAT list, for the entity page.

    `neighborhood` answers with nodes and edges because a canvas needs both.
    A list needs one row per connection with the other name already on it, and
    the two envelopes are not interchangeable: paging `{nodes, edges}` hands the
    client page two's edges with page one's nodes missing, so it has to
    accumulate a node map by hand. `{items, total}` is the convention every
    other paged route in this app returns (docs/EXTENDING.md) and the shape
    `lib/paging.ts::createPagedList` consumes.

    `edge_id` rides on every row. The client must not build it: an id assembled
    as `thisEntity|PREDICATE|other` is backwards for every incoming edge, and
    wrong for anything reached through a merged-away id.
    """
    from venona.api.schemas.entity import Connection, EntityConnections

    center, reachable, known = _ranked_neighbourhood(
        conn, entity_id, mappings=mappings, types=types
    )
    if predicate:
        reachable = [item for item in reachable if item[0][1] == predicate]

    total = len(reachable)
    items: list[Connection] = []
    for (src, pred, dst), agg in reachable[offset : offset + limit]:
        outgoing = src == center
        other = known.get(dst if outgoing else src)
        if other is None:
            continue
        items.append(
            Connection(
                edge_id=f"{src}|{pred}|{dst}",
                predicate=pred,
                direction="out" if outgoing else "in",
                other_id=other["id"],
                other_name=other["name"],
                other_type=other["type"],
                # Distinct PUBLISHERS, and `or 1` as in `neighborhood` above:
                # an edge whose only document carries no domain is one sighting.
                publisher_count=len(agg["domains"]) or 1,
                confidence=agg["confidence"],
                first_seen=agg["first_seen"],
                last_seen=agg["last_seen"],
            )
        )
    return EntityConnections(items=items, total=total)


def sources(
    conn: db.Conn,
    entity_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
    mappings: Mappings | None = None,
):
    """The articles behind one name, paged, across its whole merge closure.

    The card carries four of these and the page carries all of them, which is
    the only difference between this and `_signals`.
    """
    from venona.api.schemas.entity import EntitySources

    members = sorted(_closure(_merge_map(conn, mappings), entity_id))
    rows = repo.signal_articles_page(conn, members, limit=limit, offset=offset)
    return EntitySources(
        items=[
            Signal(
                article_id=r["id"],
                title=r["title"],
                url=r["url"],
                publisher=r["publisher"],
                published_at=r["published_at"],
                score=r["relevance_overall"],
            )
            for r in rows
        ],
        total=repo.count_signal_articles(conn, members),
    )


def record(conn: db.Conn, entity_id: str, *, mappings: Mappings | None = None):
    """Where a name came from: the merges behind it, and every surface's origin.

    Admin only, and it is the one read that deliberately shows the seams. A
    reader is told what Venona knows; whoever runs the domain also has to be
    able to see WHY it knows it, which is a different question and the only one
    that can be answered when two names turn out to have been one.

    Built over the merge CLOSURE, not `to_id = <survivor>`, so that a chained
    merge accounts for every name it absorbed rather than the last one; the
    query says why.
    """
    from venona.api.schemas.entity import AliasOrigin, EntityRecord, MergedName

    mappings = _merge_map(conn, mappings)
    row = _entity_row(conn, entity_id, mappings=mappings)
    if row is None:
        return None
    resolved_id = row["id"]
    members = sorted(_closure(mappings, resolved_id))

    merged = [
        MergedName(
            id=m["from_id"],
            name=m["from_name"] or m["from_id"],
            type=m["from_type"],
            reason=m["reason"],
            merged_at=m["created_at"],
            merged_by=m["merged_by_email"],
        )
        for m in repo.merges_for_members(conn, members, limit=RECORD_LIMIT)
        if m["from_id"] != resolved_id
    ]
    aliases = [
        AliasOrigin(
            surface=a["surface"],
            strictness=a["strictness"],
            is_acronym=bool(a["is_acronym"]),
            added_by=a["added_by"],
            article_id=a["evidence_article_id"],
            article_title=a["article_title"],
            article_url=a["article_url"],
        )
        for a in repo.aliases_with_provenance(conn, members, limit=RECORD_LIMIT)
    ]
    totals = repo.record_totals(conn, members)
    return EntityRecord(
        id=resolved_id,
        merged_from=merged,
        aliases=aliases,
        merged_from_total=totals["merges"],
        aliases_total=totals["aliases"],
    )
