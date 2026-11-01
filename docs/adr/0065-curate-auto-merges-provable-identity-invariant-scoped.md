---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — `curate` auto-merges provable identity (invariant scoped)


**Ask:** "how do we codify the merging so it happens as we run?"

That runs straight into a prohibition: *"Never auto-merge two high-similarity
entities… an incorrect auto-merge is invisible and poisons every downstream
query."* The rule is right, and it is about SIMILARITY. The live queue was not
similarity. Classifying all 29 open duplicates:

```
same name    12   identical once a trailing "(ACRONYM)" gloss + hyphens normalise
                  `Space Systems Command (SSC)` IS `Space Systems Command`
acronym of   16   one name IS an acronym the other GENERATES via acronym_variants
                  `DHS` <- `Department of Homeland Security`
human         1   `Maj. Gen. Partick Ellis` / `Patrick Ellis` — a typo
```

28 of 29 are equal by construction. The 29th is a similarity judgement and stays
in the queue — that boundary is the entire argument.

Both premises behind the ban had also lapsed. A merge is now **reversible**
(`entity_mappings.active = 0`) and **visible** (`venona entity merges`, plus the
conflict row's `resolution='merged'`, which unmerge reopens). "Invisible and
poisons everything" described a mechanism that no longer exists.

The dangerous case never reaches the path: `DIA` — Defense **Intelligence**
Agency vs Defense **Investment** Agency — is filed `collision` by `_classify`
*because* both names generate the acronym. Only `kind='duplicate'` is eligible;
containment (`Thales` / `Thales Australia`) is excluded too.

**Ordering matters.** The merge step runs BEFORE `promote_provisionals` in the
same pass, because a merge consolidates evidence across the closure — which
`promote_provisionals` already aggregates over — so two single-article entities
can carry the survivor over the gate immediately rather than a day later.

**Survivor is the cleanest readable form**: the expansion over the bare acronym,
the form without the parenthetical gloss. The loser's aliases move with the
merge, so `DHS` still resolves and still hovers; only the display name changes.

### First live pass, zero model calls

```
merged      28 pairs (23 distinct entities)
promoted    11 entities
graph       102 -> 99 edges
```

**The edge count went DOWN, and that is the correct outcome.** Merging collapses
two nodes into one, so edges that pointed at both fold into a single canonical
edge: 472 raw triples became 467. Verified against Neo4j — 99 projected, 99
reported. The graph got more accurate rather than larger; the compounding win is
the 11 newly-active entities, whose future relations will project.

Chains are handled: three CSIS entities existed, so `identical_pairs` resolves
both ids through `entity_mappings` mid-pass. Without that, the second hop targets
an entity already merged away and `merge_entities` refuses it.
