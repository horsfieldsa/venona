---
status: accepted
date: 2026-09-17
---

# 2026-09-17 — The canvas is never allowed to frame nothing

Reported three times in one afternoon, in three shapes: the graph "doesn't
render properly", it is "malformed right after the switch", and a hard refresh
fixes it every time. All three were the same fact, and the reason a refresh
cured it is the reason it was so hard to see from inside the app: **every
element was present, visible and correctly placed. The VIEW was somewhere
else.** A reload recomputes the viewport transform against the real box; nothing
short of one did.

Cytoscape holds the model (positions) and the viewport (pan and zoom)
separately, and nothing in this app ever checked that the second one pointed at
the first.

## Three ways the view and the box come apart

**The settle was hung on one event.** `layoutInitial` placed the unconnected on
their band and framed the result on `layoutstop`, which -- measured 2026-08-16,
and recorded in `useGraphLayout`'s `relayout` -- is not emitted on the first
paint of a scope. Suppressing it in a browser reproduces the report exactly:
zoom 0.1022, a third of the legibility floor, with 758 of 855 unconnected names
tangled through the middle of the connected graph.

**The box changes and nothing re-frames.** `resizeToContainer` preserves the
centre of the extent, deliberately, so that collapsing a rail does not slide the
graph under the reader. That is the right answer for a box that changed a little
and no answer at all for one that changed enough to leave the graph outside it:
opening the filter rail takes 240px, full screen takes both rails, a window can
halve.

**A zero-sized box is terminal and silent.** The observer skips zero-sized
boxes because a renderer told it is zero pixels cannot be resized back, so
nothing recomputed the transform afterwards either. Entering and leaving full
screen produces one transiently. So did the layout itself: two open rails are
608px, and a grid handing each track its preferred width left the canvas a track
of ZERO on any window under about 900px.

## The invariant

**If the canvas holds visible elements and the viewport does not overlap them at
all, fit.** Checked in `resizeToContainer`, which is what every box change
already goes through, so there is one place to get it right rather than one per
caller. It cannot fire while anything is on screen, so it can never move a graph
somebody is reading, and `framesNothing` is a pure function with its own tests.

Around that:

- `settleCanvas` is the one ending for every path that changes what is drawn:
  the box, then the band, then the fit. The layout path and the position-cache
  path both end there, so neither can drift from the other.
- The settle runs on `layoutstop` OR a beat after `layoutready`, whichever comes
  first, and once. `layoutFocus` takes the same pair.
- A box seen at zero is remembered, and the next real measurement re-frames
  unconditionally: there is no centre worth preserving from a viewport that had
  no dimensions.
- The rail tracks yield and the canvas does not (`--rail-canvas-min`). A narrow
  window now costs the rails their width instead of costing the reader the
  graph.

## Rejected

- **Fit on every box change.** The reason `resizeToContainer` preserves the
  centre is that a reader who has zoomed into a cluster must not be thrown back
  out because they collapsed a rail. The guard fires only when there is nothing
  to preserve.
- **Fitting on a timer, or on every render.** A canvas that re-frames itself
  while being read is a worse failure than one that occasionally needs a
  refresh, and it is much harder to report.
- **Letting the grid overflow instead of starving the canvas.** Horizontal
  overflow is banned for good reasons elsewhere in `docs/DESIGN.md`, and a rail
  that is narrower than it wants to be still works.

**Consequence.** `useCytoscape.ts` gains `settleCanvas`, `ensureOnScreen` and
`framesNothing`; `resizeToContainer` ends with the guard; `layoutInitial` and
`layoutFocus` settle on either event; `useGraphLayout`'s observer remembers a
dead box and re-frames on recovery; `GraphView.vue`'s grid takes
`minmax(var(--rail-canvas-min), 1fr)` for the canvas track and `minmax(0, …)`
for the rails. `ui_smoke.py` asserts the two invariants after a scope change and
drives a container to zero and back.
