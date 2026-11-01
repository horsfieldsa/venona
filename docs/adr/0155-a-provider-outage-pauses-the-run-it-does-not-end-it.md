---
status: accepted
date: 2026-08-21
---

# 2026-08-21 — A provider outage pauses the run; it does not end it

Anthropic's status page records a major "elevated errors on requests to
multiple models" incident on 2026-08-20, 19:16–19:42Z, Claude Code affected.
No venona call was in flight, so production was not hit. Tracing what *would*
have happened at 06:00 found four things, each of which turned a 26-minute
incident into a lost day — and one of which meant nobody would have been told.

**The breaker refused the rest of the run in seconds.** Six consecutive
failures open the circuit (two clusters, three attempts each); every call for
the next 120 s raised `BreakerOpen`; the half-open probe failed and restarted
the cooldown. A run of a hundred calls drained as refusals inside a minute.

**`BreakerOpen` bypassed every degrade path.** It is deliberately not an
`LlmError` (nothing was asked), and `test_breaker_open_is_not_an_llm_error`
pins that — but every `except LlmError` that turns a failed call into a skipped
cluster or a missing brief therefore let it through, and the run aborted at the
first refusal with `curate` and the projection behind it unrun.

**Health reported the wrong breaker.** `execute_run` built its own executor,
so `/api/health`'s `circuit` — the thing ADR 0135 says "knows whether calls are
actually landing" — was the state of an instance the run never used.

**Production was blind.** Alembic's `env.py` calls `fileConfig(alembic.ini)`,
which disables every existing logger and installs a WARNING-level root
handler. `db.migrate(conn)` runs in the lifespan, at 06:00, and per launched
run; after the first, `venona.*` and `uvicorn.error` emitted nothing. Two days
of production logs held four runs and zero application lines. And
`VENONA_NOTIFY_URL` was unset, so every alert and the success heartbeat were
no-ops.

## Decided

- **The breaker classifies.** `record_failure` marks the circuit `transient`
  when `budget.is_retryable` says the provider said "not now" (429, 5xx, 529,
  overloaded, timeout). A 401 is terminal: waiting buys nothing.
- **The research lane waits; the light lane does not.** `ClaudeCliExecutor`
  takes `outage_wait_seconds` (default `llm.outage_wait_seconds` = 1800).
  `_admit` sleeps until the next half-open probe and becomes it, for as long
  as the budget allows *measured from the start of the episode*, so retries
  cannot each buy a fresh half hour. The light executor passes 0: a person is
  watching that spinner. A cancelled run ends the wait through `wait_abort`.
- **A refusal is a failed call at every call site.** `call_and_record` returns
  an error `LlmResult` (and ledgers it); `analyze_document` returns a failed
  outcome. Every existing `is_error` handler then does what it already did.
  `BreakerOpen` stays a `RuntimeError`.
- **One executor per lane, shared.** `execute_run` takes `llm=`; the API and
  the scheduler pass `app.state.llm`. Health now reports the circuit the
  nightly run trips. The CLI and recording still build their own.
- **The daily re-fires after an outage on its watch.** `run_daily` compares
  `breaker.opens` before and after; a run that did not finish `succeeded`
  after the circuit opened schedules one `DateTrigger` retry 30 minutes out,
  up to three times, resuming by state. The alert names the retry time.
- **Programmatic migrate leaves logging alone.** `db.core.migrate` sets
  `config.attributes["configure_logging"] = False` and `env.py` honours it.
  `test_migrate_keeps_logging` pins it.
- **`VENONA_NOTIFY_URL` is declared in the deployment config** (`sync: false`), so the
  absence is a visible empty secret rather than an omission.

## Addendum, 2026-08-22 — the probe was the outage

Measured with a research run and two lookups in flight: `/api/health`
reported `ok: false, error: "version probe failed: unable to perform
operation on <WriteUnixTransport closed=True …>; the handler is closed"` on
every refresh for the length of the run, while every real call succeeded and
the circuit stayed closed. The Runs page rendered that as "claude down · runs
paused" and disabled every spend control — which is what the operator had
been reporting as Claude going down whenever runs overlapped.

Cause: `_run` spawned the probes with `stdin=PIPE` and `communicate(b"")`
**writes** to it; `claude --version` exits without reading stdin; under
uvloop, on a loop busy pumping four streaming calls, the pipe-closed callback
lands before the write, and the write raises `RuntimeError` from the closed
transport — which CPython's `_feed_stdin` does not swallow (it catches only
`BrokenPipeError` and `ConnectionResetError`). Not CPU, not memory (four
`claude` processes held the instance at 764 MB of 2 GB, CPU peak 0.32).

Decided: a probe opens **no stdin pipe** (`DEVNULL`); `_run` with content
and `_run_streaming`'s stdin pump treat that `RuntimeError` exactly like
`BrokenPipeError`; and a probe is tried **once more** before `ok: false` is
reported — one failed spawn is a hiccup, two is a missing binary.
`tests/test_llm_probe_under_load.py`.

## Not decided

Web-grounding fetches
from production to `defense.gov`, `congress.gov` and `dtic.mil` fail with
`ConnectError: All connection attempts failed` — those hosts appear to refuse
cloud egress — which degrades lookups and is unrelated to the model.

*Trigger to revisit:* an incident longer than `outage_wait_seconds` plus three
retries, or the light lane needing to wait (a queued lookup rather than a
watched one).
