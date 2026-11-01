---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — A typed ontology, not a predicate enum


The whitelist started as a Python tuple duplicated across `db/models.py`,
`graph/writer.py`, `graph/projector.py` and (soon) a hand-maintained JSON Schema
enum. Those drift, and the failure is silent: the model emits a predicate the
writer rejects and assertions quietly stop projecting.

Worse, **an enum enforces membership but not meaning**. A live probe produced
`Anduril —AWARDED_TO→ "$99M OTA"` — a real predicate used backwards.

**Now:** `config/ontology.yaml` is the single source, declaring `domain` and
`range` per predicate. Everything downstream is generated from it, and
`check_relation()` rejects semantically inverted assertions at write time.
Adjusting it over time is a data edit plus `venona graph rebuild` — cheap
precisely because the graph is a projection.
