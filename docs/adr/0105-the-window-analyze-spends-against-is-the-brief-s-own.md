---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — The window analyze spends against is the brief's own


**Measured, on the first full run.** `run:06a8160f`: 93 minutes, $19.74, 152
model calls, and a brief titled *"A thin day"*.

221 documents kept, of which **8** were inside `lookback_hours`. 112 clusters
analysed at ~$0.117 each; **8** of them inside the window the brief retrieves
by. So 104 analyze calls — ~$12.19 and ~71 of the 93 minutes — bought clusters
that could not have reached a brief under any ranking.

The cause was not ordering. `pending_clusters` was already newest-first and
worked back from 2026-08-16 to 2026-08-07 exactly as intended. What it lacked
was a STOP CONDITION: `lookback_hours` was enforced only in
`brief.build_retrieval`, at the very end, after the money was spent.
`analyze.pending_clusters` had no date predicate at all.

This was already known and already written down. `brief.py`'s
`_retrieval_funnel` carried the sentence *"203 of 216 documents were outside
`lookback_hours` — 94% of the analyze spend could not have reached a brief under
any ranking"*, and `run_source_stats.fresh`/`stale`/`undated` were added by
migration 0023 specifically to make it visible. The measurement shipped; the
filter did not. That gap is the actual lesson here — an instrumented defect with
no gate is a defect you pay for every night, with a chart of it.

**Consequence:** `pipeline.types.retrieval_window` is the single resolver, used
by both stages. Not two readings of one setting — one function, because two
readings drift and nothing fails when they do. `tests/test_analyze_window.py`
asserts the property (`retrieved <= analyzable`) rather than a number.

Three smaller things fell out of the same run and are fixed alongside:

- **Undated clusters were sorted last and briefed first.** `ORDER BY
  COALESCE(published_at, '0000') DESC` sank them below every dated row, while
  `build_retrieval` treats a missing `published_at` as always in-window. All six
  undated clusters were deferred at the cap; five cleared every other brief gate.
- **`MIN_BRIEFABLE_CHARS` moved to `pipeline/types.py`.** It reads like a
  presentation rule and is really a bound on spend: a document the brief will
  refuse for being 548 characters is one analyze must not pay to score.
- **A first brief widens to `first_run_lookback_hours` (7 days).** A reader with
  an empty database has no yesterday. Keyed on `COUNT(*) FROM briefs`, not runs:
  a run that fetched and skipped the brief has not spent that budget.
