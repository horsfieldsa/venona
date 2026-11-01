---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — Node positions are cached per scope, and new nodes are placed, not simulated


`fcose` in the browser is the only part of the graph explorer that falls over as
the corpus grows. Measured on the projection's real shape: 283ms at 965 visible
nodes, 1,018ms at 1,930, 3,395ms at 3,860, 12,175ms at 7,720, main thread
blocked throughout. The server is not, and does not become, the bottleneck —
21–32ms and 80KB against properly indexed Neo4j. `docs/GRAPH-PERF.md` has the
whole curve and the shape it was generated from.

So the fix is to stop laying out a graph we have already laid out. Most of the
machinery existed and was correct — `savePositions`, `restorePositions`, the
skip-layout branch in `render()` — and three things stopped it mattering: it
lived in `sessionStorage` so it died with the tab, it was one global slot so a
second scope overwrote it, and it demanded EXACT set equality, so one new entity
from a run invalidated the whole snapshot and dropped straight to cold.

**Now:** IndexedDB, keyed by scope (`lib/positionCache.ts`), and three tiers in
`render()` — full hit restores and skips the layout, partial hit restores and
places what is missing, and only a miss pays for a layout. On the live corpus a
full hit is 2.8ms against a 416ms cold layout.

**The scope key is not `graphShapeKey` alone.** It carries the type and predicate
chips, the source-count slider and the spacing control as well, because each
produces a different visible set or a different scale from the same payload. The
exact-set-equality test this replaces existed to stop a whole-graph snapshot
being restored onto a brief's thirty-five nodes; keying by scope is what makes
that impossible by construction rather than by a check.

**THE PINNED-`FCOSE` TRAP.** The obvious way to place new nodes is to restore
what you know, pin it with `fixedNodeConstraint`, and let `fcose` solve for the
rest. It is obvious enough that the codebase already contains it — `layoutExpand`
does exactly this — so anyone improving this will reach for it. **It was measured
and it does not work:** ~1s whether 57 nodes are new or 189, because pinning
constrains the simulation without shrinking it, and `fixedNodeConstraint` forces
`randomize: false`, which forces `quality: 'proof'`, a full run to convergence.
At 749 new nodes it did not complete at all.

What replaced it runs no simulation: place each new node at the mean position of
its already-placed neighbours, jittered, repeating while a pass still places
something. Flat in the number of new nodes — 6ms at 30 new, 40ms at 1,860 — and
verified by rendering it, which is the part a timing table cannot show.

Two details are load-bearing and were both found by looking at the picture. The
pass loop stops when a pass places nothing, so a fixed three-pass cap only ever
cut it short: at 70% coverage it tiled 123 of 265 new nodes into the unconnected
grid *with their edges*, drawing as long lines across empty canvas. And nodes
left with no placed neighbour are tiled in COMPONENT order, because a wholly-new
pair is stranded together and arrival order put partners at opposite ends of the
grid.

**The coverage floor is 0.7**, chosen by rendering the 70% case and looking at
it: the restored nodes are still the great majority of what is on screen and the
picture is the one you left. If it reads badly on some future corpus, raise the
floor. Do not add a simulation back.

**Positions are never persisted server-side.** A cache miss must be a slow
render, never a wrong one, and node coordinates are not a fact about the corpus —
SQLite is the system of record for facts about the corpus (invariant 1).
