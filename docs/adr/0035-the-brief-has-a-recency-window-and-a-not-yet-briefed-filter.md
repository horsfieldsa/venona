---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — The brief has a recency window AND a not-yet-briefed filter


`build_retrieval` selected *every analyzed cluster ever*, ranked by score.
`settings.pipeline.lookback_hours` was declared and read nowhere. From day two
yesterday's high scorers outranked today's news and the "daily" brief repeated
itself — the single worst defect in the shipped product, and it was on nobody's
list.

Both filters, not one:

- a lookback window alone repeats on a same-day re-run
- exclusion alone reaches back weeks for unbriefed filler after a quiet stretch

Exclusion needs `brief_clusters` (migration 0003). `briefs.source_ids` was the
obvious alternative but records DOCUMENTS, not clusters, and only the canonical
one per cluster — so a story that arrived again from a second outlet would look
unbriefed.

`_persist` DELETEs a brief's rows before inserting, so regenerating today's
brief reuses its own clusters rather than permanently burning them.

The window is computed from the caller-supplied `now`, never `datetime.now()`,
so `FrozenClock` produces a stable window offline.

*Superseded, 2026-08-06.* The self-exclusion carve-out described above is gone,
and it never worked. See "Briefs are identified by generation" below: with one
brief row in the table, `bc.brief_id IS NOT ?` was false for every row, so the
exclusion clause was vacuously true and NOTHING was ever excluded. The half of
this entry that survives is the lookback window; the "not-yet-briefed" half only
started working when brief ids became unique. Releasing clusters is now
`venona briefs delete`, not a side effect of regenerating a date.
