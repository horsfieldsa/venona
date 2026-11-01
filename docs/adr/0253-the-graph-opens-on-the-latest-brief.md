---
status: accepted
date: 2026-10-25
---

# 2026-10-25 — The graph opens on the latest brief

A bare `/graph` drew the whole projection. `docs/GRAPH-PERF.md` sized that view
when the live graph had 115 connected names; on 2026-10-25 it had 1,986, and
3,242 with names seen once. Past `DRAFT_GRAPH` (2,500) the layout drops to
`draft`, which the same document records as collapsing into a blob that fits at
a zoom where labels switch on. On a tablet that rendered as an unreadable smear
after several seconds of a frozen page, with nothing on screen to say it was
working. 309 names cleared the landmark degree floor, so even a well-arranged
canvas printed their labels over each other.

## Decision

- **The landing scope is the latest brief.** An absent `mode` with no subject
  decodes to `brief` with no brief id, and the view resolves it to the newest
  brief: 63 names on the day this was decided, against 1,986. An instance with
  no brief yet falls back to the whole graph. Reset returns to the same place.
  The whole graph is one chip away and `?mode=all` still addresses it.
- **Going up out of a focus entered from a brief returns to the brief**, not to
  the whole graph.
- **A large cold layout is covered.** From 400 visible names the canvas is
  hidden behind "arranging N names" until the layout settles, and a fetch that
  runs past 400ms shows "loading the graph". The cover is raised before the
  elements are built, because building 3,000 of them blocks the main thread for
  as long as laying them out does. A cache hit lowers it without a layout.
- **Landmarks are capped at about 40**, the best connected, above the old
  degree floor of 5.

## Measured

Production payload, read-only proxy, headless Chromium with the CPU throttled
4x (tablet width) and 6x (390px): the landing view draws 63 names; the whole
graph with names seen once is covered from the click until the finished
picture, with no frame of the half-built canvas, and 43 names keep labels at
rest.

## Not done

Laying the graph out off the main thread, or computing positions once per run
and shipping them with the payload, which would make the whole graph instant on
every device. Worth it only if readers keep choosing the whole graph once it is
no longer the front door.

## Addendum, same day: the spokes were a stale position cache

After this shipped, the iPad still drew the whole graph as a corner of long
faint lines. All three renderers (`?renderer=default|plain|webgl`, with
`?graphdebug=1`) drew the same picture at zoom 0.32, so it was not Safari's
drawing. The position cache restored names saved on the unconnected orbit that
had since gained relationships, and each drew a line across the canvas into the
core; the bounding box passed `MIN_LEGIBLE_ZOOM` and the fit clamped. Chromium
never showed it because every test started from an empty cache.

A restored name with relationships that sits more than 600 units (times the
spacing scale) from every one of them is now treated as unknown and placed at
its neighbours' centroid; if that takes coverage under the 0.7 floor the scope
is laid out cold. Reproduced in Chromium by moving 60 connected names onto a
2,500-unit ring: bounding box 5,113 wide before, 1,740 after.
