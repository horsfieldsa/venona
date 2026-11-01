---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The transcript did not auto-update: the stream never tailed


Reported as "I was hoping the embedded view would auto-update". It never could.

`stream_events` replays the durable log, then calls `registry.subscribe(run_id)`
for live events. `registry` is an in-process `asyncio.Lock`, and `venona run` is
a **separate process** — so `subscribe()` returned None for every CLI-launched
run. The code read that as "the run already finished", synthesised a `done`
event carrying the run's CURRENT status (frequently the non-terminal
`"running"`), and the browser's `EventSource` closed on it. Replay, then silence,
permanently.

Third occurrence of one root cause this session, after `reap_interrupted` and
`RunRegistry`: **nothing knew which process owned a run**, and the API server
kept assuming it owned all of them.

The fix is the design already written at the top of this module — *the SSE
stream is a view over an append-only log in SQLite, not the log itself.* If no
one in-process owns the run, tail the log: poll `events_since` every second and
stop on `done`, on a terminal status, or when `owner_pid` is no longer alive.
That last condition matters; without it a client watching a crashed run holds an
open stream forever.

Verified live against a running CLI pipeline: resuming from seq 136, the API
server delivered 37 new `model_message` events up to seq 173 over 45 seconds for
a run it did not own.

Polling rather than a notify channel because SQLite has no LISTEN/NOTIFY, the
reader is local, WAL-mode and uncontended, and a run emits a few events per
second at most. A second mechanism would be more moving parts for latency
nobody can perceive.
