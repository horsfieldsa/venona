---
status: accepted
date: 2026-08-31
---

# 2026-08-31 — The unconnected orbit the graph, they are not tiled beside it

A third of this corpus is edgeless: 231 of the 358 names on the whole-graph
view have no relationship drawn to them, because a relationship reaches the
graph only once both of its names are confirmed by a second source. They are
hidden by default and the readout offers to show them.

Shown, they were **tiled into a rectangle** beside the connected component --
fcose's own zero-degree tiling, mirrored by `tileStranded` for nodes arriving
between layouts. It works and it reads wrong. A rectangle has its own axis and
its own reading order, so the largest block on the canvas presented itself as
content: a table of something, sitting next to a graph, inviting the eye to
scan it row by row. What it actually is is the margin of the picture, and the
grid was the reason nobody read it that way.

**They go on a band around the graph instead.** A ring says "these are the rest
of it" where a rectangle says "these are a table", and the geometry carries the
fact that matters about them -- that they belong to the same set as the thing
in the middle and are simply not attached to it yet.

**Concentric, and it has to be.** One ring of 231 labels needs a circumference
of about 25,000px, which `fit` answers with a zoom that renders the whole graph
a smear. Each ring is packed to its own circumference and the next starts one
gap further out, so the band grows as the square root of the population rather
than linearly. On the live corpus that is six rings from 584 to 784, around a
core whose radius is 545.

**Packed for dots, not for labels, and that is the whole difference between a
halo and a second graph.** The first attempt reused the grid's spacing, which
was sized to hold a name under each node -- and produced a band 408px thick
starting 163px out, wider than the graph it surrounded and reading as scatter
rather than as an orbit. A degree-0 node does not draw a label until zoom
passes 1:1 (`LABEL_FLOOR`), and at that zoom the reader is looking at a slice of
the band rather than at all of it. Spending the whole circumference on room for
names nobody can read at rest is the grid's problem in polar form.

**The unconnected are kept out of the force simulation** rather than laid out by
it and moved afterwards. Handing fcose everything and repositioning on
`layoutstop` is simpler to write and costs a visible jump: `fit: true` frames
the grid fcose just drew, and the ring then lands somewhere else. So `eles` is
the connected core, fcose sizes and frames the graph that actually has shape,
and the band goes round the result -- with one un-animated `fitVisible` after
it, because the layout has just animated and a second movement on top reads as
a correction. `core` can be empty, which is a state the empty-canvas copy
already names, so it falls back to the whole set rather than handing fcose
nothing.

**A wholly-new component is still tiled.** `tileStranded` handles two
populations and they are not the same thing: a true isolate has no shape, but a
stranded pair or chain has an edge that draws as a line, and tiling those in
arrival order is what put partners at opposite ends of the block (see
`docs/GRAPH-PERF.md`). Components keep the grid and the label spacing that goes
with it; `placeUnpositioned` then sends the whole unconnected population through
`orbitLoose`, so the band is rebuilt rather than appended to and a newly
arrived isolate lands ON the ring rather than beside it.

**Ordered by id.** The band is rebuilt on every layout, and an unstable order
would rotate the whole thing each time for no reason a reader could see.

**Consequence.** `useCytoscape.ts` gains `looseNodes` and `orbitLoose`;
`layoutInitial` lays out the core alone and orbits the rest on `layoutstop`.
`ui_smoke.py` asserts the geometry rather than the picture: every unconnected
node sits outside the core's radius, and their distances from its centre fall
into ten or fewer distinct values -- which is what concentric rings are and
what a grid, at nearly one distance per node, is not.
