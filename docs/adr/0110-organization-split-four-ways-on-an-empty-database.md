---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — Organization split four ways, on an empty database


One entity type held 346 rows on the first run: SpaceX and Bain Capital beside
the 82nd Airborne Division, PEO USC, the Department of Education, GUR, Colorado
School of Mines, `org:war-on-the-rocks` (a publisher we read, not an actor) and
`org:anniston` (a place). The type's own description said so — *"any named body
that can act"* — and its four examples deliberately mixed a service, a program
office, a company and an agency, which is what the analyze prompt taught.

The cost was expressiveness, not tidiness. With buyer and seller in one type
`COMPETES_FOR` had to accept any organisation, so *"which companies are
competing for the programs my customer runs"* — the question the relevance
profile is built around — could not be asked of the graph.

**`Organization` was removed rather than kept as a residual**, because the split
landed in the same change that deleted every row. A residual type is the right
answer when there are minted ids to strand; with none, keeping it only gives the
model somewhere to retreat to. The trade is that an uncertain extraction now
picks one of four instead of a catch-all — a wrong type is visible in the
curation queue, where the old bucket hid it.

Three category errors are now named as non-entities in `ontology.yaml`, because
each reached ACTIVE on the first run: a publisher, an event, a place.

**`org_type` became emittable in the same change.** It had been declared in the
ontology, rendered into the analyze prompt by `extraction_guide`, stored in
`entities.org_type`, projected to Neo4j and typed in `schema.d.ts` — while
`analyze_schema()` omitted the property and set `additionalProperties: false`.
The model was instructed to emit a field the schema forbade, and all 346 rows
had `org_type IS NULL`.

**What the split does not fix, stated so nobody claims it did.** Four of the
five ontology rejections on that run were the model choosing the wrong
PREDICATE, not the ontology being too coarse: `SpaceX --OFFERS--> Starlink`
should be `PRODUCES`, and `Ministry of External Affairs --PART_OF--> India` is
correctly refused because a ministry is `BASED_IN` its country.
