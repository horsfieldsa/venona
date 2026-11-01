---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — `PART_OF.kind` was silently dropped for the whole build


**Found in the dev log**, not by a test. Neo4j emitted an
`UnknownPropertyKeyWarning` on every path query: *"the missing property name
is: kind"*.

The seed declared `kind: org_hierarchy` on all 11 `PART_OF` edges, the ontology
declares the vocabulary (`org_hierarchy | corporate | joint`), and the writer
had a `kind` parameter — but `assertions` had nowhere to store it and the
projector never read it. Every edge reached the graph without it.

That field is the difference between an **org chart** (PEO C3N → US Army) and a
**corporate structure** (an acquired subsidiary → its parent), both of which
deliberately share the same recursive `PART_OF` edge.

**Now:** migration 0002 adds `assertions.predicate_kind` (named to avoid
colliding with `assertions.kind`, the entity/relation discriminator), the seed
loader and the analyze stage populate it, and the projector passes it through.
Two regression tests assert every `PART_OF` assertion has a kind and that every
kind is valid for its predicate.

Worth noting the lesson: a database warning that "looks harmless" was the only
signal that a declared ontology field was being thrown away.
