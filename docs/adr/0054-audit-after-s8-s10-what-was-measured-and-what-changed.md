---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Audit after S8–S10: what was measured, and what changed


An audit against the live database (862 entities, 202 documents, 22 runs,
$32.72 spent) rather than against the code. Four findings drove the work.

### The pipeline had never run end to end

`run_stages` held 21 rows: fetch 5, analyze 6, brief 6, curate 4, **acronyms 0**.
Every run since S8 used `--only`. The three full runs predate the stages, and
the 91 undefined acronyms were written at a timestamp matching no run at all —
`run_acronyms` invoked outside the orchestrator during the build.

So `acronyms` and `curate` had never executed in the same run, and the "definition
of done" in `CLAUDE.md` had been satisfied for the app and never for the pipeline.

A related correction: `curate.py` justified being a stage rather than its own run
because a separate run "would have contended for the single-slot `RunRegistry`".
It would not have. `RunRegistry` is an `asyncio.Lock` "enforced in-process", and
every `venona` invocation is a separate process — which is exactly how five runs
overlapped between 20:43 and 20:58, and why four consecutive curate runs
classified the same 70 rows differently (`18 duplicate` → `4` → `9`). The
conclusion stands; the stated reason was wrong.

### The provisional pool could not converge

710 provisional entities, 690 of them at (1 article, 1 publisher). The obvious
reading — that the two-publisher rule is too strict — is wrong:

```
articles   n    of which >= 2 publishers
   1      698            0
   2       84           64   (76%)
   3+      80           79   (99%)
```

Reaching a **second article** is the binding constraint, not the publisher count.
The gate works; 130 of 150 active entities passed it.

What did not work is that nothing ever retired an entity. 184 provisionals are
`Person` — someone quoted once who will never be quoted again — and there is no
`DELETE FROM entities` anywhere in the codebase. The count could only rise.

`dormant` is the bound: uncorroborated **and** silent for
`provisional_dormant_days` (30). Hidden exactly as `provisional` is, revived by a
single sighting, deletes nothing. Simulated 60 days forward on the live data, all
739 provisionals go dormant and all 158 active entities stay active.

Dormancy is a statement about silence, not about correctness — which is why an
`active` entity never goes dormant. A corroborated thing that stops being
mentioned is still something the graph should answer about.

### The classifier threshold was fitted to the wrong input

`NAME_VARIANT_SCORE = 80` was scoring raw names, so a hyphen and a trailing
acronym gloss cost `Next Generation Command and Control` /
`Next-Generation Command and Control (NGC2)` 22 points and filed the same
program against itself, twice.

Normalising both away before scoring (`_comparable`) moves that pair to 100 and
opens a real gap: every true duplicate now scores ≥ 82, every true collision
≤ 71. Re-run over all 21 live rows, exactly two reclassified — the two that were
wrong. `25th Infantry Divisions` / `25th ID` (40.0) stays misfiled, and no
threshold fixes it; that is the honest limit of name similarity.

The lesson is the one in the correction above: a comment claiming a measurement
stops the next person re-measuring. If a constant is fitted to 21 rows, the
comment should say 21.

### A third of the acronym budget was being spent on English words

46 surfaces cleared the Tier-3 gate; roughly a third were not acronyms —
`LONDON`, `PARIS`, `WARSAW`, `DAYTON` (wire datelines), `HAVE`, `AIR`, `HEAT`,
`ATLAS`, `VISTA` (headline capitals). `ACRONYM_TOKEN` is a shape regex and
`looks_like_acronym` only checks shape.

The corpus answers this without a wordlist: a token that also appears as
`London` or `have` is a word being shouted. Title-case and lower-case only —
`DoD` is a *styling* of an acronym, and counting it would suppress `DOD`. Unioned
across the night's documents rather than per document, because the evidence is
usually in a different story: a piece datelined `WARSAW —` rarely also says
`Warsaw`, but another one does.

46 → 36 eligible. Adding the measured stragglers (`LLC`, `MVP`, `CPR`, `RKLB`,
fiscal years) to the user-editable stoplist takes it to 31, and what remains is
almost entirely real defence vocabulary.

Cost, from the ledger: `acronym_search` averages $0.051 and `acronym_read`
$0.022, so a lookup is ~$0.076 and the cap of 8 is **$0.61 a night** — about 3%
of a full run (analyze ~$10.50, discovery ~$8.78). The cost was never the
objection.

### Deleted

`merge_proposals` (0 rows, 0 references), `alias_conflicts.run_id` (written by
one of the two code paths that file conflicts, read by none), `Budget.tick()`
and `llm.max_run_seconds` (no caller, so `_elapsed` was permanently 0 and the
check could not fire), `Resolution.note` (set at 6 sites, read nowhere),
`POST /briefs/{id}/reannotate` (no frontend caller; the CLI covers it), and the
curate alias-audit block (recomputed one integer nightly and changed nothing).

`clusters.key_facts` / `flags` were the opposite case and were **wired up, not
deleted**: migration 0004 states plainly that they exist to feed brief density
and the opinion/press-release filter. They were populated on 154 clusters with
no reader, so the brief prompt was re-deriving `$99.6M` from 4000 truncated
characters that `analyze` had already read in full.

### The resolver was reopening decisions nightly

Both conflict upserts passed `resolved: 0`, and `db.upsert` writes every column
it is given on conflict. Both conflict ids are deterministic, so the same pair
re-files on every run over the same corpus — meaning any verdict a human recorded
would have been silently reset the next night. Omitting the column entirely lets
the schema default cover the insert and leaves an existing verdict alone.

This bug did not exist before there was a close path, which is the point: adding
the exit is what made the re-file dangerous.
