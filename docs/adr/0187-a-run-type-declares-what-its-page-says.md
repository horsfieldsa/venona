---
status: accepted
date: 2026-09-01
---

# 2026-09-01 — A run type declares what its detail page says, and the words resolve server-side

Contract C11 made a run's LAYOUT declarative. `label`, `stages`, `summary`,
`detail` and `outcomes` all ship from one table in `services/run_types.py`, and
`docs/EXTENDING.md` could therefore promise that adding a run type needed no
frontend change at all.

The promise was half true. Layout came from the table; CONTENT did not.

Two places in the client decided what a run had actually done. The sentence
under the verdict chip was an if-chain in `RunResultHeader.vue` over keys that
no run type declared — `stories`, `pages_read`, `considered`, `grounded`,
`already_current`, `is_new` — read off `stages[0].counts`. And the result panel
in `RunDetailView.vue` was two hardcoded blocks: one gated on
`kind === 'ingest'`, and one shaped for a lookup.

Six kinds declare `detail: "single"`. One of them is a lookup. So
`personal_brief`, `questions`, `lookup_all` and `definitions` all fell into the
lookup panel and were rendered against its fields. A reader's brief — which
emits `{user_id, brief_id, stories}` and none of what that panel reads —
displayed a `subject` with nothing in it and, on a run that had just written a
document, the word **`sourced: no`**.

Nothing could see it. The component typechecks either way, every unit test
passed, and `scripts/ui_smoke.py` asserted only that a single-stage run rendered
*something* rather than an empty spine.

Meanwhile the payloads those kinds do emit reached no screen at all. The sweep
computes `grounded_pct` with a comment saying it is computed "here rather than
left for a reader to divide two numbers in their head on the run detail page";
the run detail page never showed it. `questions` reports `generated`,
`rejected`, `failed_calls` and `articles`, and showed none of them.

## Decision

**`RunType` declares `facts` and `lines`, and both resolve server-side.**

```python
Fact(key, label, shape, words, href)   # key: "counts.x" | "params.x" | "run.x"
lines: dict[outcome, tuple[str, ...]]  # candidate sentences, first fillable wins
```

`GET /runs/{id}` returns `facts` already resolved and `result_line` already
written. The client renders a list without knowing what kind of run produced it.
This is the same move ADR 0183 made for the chip, applied one line down: the
chip's phrase has been server-resolved since then, and the sentence beside it
was still being assembled in a browser that could only guess from whichever keys
happened to be present.

Three rules make the result total rather than best-effort:

- **An absent key is omitted, never zeroed.** A run recorded before a key
  existed shows fewer facts, not a row of blanks. This is what lets the
  declaration grow without a migration, and it is why no backfill was written.
- **A counted slot resolving to zero does not fill, and a presence-only slot
  (`{?counts.pages_read}`) requires the key without printing it.** "Read 6 pages
  and none of them defined it", "No page could be read", and "The work ran and
  the answer was no" are three different facts about the world. Presence-based
  filling collapses the first two into `Read 0 pages`, and a slotless fallback
  collapses the last two into a claim about pages nobody recorded.
- **The last candidate for an outcome carries no slots**, so a kind that can
  reach an outcome always has something to say about it. `lines` is keyed on
  `(status, outcome)` rather than `outcome` alone, because a *succeeding* run
  may have none: `--only fetch` and `skip_brief` finish with `outcome = None` by
  design, an exception after committed work clears it, and migration 0018 left
  it NULL on every historical row it could not classify.

`tests/test_run_facts.py` pins all three, plus the completeness rule that any
outcome a kind declares a chip phrase for also declares a sentence.

## What this does not change

**`counters.` is not a fact namespace.** The thirteen frozen `RunCounters` are
what `summary` already selects and `lib/runCopy.ts` already names. A fact
pointing at one would render the same number twice on one page under two labels
and put a second counter-to-words map in Python for the first to drift against.
Section "What it produced" is the kind's own returned metadata; "How it ran" is
the frozen counters. The counter panel orders by `summary` rather than filtering
to it, so a research run keeps the six counters the live panel never showed.

**Steps stays multi-stage only.** Rendering it for one-stage runs was
considered and rejected: a lookup's single row carries `considered 4 ·
grounded 1` in wire spellings, a block beneath the same two numbers in reader
words, and its status and elapsed are already in the summary line. The comment
at `RunDetailView.vue` was right. What single-stage kinds are genuinely missing
is the per-item `stage_progress` trace, which is not built here.

**Merges, budgets and lanes are untouched.** This is a read path.

## Consequences

- Adding a run type now means `facts` and `lines` as well as `label` and
  `stages`, and `tests/test_run_facts.py` fails until both are there.
  `docs/EXTENDING.md` step 8's "no frontend change is needed" becomes true
  rather than aspirational.
- A finished run's model session is reachable. `stores/runs.ts::attach` has
  always replayed the whole log for a finished run; the page attached only while
  `isLive`, so every transcript in `run_events` was unreadable the moment its
  run ended. It is a collapsed section now, fetched on first open, and refused
  while a research run is live because the store holds one connection.
- `venona research` records its stages. It called `set_stage` and never
  `record_stage`, so a kind declaring two stages wrote no `run_stages` rows and
  rendered "No step records" on every run it ever completed.
- A reader's brief has a subject in the history list, from the declared
  `subject` key rather than a seven-way cascade over `params` that matched none
  of its fields.
