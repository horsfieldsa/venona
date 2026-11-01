---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The graph view could not show the graph


Two asks: "see the entire picture" and "a better search experience". The first
turned out to be structurally impossible, and diagnosing why produced a more
useful finding than the feature did.

**`/graph/query` requires `seed_entity_ids` (`min_length=1`) and walks exactly
one hop.** So clusters, bridges and isolated nodes were unreachable no matter
how many times you expanded — the view could answer "what is near this?" and
never "what is here?". At 184 entities and 97 relationships the entire
projection is smaller than one hop out of a hub like `org:dod`, so the seed
requirement was buying nothing. `/graph/overview` returns the lot.

Isolated entities are included deliberately. **83 of 184 active entities have no
surviving edge** — a node the corpus corroborated but never connected is a real
finding, and an overview that drops it shows a tidier graph than the one that
exists.

### The thing worth knowing

`venona graph stats` reports 908 relationships. The overview found 97. Both are
right: 811 of the 908 are `MENTIONS` (article→entity), and the entity-to-entity
graph really is 97 edges.

But SQLite holds **468 distinct relationship triples**. Only 97 project, and the
reason is exact:

```
triples with BOTH endpoints active      97   <- exactly the edge count
triples with an uncorroborated endpoint 371
```

`projector.py` writes `WHERE status = 'active'`, so a relationship with one
provisional endpoint cannot reach Neo4j. **79% of what the system knows about
how things relate is invisible, and it is the corroboration gate doing it, not
weak extraction.** Those are different problems with different fixes, and a
sparse graph with no explanation reads unambiguously as the second.

So the overview reports what it is not showing, counted from SQLite because
Neo4j cannot answer the question — the missing entities were never projected.
This is deliberately a disclosure rather than an "include provisional" toggle:
projecting uncorroborated entities changes what the graph *means* and reverses a
decision made for a stated reason ("a polluted graph is much more expensive than
a late one"). Surfacing the number is honest; flipping the invariant is a
product call.

### Search

Previously search existed only inside the path finder as a from/to picker, so
"where is Anduril in here?" required knowing the id and editing the URL. Now it
is a first-class box that seeds or centres on a hit.

Results carry `degree` and `mention_count`, because a name alone does not let
you choose. Live: `army` returns **Army** (9 links, 30 mentions), then
*British Army* (1, 3) and *U.S. Army* (3, 5). The ranking signal is the whole
feature.

That output also shows the fragmentation the conflict queue exists for —
`Anduril` / `Anduril Industries`, `Next Generation Command and Control` /
`...(NGC2)` are separate nodes. 182 conflicts are open. The graph looks sparser
and more scattered than the corpus warrants partly because those duplicates have
never been merged.
