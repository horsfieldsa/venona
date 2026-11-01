---
status: accepted
date: 2026-09-04
---

# 2026-09-04 — The field has one coordinate space, and the canvas is framed by the SVG's own rule (amends 0203)

The living graph was laid out in viewport pixels: node count from viewport
area, grid columns from the aspect ratio, cell size from width and height. So
it was never one drawing. It was a drawing the viewport computed, and every
page that differed by a pixel got a different one.

Measured, with the layout code run at plausible viewports against a
1654.43x910.16 baseline:

| viewport | nodes | grid | max node shift |
|---|---|---|---|
| baseline | 63 | 11x6 | — |
| 15px narrower (a scrollbar) | 62 | 11x6 | 14.7px |
| 50px shorter | 59 | 11x6 | 48.9px |
| 54px narrower | 61 | **10x7** | 1500.7px |

A scrollbar on one page and not the next moves every node and changes the node
count. Fifty-four pixels of width flips the grid, which is not a shift at all
but a different graph. That is what a reader crossing between the properties
was seeing, and no amount of matching colours or durations could have fixed it.

**The graph is laid out once, in the still SVG's viewBox.** 1600x1000, 67 nodes,
a fixed 10x7 jittered grid — positions, radii, line widths and drift amplitudes
all in those units. `fit()` then scales and centres to cover the viewport, which
is `xMidYMid slice` done in arithmetic rather than by the SVG engine, and sets
that as the canvas transform. Two things follow. The graph is ONE drawing at
every size and on every page, so navigation cannot rearrange it. And both layers
frame themselves by the same rule, so the canvas sits exactly where the still it
replaces did — which is the sense in which they are anchored to the same
background, and the thing 0203 and 0204 both assumed was already true.

A resize now re-frames instead of re-laying. It was rebuilding sixty-odd nodes
and a nearest-neighbour graph on every settle, and producing a different
composition each time; it now changes a transform.

## Consequences

- `FIELD_W`, `FIELD_H` and `COUNT` in `constellation.ts` are the same numbers
  `scripts/graph_field.py` emits the still from. They are one drawing's
  dimensions and must move together if they ever move.
- Clearing happens with the transform reset. Under a scaled, offset transform
  `clearRect(0, 0, w, h)` leaves the cropped margins painted, and the smear only
  shows at aspect ratios nobody develops at.
- On a phone the canvas crops the way the still always has: about a third of the
  field's width, all of its height. Denser than the desktop view rather than
  sparser, and identical to what the SVG under it would have shown.
- The node count no longer adapts to area. A very large display shows the same
  67 nodes scaled up, which is what "one drawing" costs and what it buys.
