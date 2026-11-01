---
status: accepted
date: 2026-09-02
---

# 2026-09-02 — A declared edge names no article, so it is neither corroborated nor single-source

`profile/anchors.yaml` exists because the corpus supplies the leaves and never
the trunk: no article states which department a command reports to. Every edge
it declares was written into `assertions` with `article_id` defaulting to
`doc:seed-ngc2-overview` — a `fixtures/seed/` id that `load_anchors` deliberately
never writes — and an `evidence_quote` built from raw ids:

```
govt:defense-department BASED_IN country:united-states (seed).
```

Measured on the live corpus before the change: **all 65 anchor edges** pointed at
that absent document. The projector LEFT JOINs `documents`, so `site_domain` was
NULL, so `writer.upsert_edges` fell back to `source_count = 1`. Which means:

- `useCytoscape` drew the whole trunk dashed, its marker for uncorroborated.
- `graph/queries.py` **dropped every one of them** at `min_sources >= 2`. The
  org chart vanished the moment a reader moved the corroboration slider.
- `graph_service.edge_provenance` reported `single_source: true`, and served that
  id triple to the reader as the sentence behind the edge.
- `diagnostics.edge_corroboration` counted them as zero-publisher edges, so the
  health report's single-source share was partly measuring declarations.

**The fallback was answering the wrong question.** `source_count = len(domains)
or 1` is right for an assertion whose document row went missing — it demonstrably
had evidence. A declaration never had any, and never will: it makes no claim
about publishers at all. Reporting "one publisher said so" about a line a person
wrote is a false statement about provenance, in the one panel that exists to
answer where a fact came from.

So there is a third state. An edge is `declared` when it is `method='seed'`
**and names no readable source** — not when it is `method='seed'`, because
`fixtures/seed/edges.yaml` carries a real `evidence:` on every row and those
edges cite an article and are thinly sourced in the ordinary way. The flag rides
from the assertion through `EdgeRow` to a Neo4j edge property; the corroboration
floor and the dashed-edge rule both exempt it, and the provenance panel says what
it is instead of implying a publisher.

The quote is built from entity NAMES and says plainly that it was declared rather
than read. The `article_id` sentinel is `doc:declared`, which resolves to nothing
on purpose.

### `BASED_IN` leaves `allShortestPaths`

The ontology splits `BASED_IN` out of `PART_OF` so that "a country inside it
turns every route between two US organisations into a two-hop trip through a
supernode", and argues the split is safe because the predicate is then
*filterable*. That was true of the graph view's chips and false of the path
query: `queries.shortest_paths` interpolated **every** predicate name and neither
caller could narrow it. With a home country on most organisations, the shortest
path between any two of them becomes "both are based in the same place" — true,
two hops, and it buries the org-chart route under `LIMIT 5`.

`shortest_paths` takes a `predicates` argument, defaulting to the ontology's own
answer. The exclusion is declared **in `config/ontology.yaml`**, as
`excluded_from_paths: true`, not as a name in code: the ontology is per-domain
and editable over HTTP, and a predicate list bound in Python is the thing this
repo already refuses.

### The alternative that lost

Giving anchor edges a real declaration document in `documents` would have made
`source_count` honest by giving them a domain to count. Rejected: it puts a
fabricated article into the system of record, which is the exact reason
`load_anchors` is separate from `load_seed` in the first place.

### Consequence

- `graph/writer.py::EdgeRow.declared`, set in `graph/projector.py::_edge_rows`,
  aggregated in `upsert_edges`, written as `e.declared`.
- Four corroboration filters in `graph/queries.py` and the dashed-edge selector
  in `web/src/composables/useCytoscape.ts` exempt it. `GraphEdge.declared` and
  `EdgeProvenanceResponse.declared` carry it to the client — `response_model`
  filters, so a field absent from the schema is absent from the wire.
- `Ontology.path_predicate_names` and `PredicateSpec.excluded_from_paths`.
- `scripts/graph_health.py` reports declared edges separately rather than
  counting them in the single-source share.
- Found while doing this and fixed with it: `_load_edges` de-dupes **first-wins**,
  and both loaders passed inline `part_of:` shorthands ahead of the explicit
  `edges:` list — so every row carrying a real `evidence:` was being discarded in
  favour of the bare shorthand, the opposite of what the comment above it
  claimed. The explicit list now comes first.
