---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — Counts are evidence, not bookkeeping


`store_definition` and `mark_undefined` both wrote `sighting_count = 1`. The
review queue orders by that column, so grounding an acronym buried it under
every undefined one.

Live: `UAS` had been seen 11 times, was grounded from an FAA page, and came back
reading `1×`. Grounding an acronym does not un-see it.
