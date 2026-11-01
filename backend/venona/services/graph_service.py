"""Edge provenance, read from the assertion log.

Provenance comes from Postgres rather than Neo4j deliberately: the assertion log
is the system of record and carries the verbatim evidence quote, while the graph
edge only carries a capped list of article ids. If they ever disagree, Postgres is
right.

This is a first-class surface, not a debug view. An intelligence graph whose
edges cannot be audited is a rumour mill -- the reason to trust
`PEO C3N —MANAGES→ NGC2` is being able to read the sentence that asserted it.
"""

from __future__ import annotations

from typing import Any

from venona.db import core as db
from venona.db.models import DECLARING_METHODS
from venona.db.repositories import graph as repo
from venona.graph.projector import resolve_mappings
from venona.ontology import get_ontology


def _closure(mappings: dict[str, str], entity_id: str) -> set[str]:
    """Every raw id resolving to `entity_id`'s survivor, from a map already read.

    `curate.merge_closure` is this plus the read. Taking the map lets one
    request resolve both ends of an edge against one traversal.
    """
    survivor = mappings.get(entity_id, entity_id)
    return {survivor} | {raw for raw, to in mappings.items() if to == survivor}


def parse_edge_id(edge_id: str) -> tuple[str, str, str] | None:
    """`source|PREDICATE|target`. Deterministic, so the frontend can address an
    edge without a server round trip.

    The whitelist is read per call, never bound at import: the ontology is
    editable from Settings, so a snapshot here would 404 provenance for an edge
    the graph had just started serving.
    """
    parts = edge_id.split("|")
    if len(parts) != 3:
        return None
    source, predicate, target = parts
    if predicate not in get_ontology().predicate_names:
        return None
    return source, predicate, target


def _is_declared(row: db.Row) -> bool:
    """A person stated this, rather than an article reporting it.

    The same test `graph/projector.py` applies, so the panel and the canvas
    cannot disagree about whether an edge is thinly sourced.
    """
    return row["method"] in DECLARING_METHODS and not row["site_domain"]


#: How much evidence one edge may return. Set well above any real edge -- the
#: busiest in this corpus carries low tens -- because `source_count` is computed
#: from these rows, so a cut that bites would understate corroboration rather
#: than just shorten a list.
EDGE_ASSERTION_LIMIT = 500


def edge_provenance(conn: db.Conn, edge_id: str) -> dict[str, Any] | None:
    parsed = parse_edge_id(edge_id)
    if parsed is None:
        return None
    source, predicate, target = parsed

    # Both endpoints as their merge closures, because an edge_id names one id
    # and the assertions behind it may have been written against several. The
    # projection collapses a merged node, so the edge exists under the survivor
    # while half its evidence still carries the loser's id.
    #
    # ONE map for both ends. `merge_closure` reads `entity_mappings` whole and
    # walks it to a fixed point, so calling it per endpoint paid for that twice
    # on a route the entity page hits once per expanded connection.
    mappings = resolve_mappings(conn)
    sources = sorted(_closure(mappings, source))
    targets = sorted(_closure(mappings, target))

    # Symmetric edges are stored once in lexicographic order, so a click on
    # either direction must find the same assertions.
    rows = repo.select_assertions_by_predicate_subject_id_raw(
        conn, predicate, sources, targets, limit=EDGE_ASSERTION_LIMIT
    ).fetchall()

    if not rows:
        return None

    endpoints = {}
    for entity_id in (source, target):
        row = repo.select_entities_by_id(conn, entity_id).fetchone()
        endpoints[entity_id] = dict(row) if row else {"id": entity_id, "name": entity_id}

    assertions = [
        {
            "assertion_id": r["id"],
            "article_id": r["article_id"],
            "quote": r["evidence_quote"],
            "confidence": r["confidence"],
            "method": r["method"],
            "model": r["model"],
            "title": r["title"],
            "url": r["url"],
            "publisher": r["publisher"],
            "published_at": r["published_at"],
        }
        for r in rows
    ]

    briefs = [
        dict(r)
        for r in repo.query_briefs_by_predicate_subject_id_raw(conn, predicate, sources, targets)
    ]

    # Distinct DOMAINS, not distinct publishers. `documents.publisher` is a
    # display byline that can come from the page's own `og:site_name`, so it is
    # neither stable per outlet nor unique to one -- counting it made a single
    # outlet corroborate itself and two outlets collapse into one. The writer
    # counts the same thing the same way; if these two ever diverge again the
    # canvas draws solid what this panel calls single-source.
    sources = {r["site_domain"] for r in rows if r["site_domain"]}

    return {
        "edge_id": edge_id,
        "predicate": predicate,
        "source": endpoints[source],
        "target": endpoints[target],
        "source_count": len(sources) or 1,
        "assertion_count": len(assertions),
        # A DECLARED edge names no article, so it has no domain to count and
        # would report as single-source -- a claim about publishers that nobody
        # made. Its quote already says it was declared rather than read, so the
        # panel must not also call it thinly sourced.
        "declared": any(_is_declared(r) for r in rows),
        # A single-source edge is drawn dashed. Surfacing the flag here means
        # the panel and the canvas agree on epistemic strength.
        "single_source": len(sources) <= 1 and not any(_is_declared(r) for r in rows),
        "assertions": assertions,
        "briefs": briefs,
    }


def describe_path(conn: db.Conn, node_ids: list[str], edges: list[dict]) -> str:
    """Render a path as a readable chain.

    `NGC2 —MANAGES← PEO C3N —PART_OF→ US Army`

    The arrow direction is what makes it readable: it tells you whether the
    relationship runs with or against the way you are walking the path.
    """
    if not node_ids:
        return ""

    by_pair: dict[tuple[str, str], dict] = {}
    for edge in edges:
        by_pair[(edge["source"], edge["target"])] = edge

    names = {}
    for row in repo.entity_names(conn, list(node_ids)):
        names[row["id"]] = row["name"]

    parts = [names.get(node_ids[0], node_ids[0])]
    for left, right in zip(node_ids, node_ids[1:], strict=False):
        forward = by_pair.get((left, right))
        backward = by_pair.get((right, left))
        if forward:
            parts.append(f"—{forward['predicate']}→")
        elif backward:
            parts.append(f"←{backward['predicate']}—")
        else:
            parts.append("—?—")
        parts.append(names.get(right, right))

    return " ".join(parts)
