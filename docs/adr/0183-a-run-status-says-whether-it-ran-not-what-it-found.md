---
status: accepted
date: 2026-08-31
---

# 2026-08-31 — A run's status says whether it ran; a new field says what it found


`runs.status` answered two questions with one word.

`succeeded_partial` was built for the first: the run degraded. The budget ran
out, two feeds 403'd, Neo4j was down and the projection was skipped. CLAUDE.md
has been explicit since the beginning that reporting such a run as `failed` is a
bug, because it teaches you to ignore the status field.

Then every site that needed to say something else reached for the same word,
because there was nowhere else to put it. Not "this degraded" but "the machinery
was fine and the answer was no" — the web holds no definition for this acronym,
the window had nothing new to write about, this article was already in the
corpus, this reader's brief was already current. Eight separate sites did it,
and three of them said so out loud in comments while doing it:

> a plain `succeeded` is why a lookup that grounded nothing looked exactly like
> one that worked
> — `api/routers/entities.py`

> Two different outcomes wear one status until they are separated.
> — `api/routers/glossary.py`

> reporting `succeeded` would tell the reader their click did something it did
> not
> — `api/routers/ingest.py`

## What that cost

On the defence corpus, **44 of 68 lookups** read `succeeded_partial` with an
empty `warnings` array and a null `error`. Mechanically indistinguishable from
the 24 that worked. The most-run kind in the system had a status column that was
uninformative on two rows in three, and a reader who learned to ignore it was
reading it correctly.

The damage was not confined to a rendering. Four consequences were live:

* **`StatusChip` gave up its colour.** Its comment records why: *"On real
  history 8 of 9 runs are partial, so an amber pill here fired on 89% of rows —
  a signal that frequent is a background colour."* A real degradation had no way
  left to be loud.
* **`stores/curation.ts` guessed, and got it wrong.** It rendered
  `runStatus === 'succeeded' ? 'lookup finished' : 'lookup found nothing new'`,
  so a lookup that exhausted its call ceiling told a curator the web held no
  definition — a fact about our budget reported as a fact about the world.
* **The pressure ran the other way too.** `glossary.py` mapped "every candidate
  refused us or served only a PDF" to **`failed`**, on a run that had executed
  perfectly, purely to separate it from "we read six pages and found nothing".
  Two honest facts, one field, and the only way to distinguish them was to
  misuse the other end of the scale.
* **`scheduler.outage_retry_due` keyed on `status != "succeeded"`.** Correct
  only by accident: it meant "did not finish its work", and while `status` still
  carried outcomes the two happened to coincide on the daily path. The first
  outcome-shaped partial on that path would have re-fired the nightly run, and
  re-spent for it, on a window that had nothing to give.

This is the same failure `RunContext.note` was created to fix one layer down.
`note` rescued `succeeded` from routine notices in the pipeline; the
launcher-based kinds never got the equivalent and re-derived the problem from
the outcome side.

## The decision

Split the field.

* **`status`** is execution health, and nothing else. `succeeded` means every
  stage did what it set out to do. `succeeded_partial` means it degraded —
  `ctx.warn`, which sets `ctx.degraded`. `failed` means it persisted nothing.
* **`runs.outcome`** is what the work found. Four values:

  | | |
  |---|---|
  | `delivered` | the run produced its artifact |
  | `some` | a batch landed part of what it tried |
  | `nothing_found` | it did the work; the answer is no |
  | `nothing_to_do` | there was nothing to look for |

  NULL for a run with no answer to report, which is every `failed` and every
  `cancelled`.

`some`, not `partial`. `outcome='partial'` beside `status='succeeded_partial'`
would put two near-identical words meaning different things in one row, which is
the confusion the split exists to remove. `test_no_outcome_value_collides_with_a_status`
holds it.

### The rule that makes it stick

`LaunchOutcome.status` defaults to **None, meaning derive from `ctx.degraded`**.
Every unit of work gets the correct execution verdict by omission; a site that
overrides it is saying something the context genuinely cannot know — `ingest`
turning a fetch failure into `failed`, `personal_brief` doing the same for a
model that returned nothing usable. That is the same move `note` made: make the
right answer structural rather than remembered, so a new run type cannot get it
wrong by not thinking about it.

The eight bespoke status ternaries collapsed into outcome statements. Two sites
changed execution meaning, both toward honesty: the unreachable-web lookup moved
off `failed` and onto `succeeded_partial` with a `ctx.warn` explaining itself,
and a quiet daily moved off `succeeded_partial` onto plain `succeeded`.

### Reader words live in `run_types.py`

The four stored values are not words anybody says. `RunType.outcomes` maps each
to that kind's phrase — `nothing_found` is "not found" for a lookup and
"no coverage" for a corroboration sweep; `nothing_to_do` is "nothing new" for
the daily, "already had it" for an ingest, "already current" for a reader's
brief. `decorate` resolves each row's own phrase server-side as `outcome_text`,
for the same reason it already resolves `label`: a second copy of the map in
TypeScript would drift inside a release.

A kind declares a phrase only for outcomes it can reach. A `lookup` has no
`nothing_to_do` — there is always exactly one surface to look up — and a phrase
for it would be a lie waiting to render.

## What the reader sees

**One chip, one rule: an execution problem wins, otherwise show the answer.**
`lib/runOutcome.ts` is the only place that decides it, and every surface renders
through it. So the history reads `defined`, `not found`, `brief written`,
`already had it`, `40 of 182` — and `degraded` or `failed` when the machinery
is what went wrong.

`succeeded_partial` is relabelled from "partial" to **"degraded"** and takes
back the amber. On the history that made `StatusChip` surrender the colour, the
split moves it from 49 rows to 2.

The runs table went from ten columns to seven. The column already called
`outcome` had been holding a raw `brief:2026-08-18` or a 40-character slice of
an error; the brief id is a click away and a mono identifier is not what anyone
scans a history for. `docs`/`calls` — two of thirteen counters chosen once for
every kind — became one per-kind `detail` line built from `RunType.summary`, so
a lookup stops rendering an em dash under a figure it never tried to produce.
`Table` grew `hideBelow`, which every table in the app needed and none had.

The run detail page leads with the answer. `RunResultHeader` is first;
identity, timing and diagnostics follow. Warnings and errors merged into one
`Why` block shown only when something actually went wrong, with notes given
their own quieter heading — `ctx.note` exists to surface an observation without
claiming a degradation, and a panel headed "Warnings" undid half of that. The
rejection trace is promoted directly under the answer when the outcome is
`nothing_found`, because on that run it is the entire content of the page.

## What else it made possible

* **Exit codes that can be scripted.** `0` delivered, `1` failed, `2` ran
  cleanly and found nothing. Before this, `venona lookup FOO` exited 0 when the
  web held no definition and 1 when the web was unreachable: the only two states
  a script could tell apart were the wrong two.
* **The quiet-nights alarm.** `report_daily`'s docstring already worried about a
  corpus hollowing out while every run looks normal, and answered half of it by
  reporting struck-out feeds even on a clean night. The other half — a rotation
  that is entirely healthy and simply stops producing anything worth writing
  about — could not be asked in SQL, because "found nothing to write about" was
  indistinguishable from "degraded somewhere". `schedule.quiet_nights_alert`
  (default 3) now asks it.

## What was rejected

**A fifth value, `inconclusive`,** separating "we read pages and the answer is
no" from "we never got a page in our hands". It is real — `glossary.py` went to
lengths to draw it — but it is already carried by the PAIR: `succeeded` +
`nothing_found` is the first, `succeeded_partial` + `nothing_found` is the
second, with the warning saying why. A fifth value would state in one field what
two fields already say, which is how this whole problem started.

**Rewriting historical statuses.** The migration backfills `outcome` from
`run_stages.counts` for every kind it can read, and leaves `status` alone. The
44 lookups gain a correct outcome; what the row said at the time stays what it
said. `domain_setup` keeps NULL — it launches without a stage, so its counts
were never written anywhere the query can see, and a guessed outcome on a
historical row is exactly the dishonesty being removed.

## Consequences

`docs/CONTRACTS.md` C6 (the `done` event carries `outcome`) and C11 (`RunType`
grows `outcomes`, and an eighth rule). `CLAUDE.md`'s "never let a partially
useful run report as `failed`" gains its complement: **never let a run that
simply found nothing report as degraded.** Migration 0018 / revision
`0016_run_outcome`. Pinned by `tests/test_run_outcomes.py`,
`tests/test_run_outcome_backfill.py`, `web/src/lib/runOutcome.spec.ts` and two
new cases in `tests/test_run_lanes.py`.
