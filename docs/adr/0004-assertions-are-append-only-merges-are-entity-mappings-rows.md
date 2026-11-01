---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Assertions are append-only; merges are `entity_mappings` rows


The first draft had a merge rewrite `assertions.entity_id` from loser to
survivor. That destroys the immutability that makes the graph rebuildable, and
makes unmerge lossy.

**Now:** merges are rows in `entity_mappings`; the projector resolves through
the active chain (with a cycle guard). Unmerge is `active = 0` plus a rebuild.
Same amount of code, genuinely reversible.
