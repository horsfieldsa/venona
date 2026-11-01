---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — The seed is identity, never definitions


727 of 1,036 resolved surfaces on the first run MINTED a new entity — seven in
ten times the model met a name, the resolver had never seen it. That is where 73
open conflicts and 620 provisional entities on day one came from, and it is why
47 of 727 entities had a definition and 93% of hover cards were empty.

`profile/anchors.yaml` now declares 264 entities with 663 aliases. It is the
only live-safe channel: `venona seed` also loads a fictional brief and four
invented documents, so `make bootstrap` (wipe + anchors) is what a real instance
starts from and `make reset` (wipe + seed + anchors) stays the development path.

It compounds three ways, all through machinery that already existed:
`resolve.py` rung 1 short-circuits on an exact alias; `prompts.gazetteer` shows
the model active entities ordered by relevance and was running at 101 of its 400
cap, so this improves EXTRACTION before resolution is reached; and
`alias_index` gives seeded aliases real strictness tiers, so briefs and the
extension annotate correctly from run one.

**Identity, never definitions.** Invariant 3 is absolute — a definition exists
only if it is traceable to a sentence in a document — so nothing seeded carries
one, and `summary` is deliberately absent. A seeded entity renders as visibly
undefined until a real article defines it. Seeding definitions with a captured
citation was considered and rejected: it satisfies the letter of the invariant
and erodes the habit that keeps it true.

**`make reset` did not load anchors**, which was a trap worth naming: a wipe
silently discarded the trunk, and the loss is invisible because the pipeline
just starts minting its own ids again and the graph still looks populated.
