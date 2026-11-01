"""Replay assertions -> Neo4j. This is what makes decision 2 real.

The graph is never written directly from the pipeline; it is *derived* by
replaying the immutable `assertions` table through the currently-active entity
mappings. Consequences worth stating plainly:

  * A merge is a mapping row plus a rebuild, not destructive Cypher surgery.
  * An unmerge is `active = 0` plus a rebuild. Genuinely reversible.
  * Re-running a day is idempotent by reconstruction, not by hoping the MERGE
    statements were written correctly.
  * Scoring can be retuned and the graph re-derived with zero model spend.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from neo4j import AsyncDriver

from venona.db import core as db
from venona.db.models import DECLARING_METHODS
from venona.db.repositories import graph as graph_repo
from venona.db.repositories import projector as repo
from venona.graph.schema import apply_schema, drop_all
from venona.graph.writer import EdgeRow, EntityRow, GraphWriter
from venona.ontology import get_ontology

log = logging.getLogger("venona.graph")


#: Collapsed chains per database, guarded by the stamp over the rows they were
#: collapsed from. Keyed on `conn.dsn` and size-capped for the reason
#: `corpus_check._AUDIT_CACHE` records: the suite opens hundreds of throwaway
#: databases per process and a frozen clock makes their stamps alike.
_MAPPING_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, str]]] = {}
_MAPPING_CACHE_MAX = 8


def resolve_mappings(conn: db.Conn) -> dict[str, str]:
    """Collapse active merge chains to their final survivor.

    Chains happen: A merged into B, later B merged into C. Following them to a
    fixed point means the projection never emits a node that was merged away.

    Memoised because the read path asks far more often than the write path
    answers: the merge queue resolves a pair per cursor move, so a curator
    holding an arrow key re-read and re-collapsed the whole table on every card.
    The stamp is what makes that safe -- see `select_mapping_fingerprint` for why
    it takes three fields rather than a row count.

    The returned dict is shared with the next caller. Nothing mutates it today,
    and nothing should start.
    """
    stamp = repo.select_mapping_fingerprint(conn).fetchone()
    fingerprint = (stamp["total"], stamp["live"], stamp["newest"])
    cached = _MAPPING_CACHE.get(conn.dsn)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    direct: dict[str, str] = {}
    for row in repo.select_entity_mappings(conn):
        direct[row["from_id"]] = row["to_id"]

    resolved: dict[str, str] = {}
    for start in direct:
        seen = {start}
        current = start
        while current in direct:
            current = direct[current]
            if current in seen:  # cycle guard; a bad merge must not hang a rebuild
                break
            seen.add(current)
        resolved[start] = current

    if conn.dsn not in _MAPPING_CACHE and len(_MAPPING_CACHE) >= _MAPPING_CACHE_MAX:
        _MAPPING_CACHE.pop(next(iter(_MAPPING_CACHE)))
    _MAPPING_CACHE[conn.dsn] = (fingerprint, resolved)
    return resolved


def _entity_rows(conn: db.Conn, mappings: dict[str, str]) -> dict[str, list[EntityRow]]:
    # Fold the loser's aliases onto the SURVIVOR, exactly as the edge pass does
    # a few lines down. Keyed by raw `entity_id` a merged-away entity takes its
    # aliases with it, so after `DHS` merges into `Department of Homeland
    # Security` the surviving node does not carry "DHS" and `/graph/search?q=DHS`
    # stops finding a node that WAS findable before the merge.
    #
    # `alias_index.py` uses `status IN ('active','merged')` for annotation; the
    # projection has to match, or hover cards keep working while graph search
    # quietly regresses.
    aliases: dict[str, list[str]] = defaultdict(list)
    for row in repo.select_aliases(conn):
        owner = mappings.get(row["entity_id"], row["entity_id"])
        if row["surface"] not in aliases[owner]:
            aliases[owner].append(row["surface"])

    # A type absent from the ontology does not project -- the same retirement
    # semantics predicates have. Its assertions stay on disk and return if the
    # type is re-added; `prune()` removes the orphan nodes on the next
    # projection.
    known_types = get_ontology().type_names

    by_type: dict[str, list[EntityRow]] = defaultdict(list)
    skipped_types: set[str] = set()
    for row in repo.select_entities_for_projection(conn):
        if row["type"] not in known_types:
            skipped_types.add(row["type"])
            continue
        entity_id = mappings.get(row["id"], row["id"])
        if entity_id != row["id"]:
            continue  # merged away; its assertions land on the survivor
        by_type[row["type"]].append(
            EntityRow(
                id=entity_id,
                type=row["type"],
                name=row["name"],
                canonical_key=row["canonical_key"],
                status=row["status"],
                aliases=aliases.get(row["id"], []),
                summary=row["summary"],
                expansion=row["expansion"],
                org_type=row["org_type"],
                relevance=row["relevance"] or 0.0,
                mention_count=row["mention_count"] or 0,
                first_seen=row["first_seen"],
                last_seen=row["last_seen"],
            )
        )
    if skipped_types:
        log.info("projection skipping retired entity type(s): %s", sorted(skipped_types))
    return by_type


def _edge_rows(conn: db.Conn, mappings: dict[str, str]) -> dict[str, list[EdgeRow]]:
    by_predicate: dict[str, list[EdgeRow]] = defaultdict(list)
    # Read per call, not at import, for the reason `graph/writer.py` gives. A
    # UI-triggered run executes in the API process -- so an import-time snapshot
    # would drop a newly added predicate here, BEFORE the writer ever sees it,
    # while the model's schema enum (regenerated live) happily produced it.
    predicates = get_ontology().predicate_names
    # `d.site_domain` rides along so the writer can count distinct SOURCES
    # rather than distinct articles. The DOMAIN, not `d.publisher`: the byline
    # is page-controlled and not 1:1 with the outlet -- see `EdgeRow`.
    # LEFT JOIN: an assertion whose document row is gone must still project its
    # edge.
    for row in repo.select_assertions(conn):
        predicate = row["predicate"]
        if predicate not in predicates:
            continue  # a predicate retired from the whitelist simply stops projecting
        by_predicate[predicate].append(
            EdgeRow(
                src=mappings.get(row["subject_id_raw"], row["subject_id_raw"]),
                dst=mappings.get(row["object_id_raw"], row["object_id_raw"]),
                article_id=row["article_id"],
                confidence=row["confidence"] or 0.5,
                observed_at=row["created_at"],
                kind=row["predicate_kind"],
                source_domain=row["site_domain"],
                # DECLARED is "names no readable source", not "was written by
                # the anchor loader". `fixtures/seed/edges.yaml` carries a real
                # `evidence:` on every row and `load_seed` writes that document,
                # so those edges cite an article and are thinly sourced in the
                # ordinary way. `profile/anchors.yaml` cites nothing -- and
                # neither does a curator accepting a placement, whose assertion
                # falls back to `doc:curated`. Both are people stating a fact.
                declared=row["method"] in DECLARING_METHODS and not row["site_domain"],
            )
        )
    return by_predicate


def _article_rows(conn: db.Conn) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "props": {
                "url": row["url"],
                "title": row["title"],
                "publisher": row["publisher"],
                "published_at": row["published_at"],
                "site_domain": row["site_domain"],
                "cluster_id": row["cluster_id"],
                "relevance_overall": row["relevance_overall"],
                "tier": row["tier"],
            },
        }
        for row in repo.select_documents(conn)
    ]


def _mention_rows(conn: db.Conn, mappings: dict[str, str]) -> list[dict[str, Any]]:
    """One row per (article, SURVIVING entity), folded on the highest salience.

    THE COLLAPSE IS IN PYTHON, so an ORDER BY on the read cannot fix it. Two
    assertions naming ids that a merge has since folded together are two rows
    here for one pair, and `link_mentions` MERGEs on that pair and SETs
    `salience` -- so the last row written wins and the mention's salience is
    decided by which of the two the query returned second. `upsert_edges`
    already folds its confidence with `max` for exactly this reason; this is
    the same reducer on the same shape.
    """
    folded: dict[tuple[str, str], dict[str, Any]] = {}
    for row in repo.select_assertions_2(conn):
        entity_id = mappings.get(row["entity_id_raw"], row["entity_id_raw"])
        key = (row["article_id"], entity_id)
        salience = row["salience"] or 0.0
        current = folded.get(key)
        if current is None:
            folded[key] = {
                "article_id": row["article_id"],
                "entity_id": entity_id,
                "salience": salience,
                "observed_at": row["created_at"],
            }
            continue
        current["salience"] = max(current["salience"], salience)
        # The NEWEST sighting, so `last_seen` means what it says.
        current["observed_at"] = max(current["observed_at"], row["created_at"])
    return list(folded.values())


class ProjectionBusy(Exception):
    """Another process is projecting. Raised only when the caller said `wait=False`."""


async def project(
    conn: db.Conn,
    driver: AsyncDriver,
    *,
    rebuild: bool = False,
    wait: bool = True,
) -> dict[str, int]:
    """Project Postgres state into Neo4j.

    Every call is a full replay, and it ends with a `prune` that deletes
    whatever the replay did not write. That is what keeps the graph a
    *projection*: retract an assertion or merge an entity in curation, and the
    edge or node goes away on the next projection rather than lingering until
    someone thinks to rebuild.

    `rebuild=True` truncates first. With the prune in place it is not the only
    way to converge; it stays as the backstop for every idempotency question --
    if you ever doubt the graph, rebuild it and compare.

    ONE AT A TIME, ACROSS PROCESSES. Every pass stamps what it writes and then
    deletes everything not carrying its own stamp. Two overlapping passes
    therefore delete each other's work: the one that finishes second prunes
    against its own stamp, and whatever the first wrote in the meantime is gone.
    The result is a graph missing edges that Postgres still implies -- recoverable
    (invariant 1) but silent, because nothing compares the two. The gate is a
    Postgres advisory lock rather than the run registry because the CLI is a
    separate process and the registry is an `asyncio.Lock` in one event loop.

    `wait=False` refuses instead of queueing, for a caller with a person waiting
    on the answer.
    """
    if not graph_repo.lock_projection(conn, wait=wait):
        raise ProjectionBusy
    try:
        return await _project(conn, driver, rebuild=rebuild)
    finally:
        graph_repo.unlock_projection(conn)


async def _project(
    conn: db.Conn,
    driver: AsyncDriver,
    *,
    rebuild: bool = False,
) -> dict[str, int]:
    if rebuild:
        await drop_all(driver)
    await apply_schema(driver)

    mappings = resolve_mappings(conn)
    writer = GraphWriter(driver, projection=await GraphWriter.begin_projection(driver))

    counts = {
        "entities": 0,
        "edges": 0,
        "articles": 0,
        "mentions": 0,
        "rejected": 0,
        "pruned": 0,
    }

    for entity_type, rows in _entity_rows(conn, mappings).items():
        counts["entities"] += await writer.upsert_entities(entity_type, rows)

    counts["articles"] += await writer.upsert_articles(_article_rows(conn))

    # Entity id -> type, so the writer can enforce ontology domain/range.
    types = {row["id"]: row["type"] for row in repo.select_entities(conn)}
    for predicate, rows in _edge_rows(conn, mappings).items():
        counts["edges"] += await writer.upsert_edges(predicate, rows, types=types)

    counts["rejected"] = len(writer.rejected)
    if writer.rejected:
        preview = "; ".join(writer.rejected[:5])
        log.warning("ontology rejected %d edge(s): %s", len(writer.rejected), preview)

    counts["mentions"] += await writer.link_mentions(_mention_rows(conn, mappings))

    # Last, and only on a pass that got this far.
    counts["pruned"] = sum((await writer.prune()).values())

    return counts
