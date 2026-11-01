---
status: accepted
date: 2026-09-17
---

# 2026-09-17 — The graph shows what it has not confirmed, and says so in the fill

The canvas draws an edge only where BOTH endpoints are `active`
(`select_entities_for_projection`, and the `status == "active"` pair in
`routers/graph.py::coverage`). Everything below the corroboration gate is
absent, and the coverage line reports the absence as a number.

That gate stays. What changes is that a reader can ask to see underneath it,
with the unconfirmed half marked on every surface that draws it.

### Why now

ADR 0064 found this same 79% and answered it correctly for the time: raise the
evidence, do not lower the gate, and `venona research` exists to find documents
about stuck entities. That answer has not kept up. Measured on production
2026-09-17 against 435 provisional relation endpoints in ADR 0064:

```
entities     4714 provisional · 1788 active · 262 dormant
relationships 3729 known · 1462 shown · 2267 hidden
of the hidden  2266 rest on ONE publisher; exactly 1 has two
by endpoint   1437 active+provisional · 731 both provisional · 99 touch a dormant
provisional entities that carry an edge at all: 2181 of 4714
```

The pool grew tenfold and the drain did not. A reader who wants the thin
material is not asking the gate to be wrong; they are asking to see what Venona
has read. Hiding it answers a different question than the one being asked.

### The fill is the channel, and it has to be

`useCytoscape.ts` already dashes an edge resting on one publisher, and
`docs/DESIGN.md` requires that: epistemic weakness has to survive greyscale, so
a colour step will not do. **2266 of the 2267 hidden edges are single-publisher,
so they arrive already dashed** — into a canvas where 1092 shown edges are
dashed too. Tripling the dashed population is what would break: the one signal
the canvas has for thin evidence stops separating anything.

So the new fact goes on the NODE, where it belongs — what is unconfirmed is the
NAME, not the relationship — and in the one channel nothing else uses:

| channel | already means |
|---|---|
| node fill | entity type (the hue) |
| node border-style | `dotted` = expandable |
| node border-colour | accent = seed, `--text-primary` = selected |
| node/edge opacity | context, muted, hover, path |
| edge line-style | `dashed` = one publisher |
| edge width | `log2(source_count)` |

**A name seen once draws hollow: the ground as fill, its entity hue moved to the
border.** Type survives, because the hue is still there. The signal survives
greyscale, because a fill is present or absent. And it survives selection, which
repaints the border and leaves the fill alone.

The edge needs no new treatment at all. Read the line for the relationship, the
fill for the name.

### Dormant stays hidden

99 of the hidden edges touch a `dormant` endpoint. Dormancy is ADR 0054's drain
on a pool that otherwise only grows, and a `dormant` entity is one that was
uncorroborated AND silent for the whole window. Revealing those undoes the
mechanism. Only `provisional` is revealed: 2168 edges, 2181 nodes.

### The word is not "uncorroborated"

`tests/test_copy_style.py::JARGON` bans it, and "provisional" is this repo's
word for its own state machine. The surface already says the true thing in
reader language — "hidden until a second source confirms both of their names" —
so the phrase is **one source has named this**, and on the legend, **seen once**.
`web/src/lib/learnCopy.ts` is the pattern for the wire value becoming that.

### Rejected

- **Lower the gate itself.** Promotion decides what the brief rail, the hover
  card and the alias index treat as real; it is not a display setting. ADR 0021
  measured what happens when the gate softens, and a graph that quietly promotes
  is the thing 0050 and 0054 were both written to stop.
- **A second edge treatment** (thinner, lighter, dotted). Puts two weak-evidence
  vocabularies on one line and forces the reader to decode which is speaking.
- **Keeping the path finder confirmed-only.** Held while this was proposed, on
  the reasoning that a path is a SEQUENCE and a dashed line cannot qualify one.
  `shortest_paths` argues the other way in its own docstring and is right: the
  rail's filters travel with a traversal because "a route is only an answer if
  the reader can see it", and the reverse costs as much -- refusing to walk a
  name the canvas has just drawn reports no route between two visibly joined
  nodes. The weakness IS expressible, because a path's nodes land on that same
  canvas and draw hollow there. So the path finder takes the flag too.
- **On by default.** The corroborated graph is the one worth landing on, and the
  node budget is the other reason: 1788 → 3969 nodes and 1462 → 3630 edges. Both
  still clear `OVERVIEW_NODE_LIMIT`/`OVERVIEW_EDGE_LIMIT` (8000), but the
  headroom goes from 5.5x to 2.2x.

**Where it stops.** `subgraph` (the brief's own graph) and `search_entities`
stay confirmed-only. A brief names what Venona chose to write about and its
subgraph answers a different question from the explorer's; search seeds the
canvas and returning names that the current setting will not draw is a result
that goes nowhere. Both are one `$statuses` parameter from changing if that
turns out to be wrong.

**Consequence.** `db/repositories/projector.py::select_entities_for_projection`
stops filtering to `active` and the projector carries `status` onto the node;
`graph/schema.py` and the generated `docs/graph-schema.md` gain the property.
`api/schemas/graph.py::GraphNode` gains `status`, and `routers/graph.py`
computes coverage against the revealed set. `useCytoscape.ts` gains one
stylesheet rule and the legend one line; `GraphView.vue` gains the toggle beside
the source slider. `Card.status` already exists (C8) and starts being rendered —
`EntityHoverCard.vue` and the rail read it, so the three surfaces say one thing.
No contract changes shape; C8 gains a rendered field it already carried.

**Addendum, same day.** The setting is part of `GraphIntent` and rides the URL
as `?unconfirmed=show`, alongside `orphans`. Not for shareability, though it
buys that: `graphShapeKey` is what `useGraphLayout`'s `scopeKey` keys the
position cache by, and a setting that triples the canvas while leaving the key
unchanged hands one arrangement's coordinates to the other graph -- at full
coverage, so no layout ever runs to correct it, and in IndexedDB, so it outlives
the tab. Anything that changes what the server sends has to be addressable. It
is in the key for `all` and `focus`, whose endpoints honour the flag, and out of
it for `brief`, whose endpoint does not.
