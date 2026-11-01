"""The only place Cypher MERGE is written.

Centralising it is what makes the predicate whitelist enforceable: the analyze
schema restricts predicates with a JSON Schema enum, and this module re-checks
before writing. Two independent gates, because a model that invents a predicate
would otherwise silently create an un-queryable edge type.

Idempotency is the other reason this is one module. Re-running a day must be a
strict no-op on counters. That is achieved by ASSIGNMENT, not accumulation:
the projector replays the complete non-retracted assertion set on every call,
so `mention_count` and `evidence` are computed from that batch and written
over whatever was there. Do NOT increment them in Cypher guarded by
`article_id IN e.evidence`: `evidence` is capped at ten ids, so any edge with
more support than that grows by its whole width on every projection.

Assignment means assignment, including for the nullable properties. A
`coalesce(row.x, e.x)` on `e.kind` or `m.salience` is accumulation wearing a
different hat: retract the one assertion carrying `predicate_kind = 'award'`
and the batch stops implying it while the coalesce goes on serving it until a
full rebuild. A property Postgres stops implying must go to null on the next
ordinary projection.

Every element this module writes is stamped with the projection's sequence
number, which is what `prune` uses to delete anything Postgres has stopped
implying -- a retracted assertion's edge, a merged-away entity's node.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from neo4j import AsyncDriver

from venona.ontology import get_ontology

# A type name becomes a Cypher LABEL below, and labels cannot be parameterised
# -- so with the type set HTTP-editable, the name is attacker-adjacent text
# reaching query source. `parse_ontology` rejects anything outside this shape at
# save time; this is the second gate, at the interpolation site, the same
# arrangement predicates have.
TYPE_LABEL = re.compile(r"^[A-Z][A-Za-z0-9]*$")

# How many supporting article ids an edge carries for display. Deliberately
# small: `mention_count` is the number that has to be complete, and it is
# counted from the batch rather than from this list.
EVIDENCE_LIMIT = 10


@dataclass
class EntityRow:
    id: str
    type: str
    name: str
    canonical_key: str
    status: str
    aliases: list[str]
    summary: str | None = None
    expansion: str | None = None
    org_type: str | None = None
    relevance: float = 0.0
    mention_count: int = 0
    first_seen: str = ""
    last_seen: str = ""

    def to_params(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "observed_at": self.last_seen,
            "aliases": self.aliases,
            "aliases_text": " ".join(self.aliases),
            "props": {
                "type": self.type,
                "name": self.name,
                "canonical_key": self.canonical_key,
                "status": self.status,
                "summary": self.summary,
                "expansion": self.expansion,
                "org_type": self.org_type,
                "relevance": self.relevance,
                "mention_count": self.mention_count,
                "first_seen": self.first_seen,
            },
        }


@dataclass
class EdgeRow:
    src: str
    dst: str
    article_id: str
    confidence: float
    observed_at: str
    kind: str | None = None
    # The SITE DOMAIN of `article_id`. Distinct domains across the surviving
    # evidence is `source_count` -- the number the UI calls "sources" and draws
    # a dashed edge for when it is one. See `upsert_edges`.
    #
    # The domain, NOT `documents.publisher`. The byline is page-controlled -- it
    # can come from a page's own `og:site_name` -- and measured 2026-08-16 over
    # the recorded corpus it is 1:1 with the outlet in neither direction:
    # `darpa.mil` yielded two publisher strings, and `Defense One` was yielded by
    # two domains. Neither failure is visible: one outlet counted as two draws a
    # solid corroborated edge from a single report, and two outlets collapsing to
    # one draws a dashed edge and is dropped outright at `min_source_count=2`.
    source_domain: str | None = None
    # A DECLARATION rather than a reading: `method='seed'`, from
    # `profile/anchors.yaml`. It names no article, so it has no domain to count
    # and `source_count` falls back to one -- which reads as "a single publisher
    # said so" and is false. The edge carries this instead, and the read path
    # exempts it from the corroboration floor rather than dropping the trunk.
    declared: bool = False


#: How many rows one UNWIND carries.
#:
#: An UNWIND is ONE statement in ONE transaction, and Neo4j builds the whole
#: parameter list in the server's heap before the first row is written -- so an
#: unbatched projection's memory cost is the size of the corpus rather than of a
#: write. That is a cliff and not a slope: the projection either completes or
#: dies, and it dies on the largest corpus, which is to say on the instance that
#: has been running longest.
#:
#: Bounded rather than tuned. Each chunk is its own transaction, safe because a
#: projection is a full replay under one projection id: a pass that dies half
#: way leaves rows carrying an older stamp for the next pass to overwrite and
#: prune. `prune` runs only after every chunk has landed, so a partial pass
#: deletes nothing.
BATCH_SIZE = 5_000


def _chunks(rows: list[Any], size: int = BATCH_SIZE) -> Iterator[list[Any]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


class GraphWriter:
    def __init__(self, driver: AsyncDriver, *, projection: int | None = None) -> None:
        self._driver = driver
        # Stamped onto everything written. `project()` obtains one from
        # `begin_projection`; a writer built outside a projection leaves it None,
        # and `prune` refuses rather than deleting against a missing stamp.
        self._projection = projection
        # Ontology violations found during a projection. Surfaced by the caller
        # rather than raised, so one bad assertion cannot abort a rebuild.
        self.rejected: list[str] = []

    @staticmethod
    async def begin_projection(driver: AsyncDriver) -> int:
        """Claim the next projection sequence number.

        A counter in the graph rather than a wall-clock timestamp: `graph/` may
        not read the system clock (offline determinism), and a value derived
        from graph state replays identically.
        """
        async with driver.session() as session:
            result = await session.run(
                """
                MERGE (m:ProjectionMeta {id: 'singleton'})
                  ON CREATE SET m.seq = 0
                SET m.seq = m.seq + 1
                RETURN m.seq AS seq
                """
            )
            record = await result.single()
        return int(record["seq"]) if record else 1

    async def upsert_entities(self, entity_type: str, rows: list[EntityRow]) -> int:
        if not rows:
            return 0
        # Read per call, not at import: the ontology is HTTP-editable and a
        # UI-triggered run executes in this same process, so a type saved a
        # moment ago must be writable without a restart.
        if entity_type not in get_ontology().type_names:
            raise ValueError(f"unknown entity type {entity_type!r}")
        label = entity_type
        if not TYPE_LABEL.fullmatch(label):
            raise ValueError(f"entity type {label!r} is not a legal label shape")
        query = f"""
        UNWIND $rows AS row
        MERGE (n:Entity:{label} {{id: row.id}})
          ON CREATE SET n.created_at = datetime(), n.first_seen = row.observed_at
        SET n += row.props,
            n.updated_at   = datetime(),
            n.projection   = $projection,
            n.aliases      = row.aliases,
            n.aliases_text = row.aliases_text,
            n.last_seen    = CASE
              WHEN row.observed_at > coalesce(n.last_seen, row.observed_at)
              THEN row.observed_at ELSE n.last_seen END
        """
        params = [r.to_params() for r in rows]
        async with self._driver.session() as session:
            for chunk in _chunks(params):
                await session.run(query, rows=chunk, projection=self._projection)
        return len(rows)

    async def upsert_edges(
        self,
        predicate: str,
        rows: list[EdgeRow],
        *,
        types: dict[str, str] | None = None,
    ) -> int:
        """Write edges for one predicate.

        `rows` must be the COMPLETE surviving evidence for `predicate` -- every
        non-retracted assertion, not a delta. `mention_count`, `source_count`
        and `evidence` are derived from the batch and assigned, so a partial
        batch would under-report rather than merely fail to grow.

        `types` maps entity id -> entity type. When supplied, every row is
        checked against the ontology's declared domain and range. That is the
        check membership alone cannot do: `Anduril --ACQUIRED--> Lattice` uses a
        real predicate with the wrong operand types, and only domain/range
        catches it.
        """
        if not rows:
            return 0
        # Read per call for the reason `upsert_entities` gives: an import-time
        # snapshot would reject a predicate the model was just told to use.
        ontology = get_ontology()
        if predicate not in ontology.predicate_names:
            raise ValueError(
                f"predicate {predicate!r} is not in the ontology -- "
                f"add it to config/ontology.yaml (it regenerates the model schema, "
                f"this whitelist, and docs/graph-schema.md)"
            )

        if types:
            rejected: list[str] = []
            kept: list[EdgeRow] = []
            for row in rows:
                subject_type = types.get(row.src)
                object_type = types.get(row.dst)
                if subject_type is None or object_type is None:
                    kept.append(row)  # unknown endpoint; the MATCH will drop it
                    continue
                problem = ontology.check_relation(predicate, subject_type, object_type)
                if problem:
                    rejected.append(f"{row.src} -> {row.dst}: {problem}")
                else:
                    kept.append(row)
            if rejected:
                # Loud but non-fatal: one badly-typed assertion must not abort a
                # projection, but it must never silently enter the graph either.
                self.rejected.extend(rejected)
            rows = kept
            if not rows:
                return 0

        # Collapse the assertion stream to one row per edge before writing.
        #
        # This is what fixes the runaway counter. Incrementing in Cypher --
        # `e.mention_count + CASE WHEN row.article_id IN e.evidence THEN 0 ELSE
        # 1 END` -- asks a list capped at ten article ids whether it has seen
        # this one, so an edge supported by twelve articles finds two of them
        # "new" on every projection and grows forever. `mention_count` orders
        # every neighbourhood query in `queries.py`, so hub edges drift upward
        # and a rebuild answers differently to the same graph. Counting here,
        # over the full replay, is independent of how much evidence is
        # displayed. `test_review_regressions` asserts the query assigns.
        #
        # `source_count` is counted here too, and it is NOT `mention_count`:
        # three articles from one trade outlet corroborate an edge far less than
        # three outlets do. `graph_service.edge_provenance` counts distinct
        # domains the same way, so serving the article count under the same name
        # made the canvas draw solid what the provenance panel called
        # single-source.
        aggregated: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            src, dst = row.src, row.dst
            # Symmetric edges are stored once with endpoints in lexicographic
            # order, otherwise every pair accumulates two mirrored edges.
            if predicate in ontology.symmetric_predicates and src > dst:
                src, dst = dst, src
            edge = aggregated.get((src, dst))
            if edge is None:
                aggregated[(src, dst)] = {
                    "src": src,
                    "dst": dst,
                    # dict keys: an insertion-ordered set of article ids.
                    "articles": {row.article_id: None},
                    "domains": {row.source_domain: None} if row.source_domain else {},
                    "confidence": row.confidence,
                    "kind": row.kind,
                    "first_seen": row.observed_at,
                    "last_seen": row.observed_at,
                    "declared": row.declared,
                }
                continue
            edge["articles"][row.article_id] = None
            if row.source_domain:
                edge["domains"][row.source_domain] = None
            # ANY contributing assertion being a declaration makes the edge one.
            # A declared edge that an article later corroborates keeps both: the
            # domain count rises on its own and the flag only ever exempts it
            # from a floor it would now clear anyway.
            edge["declared"] = edge["declared"] or row.declared
            edge["confidence"] = max(edge["confidence"], row.confidence)
            edge["kind"] = row.kind or edge["kind"]
            edge["first_seen"] = min(edge["first_seen"], row.observed_at)
            edge["last_seen"] = max(edge["last_seen"], row.observed_at)

        params = [
            {
                "src": edge["src"],
                "dst": edge["dst"],
                "confidence": edge["confidence"],
                "kind": edge["kind"],
                "first_seen": edge["first_seen"],
                "last_seen": edge["last_seen"],
                "mention_count": len(edge["articles"]),
                # An assertion whose document row is missing has no domain to
                # count, so fall back to one rather than claiming zero sources
                # for an edge that demonstrably has evidence.
                "source_count": len(edge["domains"]) or 1,
                "declared": edge["declared"],
                "evidence": list(edge["articles"])[-EVIDENCE_LIMIT:],
            }
            for edge in aggregated.values()
        ]

        query = f"""
        UNWIND $rows AS row
        MATCH (a:Entity {{id: row.src}}), (b:Entity {{id: row.dst}})
        MERGE (a)-[e:{predicate}]->(b)
          ON CREATE SET e.created_at = datetime()
        SET e.first_seen    = row.first_seen,
            e.last_seen     = row.last_seen,
            e.kind          = row.kind,
            e.confidence    = row.confidence,
            e.mention_count = row.mention_count,
            e.source_count  = row.source_count,
            e.declared      = row.declared,
            e.evidence      = row.evidence,
            e.projection    = $projection
        RETURN count(DISTINCT e) AS written
        """
        # Count what the MERGE actually reached, not what we submitted.
        #
        # `MATCH (a), (b)` silently drops a row whose endpoint has no node --
        # which is every edge touching an entity the projection leaves out, and
        # `dormant` is still one of those (ADR 0247 admitted `provisional` and
        # kept dormancy out). Returning `len(params)` therefore reports the size
        # of the INPUT: on 2026-08-06, when the projection was `active` alone,
        # live runs logged `edges_written: 520` and `435` while Neo4j held
        # **97**. A counter that overstates by 4.5x is worse than no counter,
        # because it is the number you reach for to answer "did the graph
        # grow?" and it answers "did we try?".
        #
        # `count(DISTINCT e)`, not `count(e)`: the MERGE can reach the same edge
        # twice via a symmetric predicate written from both directions.
        written = 0
        async with self._driver.session() as session:
            for chunk in _chunks(params):
                result = await session.run(query, rows=chunk, projection=self._projection)
                record = await result.single()
                written += record["written"] if record else 0
        return written

    async def upsert_articles(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        query = """
        UNWIND $rows AS row
        MERGE (a:Article {id: row.id})
          ON CREATE SET a.created_at = datetime()
        SET a += row.props, a.projection = $projection
        """
        async with self._driver.session() as session:
            for chunk in _chunks(rows):
                await session.run(query, rows=chunk, projection=self._projection)
        return len(rows)

    async def link_mentions(self, rows: list[dict[str, Any]]) -> int:
        """Article -[:MENTIONS]-> Entity. Carries salience for ranking."""
        if not rows:
            return 0
        query = """
        UNWIND $rows AS row
        MATCH (a:Article {id: row.article_id}), (e:Entity {id: row.entity_id})
        MERGE (a)-[m:MENTIONS]->(e)
        SET m.salience = row.salience,
            m.last_seen = row.observed_at,
            m.projection = $projection
        RETURN count(DISTINCT m) AS written
        """
        # Same `MATCH`-drops-silently shape as `upsert_edges`: a mention of a
        # provisional entity has no node to attach to.
        written = 0
        async with self._driver.session() as session:
            for chunk in _chunks(rows):
                result = await session.run(query, rows=chunk, projection=self._projection)
                record = await result.single()
                written += record["written"] if record else 0
        return written

    async def prune(self) -> dict[str, int]:
        """Delete everything this projection did not write.

        Without this the graph only ever grows. Retracting an assertion in
        curation sets `retracted_at` in Postgres and `_edge_rows` stops emitting
        it, but nothing removes the edge, so `/graph/*` goes on serving a
        relationship the reader explicitly withdrew. A merge is worse: the
        loser's node and all of its pre-merge edges sit alongside the survivor's
        folded copies. Invariant 1 says Neo4j is a projection of Postgres, and an
        append-only projection is not one.

        Safe because the projector replays everything on every call: an element
        carrying an older stamp is one Postgres has stopped implying. Only
        reachable after a projection has completed -- an exception unwinds before
        here, so a half-finished pass deletes nothing.
        """
        if self._projection is None:
            raise ValueError("prune requires a projection stamp; use begin_projection()")

        counts = {"edges": 0, "mentions": 0, "entities": 0, "articles": 0}
        statements = {
            "edges": "MATCH (:Entity)-[r]->(:Entity) "
            "WHERE coalesce(r.projection, -1) <> $projection "
            "DELETE r RETURN count(*) AS n",
            "mentions": "MATCH (:Article)-[m:MENTIONS]->(:Entity) "
            "WHERE coalesce(m.projection, -1) <> $projection "
            "DELETE m RETURN count(*) AS n",
            "entities": "MATCH (n:Entity) "
            "WHERE coalesce(n.projection, -1) <> $projection "
            "DETACH DELETE n RETURN count(*) AS n",
            "articles": "MATCH (a:Article) "
            "WHERE coalesce(a.projection, -1) <> $projection "
            "DETACH DELETE a RETURN count(*) AS n",
        }
        async with self._driver.session() as session:
            for key, statement in statements.items():
                result = await session.run(statement, projection=self._projection)
                record = await result.single()
                counts[key] = record["n"] if record else 0
        return counts

    async def stats(self) -> dict[str, int]:
        """Used by the idempotency gate: two runs must produce identical output,
        including mention_count.

        Every MATCH after the first is OPTIONAL, and that is load-bearing. A
        plain `MATCH ()-[r]->()` matches nothing on a graph with entities but no
        surviving relationships -- a fresh seed, or every edge retracted in
        curation -- which drops the row stream already carrying the entity count,
        so the query returns no record at all and reports an empty graph. The
        gate then compares `0 == 0` and passes having checked nothing.
        `test_stats_survives_a_graph_with_no_relationships` asserts the shape.
        """
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e:Entity) WITH count(e) AS entities
                OPTIONAL MATCH ()-[r]->()
                WITH entities, count(r) AS rels,
                     sum(coalesce(r.mention_count, 0)) AS mentions
                OPTIONAL MATCH (a:Article) WITH entities, rels, mentions, count(a) AS articles
                RETURN entities, rels, mentions, articles
                """
            )
            record = await result.single()
            if record is None:
                return {"entities": 0, "relationships": 0, "mention_total": 0, "articles": 0}
            return {
                "entities": record["entities"],
                "relationships": record["rels"],
                "mention_total": record["mentions"] or 0,
                "articles": record["articles"],
            }
