---
status: accepted
date: 2026-09-13
---

# 2026-09-13 — One lifecycle, however many stages

A run's lifecycle had four implementations. `pipeline/orchestrator.py` opened
six stages and settled its own row. `services/run_launcher.py` wrapped one unit
of work, optionally in one stage, and settled every other kind. `setup_service`
went through the launcher with no stage and emitted five of its own.
`cli._research_run` went through none of it — no stage events, no counters, no
notice — against C11's own rule that every run starts through `launch`.

Each was right about itself. That is what made the divergence invisible: there
was nowhere the sequence was written down, so every difference read as a local
choice rather than a drift. What they actually produced, on production the day
this was written:

| | stages started | counters flushed | step row while live |
|---|---|---|---|
| `daily` | 6 | 4 | yes |
| `research` | 1 | 1, in its final second | no |
| `lookup` | 1 | 1, in its final second | no |
| `domain_setup` | 5 | **0** | no |
| `venona research` | **0** | **0** | two rows for a one-stage kind |

## The decision

**A run is a sequence of one or more stages, and a single-stage run is the
degenerate case.** Lane decides concurrency. `len(stages)` decides rendering.
Nothing else may differ.

`services/run_scope.py` owns the sequence: the `runs.stage` pointer, a
`running` row, `stage_started`, a cancel check, then the terminal row,
`stage_finished` and a counter snapshot. `open_stage`/`close_stage` are the same
sequence for a body that cannot be a `with` block, which the wizard's steps
cannot be — each sits inside its own `if` and `try`.

It lives under `services/` because `run_service` imports `pipeline.types` and
not the reverse, so a scope under `pipeline/` would close the cycle the moment
it needed `record_stage`. It declares no stage list, because `RunType.stages`
and `orchestrator.STAGES` already answer two different questions and a third
would be the one that drifts.

**`run_launcher` is the only writer of a terminal row and a `done` event.**
`execute_run` runs stages and returns what it reached; it no longer builds an
executor, an http client, a context or a budget, and `_abort_before_start` is
gone. `self_terminating` is gone with it, and so are the rules it needed: the
forced-cancel backstop, the two `abandon_run` calls, and the notice raised by
reading the row back rather than from the outcome in hand. A second writer needs
a second set of rules for when the first stands back, and each of those was a
way for a run to end unsettled.

The CLI reaches `launch` through `run_launcher.host(settings, **state)`, which
supplies the three things it reads off `request.app.state`. C11's rule has no
standing exception now.

## Three things that stop depending on which path a run took

**`RunContext.stage` is the one source for attribution.** The event log,
`run_metrics` and the `llm_calls` ledger all read it. Each used to be filled by
whoever happened to know a name, which is how a corroboration sweep — it
analyses what it brings back inside its own `research` stage — ledgered every
one of those calls under `analyze`, a stage it never opened, while `run_stages`
recorded `research`. Two honest answers to one question, and the metrics page
rendered the phantom. `STAGE_FOR_TASK` survives as the fallback for a call made
outside any stage. Historical rows are not backfilled: they were written
honestly under the rule of their day.

**A counter snapshot after every stage, unconditionally.** The event carries the
whole of `RunCounters`, so it is idempotent and an extra costs a row.

**A spend ceiling on every run.** `max_calls_per_run` bound only the kinds whose
work closure happened to build a `Budget` — `lookup`, `personal_brief`,
`anchors`, `ingest` and `questions` ran with none, so the knob that reads as a
safety limit enforced nothing for five of sixteen kinds.

## What did not become uniform, deliberately

**Light runs do not get more stages than they have.** A `lookup` has one. Three
places in this codebase argue that giving it a position on a six-segment track
states something false, and they are right: the uniformity worth having is in
the GUARANTEES, not the segment count. `RunType.detail` is now derivable from
`len(stages)` and is asserted to agree with it.

**Lane still decides concurrency, and only that.** C11's refuse-versus-queue
rules, the coalescing and the single research slot are untouched.

## The one thing this unblocked

`on_wait` and `wait_abort` moved from the executor to the context. `executor_for`
returns one object per LANE, so as instance attributes they pointed whichever
run started last at its own stream while reporting the others' waits into it,
and let a cancel on one abort another's sleep. They are per-call now, which is
what lets a light run draw the difference between waiting for a provider and
working — for the three-slot lane as much as the one-slot one.

Extends 0183 (status is whether the machinery worked) and 0194 (three channels
for what a run has to say). Supersedes the `self_terminating` half of C11.
