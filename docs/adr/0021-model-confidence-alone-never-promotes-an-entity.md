---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Model confidence alone never promotes an entity


**Measured on a real corpus.** The promotion gate originally read "corroborated
by >=2 sources **OR** a single extraction the model rated >= 0.9 confidence."
Run against 15 real articles, that produced:

- **6** entities genuinely corroborated by two independent sources
- **48** promoted on a single self-reported high-confidence extraction

Self-reported model confidence is not calibrated — it is 0.9–1.0 for nearly
everything it extracts, so the `OR` branch defeated the gate entirely.
"Chaos 1", "5N Plus" and "COMSPOC" became active graph nodes from one mention
in one article each.

**Now:** corroboration is the only path. `promote_provisionals` also DEMOTES,
so status is derived from current evidence rather than being a one-way latch —
otherwise a retraction leaves a stale active node behind. Seed entities are
exempt: they are active by declaration, not by evidence.

The cost is that a genuinely new entity waits for a second, independent
sighting before becoming a graph node. That is the right trade — the article
still scores and still appears in the brief, and the name lands in
`unresolved_mentions` as a curation prompt. A polluted graph is far more
expensive than a late one.

After the fix, the same corpus yields 39 active entities (37 seeded + 2 earned)
and 114 held provisional.
