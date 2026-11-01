---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Run history is hidden, never deleted


**Measured, and the first design was wrong.**

The ask was "clear run history". The obvious implementation is `DELETE`, guarded
so it cannot destroy provenance: refuse to delete a run that any `documents`,
`assertions` or `briefs` row points at.

Both halves of that fail against this database.

`pipeline/orchestrator.py` reports `failed` only when **no** stage committed
anything; anything that got past `fetch` is `succeeded_partial` and has
`documents` rows. On the live database all nine runs were `succeeded_partial`,
so the guard would have refused every one — including the single most deserving
row, a 25-minute run falsely marked failed by the reap bug that migration 0008
fixed, protected forever because it carried 90 assertions.

The stated motivation fails too. `run_events` was ~773KB, and 99% of it was
`model_message` rows on exactly the runs the guard would keep. The guard and the
motivation were mutually exclusive.

And the hazard it defended against is softer than it looks: nothing in the
backend ever resolves `documents.run_id` or `assertions.run_id` back to a `runs`
row, and `services/seed.py` already writes assertions under `run:seed-0001` with
no `runs` row at all. Every seeded database is already in the state the guard
existed to prevent.

**So: `hidden_at` (migration 0013), not `DELETE`.** Nothing is destroyed, the
provenance question disappears entirely, and unhiding is `NULL`. The reference
set that a deletion design would have had to enumerate — and which the first
draft got wrong by omitting `sightings.run_id`, which is `NOT NULL` and written
for already-known articles — stops mattering.

**Still open:** this does not address `run_events` growth. The operation that
would is trimming `model_message` events from *retained* runs, which C6's
`after_seq` replay already tolerates. Deliberately not in this change.

**Consequence:** the liveness gate is `runs.status IN ('running','queued')` plus
`run_service._process_is_alive(owner_pid)` — **never** `registry.active_run_id`.
The registry is an in-process `asyncio.Lock` and `venona run` is a separate
process; trusting it here would reproduce migration 0008's bug one layer up.
