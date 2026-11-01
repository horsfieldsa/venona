# Graph load time: what is actually slow, and when it starts to matter

*Measured 2026-08-16, against the live corpus and a running `make dev`.
Re-measured the same day after the work below shipped.*

The worry that prompted this: the graph explorer will become too slow to use as
the corpus grows. It is a real risk, but not the one it looks like, and not on
the timeline a first guess suggests. This records the measurements so nobody has
to re-derive them, and so a future run of the same benchmark can be compared.

**This shipped.** The first pass of this document ended "nothing here is
implemented"; it is all implemented now, and "What shipped" below records the
post-change numbers beside the ones that motivated it. The evidence is kept
because the decisions it justifies are the kind that get "improved" back --
particularly the pinned-`fcose` trap, which is the obvious design and does not
work.

## Conclusions first

1. **The server is not the bottleneck and does not become one.** 21–32ms and
   80KB today, against properly indexed Neo4j and 6ms of SQLite (as measured;
   the store is Postgres now and this has not been re-measured). Extrapolated to
   50x the corpus it is still tens of milliseconds and ~2MB uncompressed.
2. **`fcose` layout in the browser is the only thing that falls over.** It is
   fine to about 5,000 total entities, uncomfortable at 10,000, and unusable at
   20,000.
3. **Caching node positions removes the problem rather than deferring it.** A
   full cache hit is **20ms against a 4,699ms cold layout** at 10,000 entities.
4. **The obvious way to handle new nodes is the wrong one.** Pinning the known
   nodes and re-running `fcose` — what `layoutExpand` does today — costs ~1s and
   fails to complete at all past a few hundred new nodes. Placing new nodes at
   the centroid of their placed neighbours costs **~80ms, and looks the same.**
5. Two unrelated cheap defects found on the way: the overview node query has no
   `LIMIT` at all, and the graph engine is ~68% of the JS bundle on every page.

## Method

Chromium 151.0.7922.137, Intel Core 5 320 (6 threads), Arch Linux. Synthetic
graphs generated to match the **measured shape of the live projection**, laid out
with the exact options `layoutInitial` uses (`useCytoscape.ts:629-666`) at
`graphSpacing: 'normal'` (scale 1), `quality: 'default'` above the 120-node
`LARGE_GRAPH` threshold, `animate: false`, `packComponents: true`.

The shape being reproduced, taken from `POST /api/graph/overview`:

| property | live value |
|---|---|
| isolated fraction | **61.4%** of all active entities |
| connected nodes | 115 of 298 |
| mean degree (connected) | 2.03 |
| max degree | 36 (`country:united-states`) |
| degree histogram | 78 nodes at degree 1, then 19, 8, 3, 1, 2, 2, 1, 1 |
| components over connected nodes | 12; largest holds 79% |
| edges per connected node | 1.017 |

That is a sparse, star-dominated, near-tree graph, so the generator is a
Barabási–Albert tree (m=1) for the giant component, dyads for the rest, plus
isolated nodes to 61.4%. **The model is validated against reality**: it predicts
71ms at today's size where the live app measures 61ms.

This matters, because getting the shape wrong gets the answer wrong. An earlier
pass used denser preferential-attachment graphs and put the wall at ~5,000
*visible* nodes. On the real shape it is roughly four times further out. Any
re-run of this benchmark must re-check the shape table above first.

## The curve

Cold load, no cached positions. "Visible" is what `fcose` actually runs over —
`layoutInitial` lays out `cy.elements(':visible')`, and `hideIsolated` defaults
to `true` (`stores/graph.ts:44`), so the isolated 61.4% are excluded from layout
though not from the payload or the element count.

| total entities | visible | edges | init | layout | **total** |
|---|---|---|---|---|---|
| 300 *(≈ today)* | 116 | 118 | 33ms | 37ms | **71ms** |
| 1,000 | 385 | 393 | 67ms | 114ms | **181ms** |
| 2,500 | 964 | 981 | 81ms | 401ms | **482ms** |
| 5,000 | 1,929 | 1,963 | 137ms | 1,195ms | **1,332ms** |
| 10,000 | 3,859 | 3,926 | 370ms | 4,329ms | **4,699ms** |
| 20,000 | 7,719 | 7,851 | 564ms | 18,037ms | **18,600ms** |
| 40,000 | 15,438 | — | — | *did not complete in >3 min* | — |

Roughly 4x cost per doubling. The main thread is blocked for the whole of it.

**Where the corpus is on that curve.** `config/settings.yaml:35` runs a daily
cron over 17 sources. The one substantial run so far (`run:06a81e5b`) ingested
181 documents and produced 423 net-new entities — 298 active, 115 visible. The
net-new rate decays as recurring names dedupe against an established corpus, so
a date here would be false precision. What can be said without hand-waving is
the shape of the risk: **the first 181 documents bought 300 entities, and the
curve is comfortable until roughly 5,000 and unusable by 20,000.** The last
doubling costs more than every doubling before it combined.

## What fixes it

Measured at **10,000 total / 3,859 visible**, where a cold layout is 4,699ms.

| strategy | cache coverage | new nodes | time | stranded |
|---|---|---|---|---|
| Cold `fcose` | — | — | 4,699ms | — |
| `preset`, full hit | 100% | 0 | **20ms** | — |
| Pinned `fcose` (`proof`) | 99% | 57 | 997ms | — |
| Pinned `fcose` (`proof`) | 95% | 189 | 991ms | — |
| Pinned `fcose` (`proof`) | 80% | 749 | *did not complete* | — |
| **Centroid placement** | 99% | 57 | **76ms** | 0 |
| **Centroid placement** | 95% | 189 | **82ms** | 2 |
| **Centroid placement** | 80% | 749 | **94ms** | 32 |
| **Centroid placement** | 50% | 1,939 | **95ms** | 184 |

Two things in that table are worth more than the headline.

**The pinned-`fcose` row is a trap.** It is the obvious design — restore what you
know, pin it with `fixedNodeConstraint`, let the layout solve for the rest — and
it is what `layoutExpand` (`useCytoscape.ts:746-781`) already does. It costs ~1s
whether there are 57 new nodes or 189, because pinning constrains the simulation
without shrinking it, and `fixedNodeConstraint` forces `randomize:false` which
forces `quality:'proof'`, a full run to convergence. At 749 new nodes it stops
completing. Anyone reaching for the existing expand path as the incremental
mechanism will land here.

**Centroid placement is flat.** Place each new node at the mean position of its
already-placed neighbours, jittered, three passes so a new node adjacent only to
other new nodes still lands. No simulation at all. It costs ~80–95ms *regardless
of how much is missing*, degrading only in the count of "stranded" nodes — those
with no placed neighbour after three passes, which need a fallback.

It also looks right, which is the part a timing table cannot show. At 95%
coverage the new nodes distribute through the graph indistinguishably from a
full layout; at 80% there is slight clustering toward the denser region and it
is still perfectly readable. This was checked by rendering it and looking at it,
per the standing rule that a slice that passes tests but does not render is not
done.

So the design the evidence points at is three-tier, and the cheap tiers cover
the common cases:

- **Full hit** (arriving at `/graph`, a reload, a tab restore): `preset`, 20ms.
- **Partial hit** (the morning after a run): restore + centroid-place, ~80ms.
- **Cold** (first ever visit, or a filter producing an unseen scope): full
  `fcose`, and this is the only path that ever pays the curve above.

**Most of this already exists and is already correct.** `savePositions`,
`restorePositions`, `snapshotCoversCanvas` (`useCytoscape.ts:825-867`) and the
skip-layout branch in `render()` (`GraphView.vue:483`) are the full-hit tier,
working today. Three things stop it mattering: it lives in `sessionStorage` so it
dies with the tab; it is one global slot, so any second scope overwrites it; and
`snapshotCoversCanvas` demands **exact set equality**, so a single new entity
from tomorrow's run invalidates the whole snapshot and drops straight to cold.
That last one is why the partial tier is worth building at all.

## What shipped

*Re-measured 2026-08-16 after the change, same machine, same generator. The
shape table above was re-checked against a fresh `/graph/overview` first and had
not moved: 61.4% isolated, mean degree 2.035, max degree 36
(`country:united-states`), 1.0174 edges per connected node, same histogram. So
the curve above still describes this corpus.*

**On the live corpus.** The payload dropped from 298 nodes to 115 -- the 183
edgeless entities are no longer sent, rather than sent and hidden -- and the
readout is unchanged, because the count it prints now comes from the payload
(`isolated_hidden`) instead of from the nodes the client was hiding. A cold
layout of those 115 visible nodes is 416ms, most of which is the 400ms entry
animation; restoring them from the cache is **2.8ms**, and a partial hit that
places 23 new nodes among 92 known ones is **3.0ms** with nothing stranded.

**The bundle.** One 919KB / 292KB gzipped chunk became 326KB / **109KB gzipped**
plus a 617KB / 193KB graph chunk that only `/graph` loads. Every other route --
Brief, Archive, Lookup, Curation, Runs, Settings -- stopped downloading and
parsing a graph library to render text.

**The layout curve, re-run.** Now over the canvas the server actually sends
(connected nodes only), and with the third quality tier measured against the
second. `init` is not comparable to the table above: this run swaps elements
into a live instance rather than constructing a new one, so it excludes
Cytoscape's own construction.

| total | visible | edges | `default` | `draft` |
|---|---|---|---|---|
| 300 | 116 | 118 | 28ms | 12ms |
| 1,000 | 386 | 393 | 75ms | 29ms |
| 2,500 | 965 | 982 | 283ms | 62ms |
| 3,100 | 1,197 | 1,218 | 393ms | 81ms |
| 4,000 | 1,544 | 1,571 | 664ms | 108ms |
| 5,000 | 1,930 | 1,964 | 1,018ms | 142ms |
| 6,500 | 2,509 | 2,553 | 1,532ms | 195ms |
| 8,000 | 3,088 | 3,142 | 2,304ms | 268ms |
| 10,000 | 3,860 | 3,927 | 3,395ms | 301ms |
| 20,000 | 7,720 | 7,854 | 12,175ms | 755ms |

`draft` is cheaper at every size, and the threshold is deliberately NOT set
where it gets cheaper, because it is also WORSE at every size. Rendered and
compared at 1,930 visible: `default` draws the sparse radial graph this corpus
is, `draft` collapses it into an overlapping blob whose smaller bounding box
then fits at a zoom where every label turns on -- the wall of text the label
floors exist to prevent. So `DRAFT_GRAPH` is 2,500 visible nodes, the last point
where `default` is still under about a second and a half. Past it the cost
roughly quadruples per doubling and a rough graph in 200ms beats a good one
nobody waits for.

**The cache tiers, re-run at 10,000 total / 3,860 visible**, where a cold layout
is 3,395ms. Coverage is a fraction of the canvas, and the canvas is now the
connected set:

| coverage | new nodes | restore + place | stranded |
|---|---|---|---|
| 99% | 30 | 6ms | 0 |
| 95% | 150 | 7ms | 0 |
| 80% | 660 | 26ms | 34 |
| 70% | 1,060 | 32ms | 101 |
| 50% | 1,860 | 40ms | 217 |

Faster than the first pass measured, and flat in the number of new nodes as
before. Two things were learned by rendering it rather than timing it:

- **Three passes was one too few.** A chain of four or more new nodes ran out of
  passes while it was still making progress, so 123 of 265 new nodes at 70%
  coverage were tiled into the unconnected grid *with their edges*, drawing as
  long lines from the graph across empty canvas. The loop already stops when a
  pass places nothing, so the cap was doing nothing but cutting it short; it is
  8 now, and the extra passes cost nothing measurable.
- **Stranded nodes are tiled in COMPONENT order.** They are not all singletons --
  a wholly-new pair is stranded together -- and tiling them in arrival order put
  partners at opposite ends of the grid. Verified after the fix: at 70% coverage
  on a 965-node canvas, zero edges cross between the grid and the graph, and the
  120 stranded nodes are 60 pairs sitting side by side.

  The grid here is the one a stranded COMPONENT gets. A stranded singleton has
  no shape to preserve and joins the band around the graph instead; ADR 0182 has
  why, and `placeUnpositioned` rebuilds that band after this tiling runs.

**The coverage floor is 0.7**, and it was set by looking at the result rather
than at a timing table. At 70% the restored nodes are still the great majority
of what is on screen, the new ones distribute through the graph, and the picture
is the one you left. If it ever reads badly, RAISE it -- do not add a simulation
back.

---

## Two cheap defects found on the way

*All three are fixed; this is what they were.* Neither is the wall, and neither
was found by looking for it — which is the argument for measuring something you
are already confident about.

**1. The overview node query has no `LIMIT`.** `graph/queries.py:221-228`:

```cypher
MATCH (n:Entity) WHERE n.status = 'active' ... RETURN ...
```

`OVERVIEW_EDGE_LIMIT = 3000` bounds edges only. Every active entity is returned
on every call, and it is the only unbounded result set in the read path. Its
sizing rationale (`queries.py:26-29`) and the `/graph/overview` docstring are
both anchored to a snapshot that has already been overtaken — *"184 active
entities, 908 relationships"*.

**2. `hideIsolated` is applied client-side only.** `filterPayload`
(`stores/graph.ts:93-100`) never sends it, so the server ships 183 nodes the
client immediately sets to `display:none`. Today that is 61% of the payload and
61% of the created elements, drawn never. It does not affect layout — `visibleEles()`
already excludes them — but it does affect payload, element construction, and
every full-store pass in `toElements`.

**3. The graph engine is 68% of the JS bundle, on every page.** A production
build is one 919KB / **292KB gzipped** chunk, with only `GuideView` (6KB) split
out. Of that, the graph engine is roughly **200KB gzipped**:

| package | gzipped |
|---|---|
| `cytoscape` | 133KB |
| `layout-base` | 32KB |
| `cose-base` | 22KB |
| `cytoscape-fcose` | 13KB |

`useCytoscape.ts` is statically imported by `GraphView.vue`, which is statically
imported by the router (`router/index.ts:54`), so Brief, Archive, Lookup,
Curation, Runs and Settings all download and parse it. `GuideView` is the one
route that is `import()`-split.

## Why the server stays out of it

Recorded so this does not get re-investigated.

- `POST /api/graph/overview`: 21–32ms over three runs, 80,068 bytes, 298 nodes +
  117 edges. **193 bytes per element** — about 2MB uncompressed, ~300KB gzipped,
  at 5,000 nodes and 6,000 edges, over localhost.
- `_coverage` (`api/routers/graph.py:150-203`) runs on every overview call and
  does two full scans; measured at **6ms**, and the assertions scan uses
  `ix_assertions_subject`.
- Neo4j is properly indexed for these queries — `Entity.status`, `Entity.type`,
  the `Entity.id` uniqueness constraint, and the `entity_search` fulltext index
  (`graph/schema.py:11-31`). Relationship properties are not indexed, but the
  queries wrap them in `coalesce()`, which is non-indexable anyway; at this size
  it is an in-memory sort over a few thousand rows.
- There is no caching layer, and `SecurityHeadersMiddleware` stamps
  `cache-control: no-store` on every `/api` response (`api/middleware.py:225-232`),
  so every filter toggle is a full round trip. At 30ms that is not worth fixing.

## Reproducing this

The benchmark was run from throwaway scripts against the live app, deliberately
not committed — a benchmark harness in the tree is a maintenance burden before
anyone has decided to act, and adding a `.py` or `.ts` file to the working
directory restarts `--reload` uvicorn mid-run.

To re-run it: open `/graph`, generate graphs to the shape table above, and time
`cy.layout({...})` with `layoutInitial`'s options over `cy.elements(':visible')`.
**Check the shape table against a fresh `/graph/overview` first** — if the
isolated fraction or the mean degree has moved, the curve has moved with it.

Three things cost time on the re-run and are worth knowing:

- **Drive the app's own module, not a copy of it.** `import()` the URL the page
  ACTUALLY loaded — read it out of `performance.getEntriesByType('resource')` —
  because vite appends a `?t=` version query after any HMR update, and importing
  the bare `/src/composables/useCytoscape.ts` path returns a SECOND module
  instance whose `cy` closure is null. That fails silently as "placed 0,
  stranded 0", which reads as an algorithm that does nothing rather than as a
  harness pointing at the wrong object. Check `mod.getCy() === window.__cy`
  before trusting a single number.
- **Model the canvas the server sends.** `hide_isolated` is a request filter now,
  so the isolated 61.4% are not elements at all. Adding them and hiding them —
  which is what the original run did, correctly, for the code as it then was —
  makes coverage a fraction of a population that no longer exists, and every
  stranded count comes out roughly 61% too high.
- **Exclude them from `:visible` the app's way.** Give them the `filtered-out`
  class rather than injecting a `display: none` rule, so the benchmark inherits
  the real stylesheet's definition of visible rather than an imitation of it.

Sanity check before trusting any re-run: the 300-entity case must land near 70ms
for construction plus layout, and a full cache hit on the live corpus must be
single-digit milliseconds.

## The caps, re-derived (2026-08-26)

The two conclusions above — *the server is not the bottleneck*, and *the browser
is, until the draft tier and the position cache move it* — had a consequence
nobody followed through: `OVERVIEW_EDGE_LIMIT` and `OVERVIEW_NODE_LIMIT` were
still sized against the client ceiling as it stood **before** those shipped.

Inverting the shape table (61.4% isolated, 1.0174 edges per connected node)
against the caps as they were:

| cap | connected nodes | total entities | draft layout at that size |
|---|---|---|---|
| 3,000 edges | 2,949 | **~7,640** | ~270ms |
| 4,000 nodes | 4,000 | ~10,360 | ~310ms |

Two things fall out, and neither is visible from the code:

1. **They did not bite together.** The edge cap ran out 26% earlier than the
   node cap, so the edge cap alone decided what a reader saw and the node cap
   was decoration. A mismatch is invisible from the canvas — it says `capped`
   either way.
2. **The first one bit while the browser was idle.** At ~7,640 entities the
   `draft` tier lays the same graph out in about 270ms, and a cache hit is
   ~20ms. The server was clipping a graph the reader's machine would have drawn
   without complaint.

So both are now **8,000**, which is the last MEASURED row rather than an
extrapolated one: 7,720 visible nodes and 7,854 edges, 755ms cold on `draft`,
~20ms from cache, ~20,000 total entities. `tests/test_graph_api.py` holds both
properties — that the pair bites together, and that neither sits below what the
browser has been measured drawing.

**Past that the answer is not a bigger dump.** It is opening on a seed the
reader expands, via the `neighborhood` endpoint that already exists — which
changes what someone sees the moment they open the page, and is a product
decision rather than a tuning one.

### `layoutExpand` stopped simulating (2026-08-26)

The pinned-`fcose` trap this document names — ~1s at 57 new nodes, ~1s at 189,
no completion at 749 — was still live on the click-to-expand path, which is
exactly where the document predicted someone would land. It places directly now.

One thing the measurement did not cover, because the restore path never hits it:
on expand every new node has **exactly one** placed neighbour, the node just
clicked, so pure centroid placement lands them all on one point with only the
30px jitter between them. Sixteen neighbours draw as one smudge. The spreading
the simulation was doing has to be kept explicitly, so the clicked node's new
neighbours go on a ring whose radius grows with the count — enough arc per node
for its label — and everything else (a second seed's neighbours, a
neighbour-of-a-neighbour) falls through to `placeUnpositioned` as before.

The visible difference is that new nodes appear rather than animating outward
over 350ms. `revealNewNodes` pans only when something landed off screen, which
is the one case the old `fit: false` handled by luck.
