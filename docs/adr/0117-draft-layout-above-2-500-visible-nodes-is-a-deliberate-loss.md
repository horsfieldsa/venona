---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — `draft` layout above 2,500 visible nodes is a deliberate loss


A third quality tier below `default`, and unlike the other two it is not a free
win: `draft` is cheaper at every size and also WORSE at every size. Rendered and
compared at 1,930 visible nodes, `default` draws the sparse radial graph this
corpus actually is; `draft` collapses it into an overlapping blob whose smaller
bounding box then fits at a zoom where every label turns on — the wall of text
the `min-zoomed-font-size` floors exist to prevent.

So the threshold is not where `draft` becomes cheaper. It is where `default`
stops being a pause and starts being a hang: 1,532ms at 2,509 visible, 2,304ms
at 3,088, 12,175ms at 7,720, with the main thread blocked for all of it. Past
2,500 a rough graph in 200ms beats a good one nobody waits for, and it is a
one-off either way now that positions are cached.

**Consequence:** `LARGE_GRAPH` (120) and `DRAFT_GRAPH` (2,500) are both
measurements, and the table they came from is in `useCytoscape.ts` beside the
constant rather than only in `docs/GRAPH-PERF.md`, so the next person to tune it
can see what they are trading.
