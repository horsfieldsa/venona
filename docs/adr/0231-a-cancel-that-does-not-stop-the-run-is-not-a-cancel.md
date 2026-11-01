---
status: accepted
date: 2026-09-13
---

# 2026-09-13 — A cancel that does not stop the run is not a cancel

`POST /api/runs/{id}/cancel` set a flag and answered 202. Stages check that flag
between items, so a run inside a model call noticed nothing until the call came
back. `tests/test_run_lanes.py::test_health_says_a_cancelled_run_is_still_running`
already recorded what that cost, observed 2026-08-25: still the active run ten
minutes after an accepted cancel, spending throughout, and it ended only when
the process was brought down.

The cooperative design was not wrong about the risk it was managing — a stage
stopped mid-write leaves half-written state, and letting it reach its next check
is how a run records what it committed and writes a considered terminal row. It
was wrong that this is the only thing a person can be given. Someone pressing
cancel is usually pressing it because the run is doing something expensive that
they want stopped, and "it will stop at the next convenient moment, which may be
several minutes away, and we will not tell you which" is not an answer.

## The decision

**Cooperative first, forced after `CANCEL_GRACE_SECONDS` (5s).**

`RunRegistry.cancel` sets the flag as before and arms a timer. A run that
notices within the grace unwinds through `RunCancelled` exactly as it always
has. A run that has not stopped by then has its task cancelled, which is the
path `llm/executor.py::_reap` already kills the subprocess on — so the paid call
stops rather than running to completion for an answer nobody will read.

Three things follow from it, and each is load-bearing:

- **A second cancel forces at once.** The escape hatch when five seconds was not
  enough, and what makes the endpoint idempotent.
- **A forced run still gets a terminal row.** `execute_run` writes its own row
  *after* its try/finally and the launcher skips its write for a self-terminating
  run, so a task cancelled inside it reached neither. Without settling the row
  in `_runner`'s `CancelledError` handler, forcing a research run would have
  left it `running` forever with its stage still running under it.
  `abandon_run` takes the status for this; `_settle_open_stages` closes the
  stage as `interrupted`.
- **A run stopped on purpose is not a failed run.** `handle.cancelled` is what
  tells a person's cancel from a uvicorn shutdown, and only the second one
  writes an error.

**And the run says it is stopping.** `/api/health` reported
`active_run_cancelling` and nothing read it. Every run in flight now carries
`cancelling`, so the Runs page can say so on the row and on the button —
because the grace means there is a window where the honest answer is "asked,
not yet stopped", and a row that keeps reading `running` through it leaves the
person who pressed cancel unable to tell a slow stop from a dead button.

**The press asks first.** Cancelling throws away work already done and paid
for. It is the one control on that page that destroys rather than starts, and
it was the only one firing on a single press.

## What this costs

- **The ledger entry for the call in flight.** Killing the subprocess loses the
  record of spend that has already happened — `_reap`'s own docstring names
  this. Stopping the spend is the point of the button; the alternative is
  waiting out the call we are paying for.
- **The stage's bookkeeping.** Same as a cooperative cancel: the run reports
  `cancelled` and the open stage row is settled as interrupted.
- **It is a floor, not a ceiling.** A run inside a long *synchronous* call
  finishes that call first, because `task.cancel()` lands at the next await.
  That is another reason the state is shown rather than assumed.

## Changing this requires touching

- `services/run_service.py` — `CANCEL_GRACE_SECONDS`, `RunRegistry.cancel`,
  `_arm_force`/`_force_after`, `release`, `abandon_run`
- `services/run_launcher.py` — the `asyncio.CancelledError` handler and the
  terminal `done` it emits for a forced run
- `api/routers/runs.py::cancel_run` (async, because it arms a timer),
  `api/routers/meta.py::_run_brief` and `api/schemas/meta.py::RunBrief`
- `web/src/stores/runs.ts` (`cancellingIds`, `isCancelling`),
  `web/src/views/RunsView.vue`, `web/src/composables/useAppPolling.ts`
