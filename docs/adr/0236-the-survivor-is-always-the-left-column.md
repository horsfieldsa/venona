---
status: accepted
date: 2026-09-14
---

# 2026-09-14 — The survivor is always the left column

In the merge review the surviving entity renders in the LEFT column, whichever
side of the pair it came from. Pairs that cannot merge are exempt.

**Which side survives is a property of the data, and it moves.** It flips with
`x`, with the Swap button, and on its own when a new pair loads and the longer
name wins by default (ADR 0085). Rendering the survivor in the column the data
names therefore moves the surviving card between the two columns from pair to
pair, so the curator re-finds "the one that lives" once per pair — across a
queue of 147, that is the whole cost of the screen. Anchored left, the layout
says the same thing every time: left survives, right is absorbed, and pressing
Swap is a visible exchange rather than a relabelling.

**The exemption is not a special case.** For an anchor pair, parent and child
are a DIRECTION, not a survival; cross-type and type-conflict pairs have no
survivor at all, because both readings are known to be different things before
the curator reads them (ADR 0208). Sorting those would assert something untrue,
so they render in their own order with their own labels.

**Consequence.** `web/src/components/curation/MergeReview.vue` builds both
candidates as DATA in its `sides` computed and the template renders one column
twice from it, which is what keeps the two from drifting into different frames.
The sort at the end of `sides` is this decision; the load-bearing sentence stays
beside it.
