---
status: accepted
date: 2026-09-14
---

# 2026-09-14 — Hide runs, never delete them

A run the operator is finished with is hidden by stamping `runs.hidden_at`. No
surface deletes a `runs` row.

**Deleting is not safe in the way it looks.** `documents.run_id`,
`sightings.run_id` (NOT NULL), `assertions.run_id` (NOT NULL) and
`briefs.run_id` all carry a run's id and **no `run_id` column anywhere carries
a `REFERENCES` clause** — re-checked 2026-09-14, `grep "run_id.*REFERENCES"
backend/venona/db/migrations/` returns nothing. Deleting the row leaves every
one of those pointing at nothing, and invariant 1 makes Postgres the record of
everything. That no query joins them back today is a weak thing to bet an
intelligence system's provenance on.

**A deletion gate that protected provenance would refuse almost every row worth
removing.** `orchestrator.py` reports `failed` only when NO stage committed, so
anything past `fetch` is `succeeded_partial` with `documents` rows attached.
Measured 2026-08-06 on the live database: 4 of 9 runs were deletable and all
four were runs nobody had complained about, while the single most deserving row
— a 25-minute run falsely marked failed by a reap bug — carried 90 assertions
and would have been protected forever.

**`hidden_at` is a timestamp, not a boolean**, because when somebody decided a
run was noise is worth keeping, and because unhiding is then a plain `NULL`
rather than a second flag.

This does not address `run_events` growth, which is almost entirely
`model_message` rows on runs that stay visible. Trimming transcripts on retained
runs is the operation that reclaims that, and it is deliberately separate.

**Consequence.** The hiding block in `api/routers/runs.py` keeps the
load-bearing sentence and cites this record; `0001_baseline.sql` carries the
surviving `-- Hide, never delete.` column comment. The argument was written in
`0013_run_hidden.sql`, which the 2026-08-17 Postgres squash removed, so until
now it existed only in git history.
