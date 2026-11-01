---
status: accepted
date: 2026-09-13
---

# 2026-09-13 — A name has an address

Every name in this corpus was reachable and none of it was addressable. The
hover card gives a definition, three articles and ten neighbours inside 400px;
`SearchView` and the command palette answered a question about one name by
opening the graph canvas with that node focused, which is a different question.
There was nothing to open in a second tab and nothing to send anybody.

`/entity/:type/:slug` is that address. The two segments reassemble to the C4 id,
which already *is* a slug: minted once at first sight, never recomputed, and a
rename adds an alias rather than moving it. Splitting the colon out is what
keeps the address free of percent-encoding, which is the difference between a
link that survives being pasted into a chat window and one that does not.
`web/src/router/entityRoute.ts` owns encode and decode and they are inverses,
the rule `graphRoute.ts` already holds.

## Three decisions inside it

**The page is paid; the card is not.** `/card` and `cards:batch` are in
`UNENTITLED_ROUTES` because the hover layer is part of the archive ADR 0211
promises: refusing them renders a lapsed reader's own briefs as prose whose
every program is dead. The page is the other thing — the whole of what Venona
holds on a name, which is what the graph and Learn already charge for as
"places you GO". So `/connections` and `/sources` are entitled, the card above
them still renders, and the refusal is said **once** on the page rather than
firing the global toast per section.

**A mention click still pins the card.** The page is one further click, from the
card's footer. Navigating out of a brief on a click is the trapdoor
`docs/DESIGN.md` names, and the reading surface keeps its rule.

**The first screen costs nothing extra.** The card already carries ten
neighbours and four articles and is usually warm from the hover that preceded
the click, so the page renders from it and fetches the full lists only when
someone presses "Show all". Four reads on mount would have been four reads for a
page most readers glance at.

## What it cost to find

`GET /api/entities/{id}/neighbors` was called by nothing in `web/src`. Two
defects had been sitting behind it, and the page is what would have made them
visible:

**Symmetric predicates were counted twice.** `graph/writer.py` swaps the
endpoints of a `PARTNERS_WITH` into lexicographic order before it MERGEs, with
the comment "otherwise every pair accumulates two mirrored edges". Nothing in
`pipeline/` normalises, so the assertion log genuinely holds both directions —
and `entity_service` aggregated on `(src, predicate, dst)` with no swap. One
partnership became two rows carrying half the publishers each, and expanding
either called `edge_provenance`, whose query matches both directions and
reports the full count: a row saying two publishers directly above evidence
listing four. Two pairs in the live corpus are affected today (Elroy Air ↔
Kratos, Anduril ↔ Boeing). The swap now lives in the read path beside the
writer's, reading `get_ontology().symmetric_predicates` per call.

**Aliases did not follow the merge closure.** `merge_entities` does not
re-point `aliases.entity_id`; `graph/projector.py` folds the loser's surfaces
onto the survivor at projection time, and its comment records that
`/graph/search?q=DHS` otherwise stops finding a node it used to find. But
`_aliases` queried the survivor alone, so graph search folded and the card did
not — the card listing fewer names than the search box would match.

Both are the class of fault the invariant-1 design makes cheap to fix and easy
to miss: the assertion log is correct, and only a reader of it was wrong.

## The window, and why it is not an offset on `/neighbors`

`neighborhood()` reads every assertion for the merge closure, aggregates in
Python and ranks. Slicing that for page two re-runs the whole traversal, the
rank key was not unique so ties fell through to the order a query with no
`ORDER BY` happened to return, and `Neighborhood` is `{nodes, edges}` rather
than the `{items, total}` envelope `lib/paging.ts` consumes.

So connections got their own route and their own flat shape, the repository
query is bounded and ordered the way its sibling already was, the rank key
carries the whole group key as its last term, and the offset has a ceiling:
past two hundred rows the answer is a filter, not a bigger window.
