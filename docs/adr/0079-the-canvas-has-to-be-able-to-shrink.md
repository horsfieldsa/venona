---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The canvas has to be able to shrink


`render()` used `mergeElements`, which adds what is missing and never removes.
That was correct while the graph could only grow: you seeded on one entity and
expanded hop by hop, and keeping what you had built up is the entire value of
the view.

It stopped being correct the moment the graph opened on the whole corpus.
Seeding on an entity afterwards is a SHRINK — the store drops to ~14 nodes and
the canvas kept all 229, so the readout and the canvas disagreed about what you
were looking at, and picking an entity from the command palette appeared to do
nothing at all.

**Consequence:** `syncElements` (add missing, remove absent) on the seed and
overview paths; `mergeElements` stays on the expand path, where merge-only is
the point. Caught by `make ui-smoke`, not by a human.
