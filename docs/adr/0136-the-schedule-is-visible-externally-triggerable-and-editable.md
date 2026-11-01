---
status: accepted
date: 2026-08-18
---

# 2026-08-18 — The schedule is visible, externally triggerable, and editable without a restart


Three gaps in the scheduling story, closed together ahead of moving off the
laptop onto a managed host where the process is not guaranteed to be awake at
fire time.

**`/api/health` carries a `schedule` block; there is no `GET /schedule`.** The
frontend's transport for polled state is the 30s health poll, and the block is
cheap: scheduler introspection plus one indexed-scan query for the last
successful daily. `next_run_at` prefers the live job's `next_run_time` — the
thing that will actually fire — and falls back to computing from the yaml cron
when there is no job (tests, offline, or `enabled: false` with an external cron
driving). A bad cron string yields `next_run_at: null`, never a 500: health is
the endpoint that must answer when things are broken. The Runs masthead renders
it as a fifth stat ("in 14 hours" / "off" / "—" for offline replay).

**`POST /api/runs/schedule/trigger` fires the daily exactly as the timer
would.** It awaits `run_daily(request.app, request=request)` — the same
function APScheduler's job calls, so the cross-process lane check, launch
through `run_launcher`, and the notify heartbeat all come along. It exists for
a hosted cron (with the root bearer) and its semantics follow
from that caller: a busy lane is **202 `skipped`, not 409**, because a cron
treats non-2xx as a failed job worth retrying — straight back into the same
busy lane. That extends the 2026-08-05 skip-not-queue decision to the wire.
It deliberately ignores `schedule.enabled`: on a host where the process sleeps,
you set `enabled: false` and the cron *is* the schedule. `POST /api/runs
{"kind": "daily"}` could nearly do this, but it 409s on conflict and skips the
notify wiring — the two deltas are the endpoint's whole justification. A
misconfigured deploy running both the in-process timer and the cron is benign:
the second arrival skips on the lane conflict.

**A settings save re-points the job in place; `schedule.*` left
`RESTART_FIELDS`.** `save_config` calls `scheduler.apply_schedule` after
rebinding `app.state.settings`. The mechanics forced one structural choice:
`save_config` is a sync route running in a threadpool, and
`AsyncIOScheduler.start()` needs a running loop there is no way to reach from
one — while `add_job`/`remove_job` on an already-started scheduler are
thread-safe (jobstore RLock; the wakeup crosses via `call_soon_threadsafe`). So
`start_scheduler` now always constructs and starts the scheduler when not
offline, jobless when disabled, and enable/disable is job add/remove.
`app.state.scheduler` being truthy therefore means "not offline", not "the
schedule is on" — ask the job. Two guards came with this: `_validate_settings`
round-trips `CronTrigger.from_crontab`, because with a live re-point the bad
string would otherwise reach the scheduler *after* the file was written; and
re-enabling via a save does not run `catch_up` — a settings edit that silently
starts a forty-minute pipeline is worse than waiting for the window, and the
trigger endpoint covers the impatient case.

*Trigger to revisit:* a second scheduled job kind (the "curation is stage five"
entry's collision reasoning applies before adding one), or the host's cron
needing per-fire idempotency beyond what the lane skip provides.
