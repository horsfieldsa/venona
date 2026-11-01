---
status: accepted
date: 2026-08-07
---

# 2026-08-07 — The graph reads at a glance, and the route survives the trip


The graph opened on the whole corpus with every node labelled and ninety-one
edgeless entities spreading the bounding box until `fit` asked for a zoom of
0.05. Accurate, and unreadable. Three changes, in order of how much each bought.

**Labels are a function of zoom, and it is not our function.** Cytoscape has
`min-zoomed-font-size`, evaluated per draw by the renderer. Being a style
property it is per-selector, so "which nodes keep their name when zoomed out"
falls out for free — no `cy.on('zoom')` handler, no rAF, no teardown, no risk of
a per-frame handler touching a Vue ref. Beware the quantisation: the renderer
compares against `2^ceil(log2(zoom * pixelRatio))`, so with `pixelRatio: 1` and a
10px font there are exactly three reachable cutoffs, at zoom 0.25, 0.5 and 1.0.
Tuning 12 to 14 does nothing at all, and someone will try.

**A landmark is high DEGREE, not high relevance.** The first cut used
`relevance >= 0.55`, which selected zero nodes: relevance is scored 0–1 but the
live corpus tops out at 0.3, so any threshold on it selects everything or
nothing. Degree is also the better signal for the job — orientation in a field
of dots is "which of these is the middle of something", which is what a landmark
on a map is. Degree is written into node data by `stampDegrees` rather than read
via `n.degree()`, because a style function re-evaluates on data change and not on
structural change: a node whose edges arrive on a later expansion would keep the
size and the label floor it was born with.

**Hiding the unconnected is what actually produced breathing room.** Not the
spacing control — under `fit`, spacing is very nearly scale-invariant: a 1.7x
bigger layout gets a 1.7x smaller zoom and the only net change is that nodes and
labels end up relatively smaller. The ninety-one edgeless entities were what
forced the tiny fit zoom; hiding them took it from 0.05 to 0.61. The spacing
control still exists, and it is genuinely visible in focus and brief mode where
the fit lands near 1:1. The rail says so, because a control that looks broken is
worse than no control.

Hiding them is only legal because the readout states the count and carries its
own undo — `queries.overview()` argues that an edgeless node is a real finding,
and it is right. The undo is deliberately NOT the existing `reset` link: that
also clears the type and predicate chips, and one link that undoes two unrelated
decisions is a bug with a delivery date.

**Focus mode walks the resident corpus; it does not ask the server.**
`/graph/query` caps at fifty EDGES per request ordered globally by mention count,
so walking a frontier with it returns fifty hub-weighted edges that are
indistinguishable from the real two-hop neighbourhood. A view that cannot tell
you whether it is complete has no business claiming a depth. The whole
projection is already client-side after `/graph/overview` (a few hundred entities
against a 3000-edge ceiling), so a breadth-first walk over what we have is exact
and instant. Adjacency is built from filter-passing edges only — traversing an
edge the reader has filtered out would put a node in scope that no visible path
reaches.

Focus HIDES rather than removes, which is why leaving it runs no layout at all:
the whole-graph arrangement is stashed in a module-level slot and comes back
byte for byte. `savePositions`, `snapshotCoversCanvas` and `layoutExpand`'s
`fixedNodeConstraint` all assume nodes persist.

**Visibility runs BEFORE the layout, always.** Both layouts run over `:visible`
and both end in a fit, so hiding afterwards fits the viewport to a set that
includes what is about to disappear. The brief's nineteen drawn nodes ended up
as a sparse scatter in a corner sized for thirty-five.

**The position snapshot needs EQUAL sets, not a subset.** "Every canvas node is
in the snapshot" silently accepted a whole-graph snapshot as a description of a
brief's thirty-five nodes, restoring corpus coordinates for a subgraph. A
snapshot of a different scope is not a snapshot of this one.

### The route

`showEverything` did `router.replace({query: {view: 'all'}})` — obliterating the
seed, the filters and the way back on its way past, and writing a key nothing
ever read. `?from=` named a paragraph but not the document containing it, so
"back to the brief" was hardcoded to `/`, and arriving from an archived brief
landed the reader on today's holding an item id that is not in it. And because
`GraphView` is inside `<KeepAlive>`, `onMounted` runs once: the only watcher was
on `route.query.seed`, guarded on equality, so arriving from a second brief on
the same entity was a total no-op.

One module owns encode and decode and they are inverses — see the table in
`docs/DESIGN.md`. That is not tidiness: the view watches the decoded intent's
key, so its own `router.replace` writes a query that decodes to the intent it
already holds, the watch source does not change, and the loop cannot start.
A `let applying = true` flag was the alternative and is worse: it has to be
cleared in a timer, it swallows the first navigation after a filter change, and
it is wrong under browser back/forward, which produces a genuine change that
looks exactly like an echo.

**Fixing the return trip introduced a second bug, in `BriefView`.** Once
`backToBrief` pushes `/briefs/:id`, both `onActivated` and `watch(routeId)` fire
— two concurrent `loadForRoute` calls, two `restoreScroll` calls. They are not
idempotent: the first consumes `returnItemId` and scrolls to the paragraph, the
second finds it null and falls through to the remembered offset. The reader
lands at the top of the page, which looks exactly like the bug being fixed. An
in-flight dedupe keyed on the route collapses the concurrent pair only; a later
activation of the same route must still re-run, since that is what restores the
position after a graph trip.
