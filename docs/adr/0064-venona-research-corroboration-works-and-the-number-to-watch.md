---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — `venona research`: corroboration works, and the number to watch


**The question:** how do relationships get filled in, and can a job research
existing entities to find more?

**How they get filled in — one path only.** The model emits a relation in the
`relations` array of a single-document analyze call, with a verbatim evidence
quote, a predicate that survives `check_relation`, and both endpoints already
resolvable (`_resolve_endpoint` passes `allow_mint=False` — a relation can never
create an entity). Nothing else writes one.

**Why the graph looked starved.** Extraction was never the problem: 520 relations
written against ~38 rejected. SQLite held **468 distinct triples** and Neo4j
showed **97**, because `projector.py` projects only `active` entities and the
Cypher `MATCH` silently drops an edge whose endpoint has no node. 79% invisible,
all of it the corroboration gate.

**Why it would not fix itself.** Promotion is passive. Of the 435 provisional
relation endpoints, **423 sat at exactly one article**. `org:gao` had 24 articles
from one publisher and could never self-corroborate. And the dormancy rule added
the same day was about to retire exactly these.

### The design: find documents, not relationships

The job never extracts a relationship. It finds **documents** about stuck
entities and hands them to the existing pipeline, so relations still arrive the
only way they ever have — the model reading real article text, with an evidence
quote and an ontology check. No new trust surface.

That is affordable because analyze picks work up by STATE, not run id
(`pending_clusters` is `WHERE analyzed_at IS NULL`). Producing URLs buys
extraction, promotion, acronym grounding and the graph for free.

Targeting ranks by **blocked triples**, not mentions: an entity mentioned twice
that sits on no relationship costs the graph nothing, while Fincantieri —
mentioned once — held seven edges out. `dormant` entities are included on
purpose.

### It works. Measured, first live run, $0.75

```
targeted    6 entities (blocking 34 relationships)
documents   3 new, 3 already had / unusable
analyzed    3 clusters
promoted    7 entities
graph       97 -> 102 edges  (+5)
```

Verified against Neo4j directly, not just the command's own report: Fincantieri
and Echodyne went provisional → **active** with 2 publishers each, and
`both endpoints active` in SQLite rose 97 → 102 in lockstep. Seven entities
promoted from three documents — corroboration spills over.

**And the honest half: 3 of 6 found nothing.** Fire Point, HII and Sikorsky are
still on one publisher. If the trade press never wrote a second piece, no job can
invent one, and the command says so rather than reporting a win.

### The prerequisite that made the result trustworthy

`upsert_edges` returned `len(params)` — the count SUBMITTED. Live runs logged
`edges_written: 520` and `435` while Neo4j held 97, because `MATCH (a), (b)`
drops rows whose endpoint has no node. `link_mentions` had the identical bug.

Both now `RETURN count(DISTINCT e)`. Not `count(e)` — several assertions
routinely MERGE the same edge (that is what `mention_count` accumulates), which
lands halfway between the two wrong answers. After the fix the projector reports
`97 / 811` against a ground truth of `97 / 811`.

This had to be fixed **first**. The whole point of the command is to answer "did
the graph grow?", and the counter it would have used was answering "did we try?".

### Not built, deliberately

No nightly stage — on-demand only, until the hit rate is known across more
entities. A third of six is promising, not proven.

The cheaper win remains untouched and free: **182 open conflicts**, with
`Anduril` / `Anduril Industries` and `Next Generation Command and Control` /
`...(NGC2)` still separate nodes. Merging consolidates evidence across the merge
closure — `promote_provisionals` already aggregates over it — and can promote
entities with **zero model spend**. Worth working that queue before spending on
research.
