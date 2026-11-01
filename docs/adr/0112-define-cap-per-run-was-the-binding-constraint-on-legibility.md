---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — `define_cap_per_run` was the binding constraint on legibility


Raised 25 → 100. At 25 the first run ended with 47 of 727 entities defined, so
93% of hover cards were empty against a product whose first promise is that
every program and acronym is hoverable.

25 was the right number while `analyze` was draining the whole call budget and
`define` was deferring work for lack of it. With analyze bounded to its window
the budget is there, and at the measured $0.014 per call this is ~$1.40 a run —
the cheapest remaining thing that changes what a reader sees.

Worth stating that the downstream stages were never *too many chains*, which was
the initial suspicion. They were never REACHED: acronyms deferred 8 lookups,
define deferred 9, and adjudicate left 60 of 73 conflicts untouched, all because
analyze had spent the budget first. Bounding analyze means they now run, at a
cost of roughly 36 calls.
