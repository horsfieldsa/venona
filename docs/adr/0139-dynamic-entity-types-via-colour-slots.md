---
status: accepted
date: 2026-08-18
---

# 2026-08-18 — Entity types become per-domain, coloured by slots

A domain may declare its own entity-type set in its `ontology.yaml`. The three
walls that froze the type set come down in a specific order, because each newly
permitted thing must already be safe:

1. **Colour**: `tokens.css` gains a bounded palette `--entity-slot-1..N`
   (slots 1–9 alias the nine existing families in current order, so defense is
   visually unchanged; the existing `--entity-<prefix>` names become aliases).
   Each ontology type declares a unique `color_slot`; the web app injects
   `--entity-<prefix>: var(--entity-slot-N)` at boot from `/api/meta/ontology`.
   The "missing token renders transparent" hazard moves from build-time lint to
   save-time validation — a type without a valid slot cannot be saved.
2. **Bindings**: every import-time ontology-derived list
   (`db/models.py::TYPE_PREFIX/ID_PATTERN`, `graph/writer.py::_LABEL_FOR_TYPE`)
   becomes a per-call read of `get_ontology()`. Type names get a shape guard
   (`^[A-Z][A-Za-z0-9]*$`) because they are interpolated into Cypher as labels —
   the same threat model that gave predicates `PREDICATE_NAME`. Prefixes get
   `^[a-z][a-z0-9]{0,11}$` (they reach a regex alternation and CSS names).
3. **The validator**: `_validate_ontology` then allows type add/remove with a
   version bump and changelog entry. Prefix renames on surviving types stay
   refused (ids are minted once, C4). `ANCHOR_PREDICATES` stay mandatory.

The cap on type count is the palette size — one constant. An empty
`org_types`/`platform_types` vocabulary must omit the schema property rather
than emit an empty enum (invalid JSON Schema).
