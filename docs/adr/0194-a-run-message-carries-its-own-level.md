---
status: accepted
date: 2026-09-01
---

# 2026-09-01 — A run message carries its own level, and only one of the three degrades


ADR 0183 split `runs.status` from `runs.outcome`: whether the machinery worked,
and whether there was an answer. It fixed the field. It did not fix the
EVIDENCE, and the same failure regrew one layer down.

`ctx.warn` and `ctx.note` appended to one `warnings: list[str]`. Only the
run-level `degraded` bool reached the wire, so nothing downstream could tell a
degradation from a remark, and `RunDetailView` had to pick one heading for all
of a run's messages out of the run's status:

* in trouble, everything rendered under an amber **Why**;
* otherwise everything rendered under a grey **Notes**.

The two lists were byte-identical apart from a text colour. So a run with one
real degradation reported its curation backlog and the ontology's routine
rejections as reasons it had degraded, and `RunsView`'s amber "warnings"
StatBlock counted notes.

## The quiet night

The sharpest instance was in the daily itself, and it had been contradicting
our own documentation for weeks.

`brief.py::_empty_retrieval_reason` answers "why was there nothing to write
about" four ways, and the module's own comment says *"an empty brief is usually
not a fault"*. It wrote its answer into `BriefReport.error`. The orchestrator
did this with that field:

```python
if brief.error:
    ctx.warn(f"brief: {brief.error}")
```

Unconditionally. So a window holding nothing new finished `succeeded_partial` +
`nothing_to_do`, and `runOutcome.ts`'s rule — an execution problem wins —
rendered the chip as **degraded** instead of **nothing new**. Meanwhile
`docs/RUNBOOK.md` had documented the intended behaviour since 0183:

> `succeeded` + `nothing_to_do` — There was nothing to work on. For a daily
> that is a quiet window.

Epic U believed it had fixed this. It had fixed the NEIGHBOUR:
`retrieval.quiet_day` means "few strong clusters, brief still written", and
that case does end `succeeded` + `delivered`. The case where the window is
empty outright goes down a different branch, and nothing caught it because the
two conditions read alike from a distance.

## The second fact hiding in the same field

`BriefReport.error` carried "there was nothing to write about" AND "the model
could not write it". `orchestrator` then said:

```python
outcome.outcome = "delivered" if brief_id else "nothing_to_do"
```

so a brief that was OWED and failed reported `nothing_to_do` — the same value
as a genuinely quiet night. Two things downstream believed it:
`scheduler._quiet_streak` counted that failure toward the hollowing-corpus
alarm, and `outage_retry_due`'s third condition (`outcome != "nothing_to_do"`,
added by 0183 specifically so a quiet window is not re-fetched) suppressed the
retry for it. A run that failed under a provider outage — which the same
docstring calls "the strongest case there is for a second attempt" — was the
one thing that could not earn one.

## The decision

**A message carries its own level.** Three channels on `RunContext`, and the
test is what the READER lost:

| | |
|---|---|
| `ctx.warn(text)` | they got less than they should have. **The only thing that sets `ctx.degraded`.** |
| `ctx.todo(text, to=…)` | a person has to rule on it |
| `ctx.note(text)` | worth saying, nothing to do |

`todo` is the one that did not exist, and its absence is why the curation
backlog kept being filed as damage. It is not a degradation: nothing failed.
It is not a remark either: it re-fires every run until somebody rules on it,
which is the definition of a queue. It got `warn` in `acronyms.py` (with a
comment explaining that it "needs a person", which is exactly this channel
described in the absence of it) and `note` in `curate.py`, and both were wrong
in the way the available vocabulary forced.

`BriefReport.error` splits into `error` and `empty_reason`. The orchestrator
warns on the first and notes the second, and the outcome follows: `delivered`,
`nothing_to_do` when the window was empty, and **null** when a brief was owed
and failed — which is what 0183 already defines null to mean.

### What the reader sees

Three blocks on `/runs/:id`, each fed by the messages' own levels: **Why**
(amber, degradations only), **Needs you** (linked to the surface that can act),
**Notes** (grey). One decoder, `web/src/lib/runMessages.ts`, for the same
reason `runOutcome.ts` is one decoder.

`to` is SYMBOLIC — `curation`, `sources`, `config` — not a path. A route string
emitted from Python keeps being emitted the day the router is edited, and
nothing fails until a reader follows a dead link. `RunContext.todo` refuses a
destination outside the set, so the failure is at the source rather than a
silently unlinked line.

`degradedWithoutEvidence` moves from "the array is empty" to "no message at
`level === 'warning'`". That is a widening: it now also disbelieves a
`succeeded_partial` row whose messages are all notes and todos. Those rows are
precisely the ones claiming damage they never took.

### Storage

None. `runs.warnings` is a TEXT column holding arbitrary JSON, and no SQL
anywhere reads into it. Tagged objects need no DDL — but `db.jdump` is
`json.dumps` with no dataclass hook, so `types.as_json` is the one conversion
boundary and every persistence site goes through it.

Legacy rows are a bare `list[str]`, and their level is DERIVED rather than
guessed: `ctx.warn` is the only route to `succeeded_partial`, so on a
`succeeded` run every message provably was a note; on a degraded row we cannot
tell which were which, so they stay warnings. Statuses are left exactly as
written, per 0183.

The SSE `done` event keeps `list[str]`. C6 says `data` is additive-only, and
the live log is a log; the levels belong on the row.

## The copy, and why it was the worst in the app

`tests/test_copy_style.py` derives its Python scope from `"HTMLResponse" in
source`, which in `backend/venona` matches two auth routers. So every sentence
a run says about itself — rendered verbatim as a list item on a page an
operator reads — was outside every copy rule the project has. It showed. Five
messages named a CLI command, including `venona graph rebuild`, which is not
even the reader's job and which `useGraphVisibility.ts` already carried a
comment about sending readers to the wrong place. Others carried `gazetteer`,
`ambiguous_surfaces`, "the resolver's ambiguity guard", bare `cluster_id`s and
`f"{type(exc).__name__}: {exc}"`.

There were also four different sentences for hitting the call ceiling, despite
`llm/budget.py::deferral` existing since the last time somebody noticed that,
with a docstring saying so. Two of the four sites also both fired on one
budget exhaustion, so one event produced two entries in two vocabularies.

All of it is rewritten, and a new collector in `test_copy_style.py` walks
`backend/venona/**/*.py` with `ast` for the literals reaching `ctx.warn` /
`todo` / `note` and the stage lists that drain into them — static f-string
parts included — and holds three rules: no dashes, no `venona <subcommand>`,
no internal vocabulary.

`_empty_retrieval_reason` keeps its four-way split and loses its command. Two
tests pinned the literal `analyze-run`; the DISTINCTION is what they were
protecting (a thin day of stubs and a window nothing has read are different
facts) and naming a command was only how it happened to be spelled.

## Consequences

Quiet nights change what an operator is TOLD, not just what they read:
`scheduler.py` branches on `status == "succeeded"` to choose between
`run.succeeded` and `run.failed`, so a quiet nightly stops raising a failure
notice. And an outage during a run whose brief failed now earns its retry,
which spends — argued for in `outage_retry_due`'s own docstring, and now
capped by `schedule.outage_retry_max` in `config/settings.yaml` rather than by
a module constant. Widening what earns an unattended re-spend is what made the
absence of an off switch worth fixing: the ceiling and the delay are read at
the point of use, `config/settings.yaml` is editable over HTTP, and
`outage_retry_max: 0` stops tonight's retry without a deploy. Config migration
0005 carries both to instances that already exist, including the ones whose
config lives on a disk a push never reaches.

`venona brief` gained an `empty_reason` branch: without one a quiet window
printed `brief None` and exited 0, the one exit code a script must not get
there.

C11's status/outcome rule grows the three channels. CLAUDE.md gains two
prohibitions. `docs/DATA-MODEL.md`, `docs/RUNBOOK.md` and
`docs/ADMIN-GUIDE.md` all described two channels and now describe three.
Pinned by `tests/test_run_outcomes.py`, `tests/test_orchestrator_smoke.py`,
`tests/test_copy_style.py` and `web/src/lib/runOutcome.spec.ts`.
