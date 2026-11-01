---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Merging was prohibited-with-no-alternative; the read side had to land first


`CLAUDE.md` forbids `UPDATE`ing an assertion and mandates that merges be
`entity_mappings` rows. `graph/projector.py` read that table correctly —
chain-collapsing, cycle-guarded. **Nothing wrote it**, and `docs/RUNBOOK.md`
documented a `venona entity merge` that did not exist. The prohibition was
enforced and its permitted alternative had never been built, so there was no way
to merge two entities at all.

**The read side first, deliberately.** A merge written before this would have
been a *regression*, not an improvement:

- `entity_service._entity_row` had no status filter, so the merged-away entity
  kept rendering a full card forever, and historical `brief_blocks.spans` store
  raw ids.
- `alias_index` joined `entities WHERE status='active'`, so demoting the loser
  made `MDA` and `FBI` **stop matching entirely** — the merge would have deleted
  the very surfaces it was meant to unify.

Both now resolve through `projector.resolve_mappings()` — reused rather than
reimplemented, because two chain-collapsers that disagreed would be worse than
none. The alias index deliberately includes `merged` entities and repoints them
at the survivor.

**`merged`, not `rejected`.** "This is a duplicate" and "the model hallucinated
this" are different facts. `merged` also keeps the loser out of
`promote_provisionals`, which re-evaluates every run and would otherwise
resurrect it into the projection after a human had merged it away.

**Two corrections to my own first attempt, both found by trying it live:**

1. I required an **active survivor**, reasoning that the projector only emits
   active nodes. But 196 of 197 provisionals sit at (1 article, 1 source), so
   nearly every real duplicate is two *provisional* entities — the rule made
   merging impossible exactly where it was most needed. Now only `merged` and
   `rejected` survivors are refused.
2. Promotion had to become **closure-aware**. Assertions are never rewritten, so
   the survivor kept counting only its own; merging two single-article entities
   left both below the gate and achieved nothing. Evidence is now gathered over
   *every* entity including merged-away ones, while status is decided only for
   provisional/active rows. Filtering evidence by status was the bug — it
   dropped the loser's articles.

Verified live on the two real duplicates: `org:mda` → `org:missile-defense-agency`
and `org:fbi` → `org:federal-bureau-of-investigation`. Both survivors then
cleared the corroboration gate and went active, `"MDA"` now annotates to the
survivor, and the graph rebuild excludes the losers.
