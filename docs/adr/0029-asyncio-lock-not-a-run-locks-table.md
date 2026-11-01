---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — `asyncio.Lock`, not a `run_locks` table


One active run at a time is enforced in-process. This is a single-user,
single-process application, so a database lock would be ceremony guarding a race
that cannot occur.

The scheduler **skips** rather than queues when a run is already active: if
yesterday's run is somehow still going at 06:00, starting a second one makes the
problem worse.

*Scoped, 2026-08-07 — see "One slot was the wrong granularity" below.* The lock
is still an `asyncio.Lock` and the scheduler still skips; what changed is that
both now ask about the **research lane** rather than about runs in general.
