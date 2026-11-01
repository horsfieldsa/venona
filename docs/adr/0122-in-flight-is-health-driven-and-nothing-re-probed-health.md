---
status: accepted
date: 2026-08-17
---

# 2026-08-17 — `In flight` is health-driven, and nothing re-probed health


Starting a run left the Runs page unchanged until the next scheduled tick, which
is 30 SECONDS (`useAppPolling`). Reloading appeared to fix it because
`onMounted` ticks immediately — so it read as a cache when it was a poll that had
not come round yet.

Two things kept it hidden. The 3s live cadence keys off `runs.lightRuns`, so a
research-lane run — `on_demand`, `daily`, `rewrite` — never sped the loop up at
all. And `trigger()` did refresh the HISTORY list, so the row appeared under
`History` while `In flight` stayed empty: a run that reads as already finished
rather than as one that has not been noticed.

**`runs.launched()`, called after every launch.** It refreshes health and the
history list together, because they answer different questions and the page
shows them side by side. There were nine launch sites and not one of them
touched health; a tenth is a matter of time, so this is the one place that knows
what to do about it.

**The same call on `onDone`.** The other edge has the same asymmetry: refreshing
history alone left a finished run sitting in `In flight` for up to another 30s
with its counters frozen, which reads as a run that hung.

The poll cadence is deliberately unchanged. A research run streams over SSE once
health reports it, so 30s is only ever the *discovery* latency and `launched()`
removes that; making the loop fast for a tens-of-minutes run would mean ~400
extra `/health` probes, each of which touches Neo4j and the claude CLI.

`ui_smoke` presses Rewrite with `fetch` stubbed for that one call and asserts a
`/health` request follows. The request never leaves the browser — letting it
through would start an opus brief call.
