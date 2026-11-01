---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Briefs are identified by generation, not by calendar day


`pipeline/brief.py` minted `brief_id = f"brief:{date}"`, and `briefs.id` is the
PRIMARY KEY. The date WAS the identity, so a second run in a UTC day was never a
second brief — it was an UPSERT over the first, followed by `DELETE FROM
brief_blocks / brief_items / brief_clusters`.

The live database made the cost concrete: **six brief-producing runs, one
`briefs` row.** Five briefs destroyed with no archived copy, and five
`runs.brief_id` values left pointing at a later run's content, so the run detail
view misreported what each run had produced. No `brief:2026-08-05` row existed at
all — that run started at 22:47Z and reached its brief stage after midnight.

**The reported symptom was the smallest part of it.** "The new brief isn't
showing" was `stores/briefs.ts` skipping `loadLatest()` because
`current.id === latestId` — true across same-day runs precisely because the id
never changed. A hard refresh always recovered. The data loss underneath did not.

### The defect inside the defect

Retrieval excluded already-briefed clusters with:

```sql
NOT EXISTS (SELECT 1 FROM brief_clusters bc
             WHERE bc.cluster_id = c.id AND bc.brief_id IS NOT ?)
```

`?` was the brief being written, exempted so that regenerating a date could
re-retrieve its own clusters. With **one** brief row in the table that predicate
is false for every row, the `NOT EXISTS` is vacuously true, and nothing is ever
excluded. `clusters_used` across the six briefs: 12, 8, 11, 12, 12, 12. The
reader was served the same top stories six times in a day.

That is the exact failure the 2026-08-05 entry above records as FIXED. It was
fixed in intent and defeated by the id scheme, and no test caught it because the
test asserted the carve-out rather than the outcome.

**Consequence:** `brief:{date}:{uuid7}`. The carve-out is deleted — a brief now
owns no `brief_clusters` rows when it retrieves, so it needs no exemption.

### What this cost, and what was deliberately kept

- **The `--only brief` tuning loop breaks**, by design. A second pass no longer
  re-retrieves; it retrieves the NEXT twelve clusters and writes a different
  brief. The replacement is explicit: `venona briefs delete <id>`, then re-run.
  Deleting releases the clusters, which is the whole reason the command exists.
- **`venona briefs delete` really deletes**, unlike `venona runs hide`. Migration
  0013 argues that deleting a run orphans `documents`/`sightings`/`assertions`
  rows. A brief owns its children outright, and a soft hide would leave
  `brief_clusters` burned forever — defeating the point.
- **`_persist` keeps its three DELETEs** even though a fresh id has no children.
  `SeededIdGen` is a per-instance counter, so two `venona brief` processes under
  `VENONA_OFFLINE=1` mint the same id; `brief_blocks.id` is a PRIMARY KEY whose
  suffix restarts at `b1`. Without the DELETEs that raises `IntegrityError` from
  *below* the `try` in `run_brief` — unwinding a run that has already paid for
  the Opus call, which is the failure that `try` exists to prevent.
- **`_recent_threads` moved from `b.date < ?` to `b.generated_at >= ?`.** Keyed
  on the date, a second brief in a day could not see the first, so it could not
  mark anything `follow_up` and would re-explain a thread the reader was told
  about an hour earlier. It compares against a millisecond-precision cutoff:
  `_cutoff` emits whole seconds, `generated_at` carries milliseconds, and
  `'…00.500Z' < '…00Z'` as TEXT because `.` sorts below `Z`.
- **No backfill.** Existing `brief:YYYY-MM-DD` rows keep their ids. The id is
  opaque — anything date-aware reads the `date` column — and rewriting them would
  break `brief_blocks.brief_id` and every stored span reference for nothing.

`"nothing analyzed yet -- run \`venona analyze-run\` first"` now distinguishes
itself from `"nothing new since the last brief"`. With one brief per run, the
latter is the ordinary outcome of a second run in a day, and sending the operator
to `analyze-run` for it was a diagnosis of a working pipeline.
