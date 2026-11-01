---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Curation is stage five, not a second scheduler


The obvious shape for a nightly maintenance pass was its own scheduled job. It
would have been wrong on three counts, all verifiable in the code:

- `RunRegistry` holds a single `_active` slot, so a curation run would have
  409'd the user's manual run — the glossary lookup already hits this.
  *(No longer true as of 2026-08-07: lanes. The other two objections stand, and
  the conclusion is unchanged — `curate` earns none of that machinery.)*
- A collision with the daily returns at `scheduler.py:30` **before**
  `create_run`, so a skipped night would exist only as a log line.
- `stores/runs.ts` would have yanked RunsView off the daily stream onto the
  curation one.

The pass spends nothing and takes milliseconds. It earns none of that
machinery. As `curate` in `orchestrator.STAGES` it inherits durability, SSE,
cancellation and `succeeded_partial` for one string in two files. Promote it to
its own run kind if it ever grows model calls; `lookup` is the worked example.

*2026-08-07: that last sentence is now cheap to act on.* A run kind is an entry
in `services/run_types.py` plus a `work` closure handed to `run_launcher.launch`
— which is how `rewrite` and `definitions` were added. The third objection also
went away by construction: `/health`'s `active_run` reports the research lane
only, so no light run can yank RunsView off the stream it is following.

**It is propose-only.** Nothing merges, renames or rejects. The one thing it
changes is `status` via `promote_provisionals`, which is a pure function of
evidence and reverses itself when the evidence does.

### Classifying conflicts is the actual product

`alias_conflicts` was recording three different failure modes in one pile, and
nothing ever set `resolved = 1`. They need different actions, so an
undifferentiated queue makes a human re-diagnose every row. Migration 0005 adds
`kind`; the stage classifies on the way past.

The ordering of the checks is the whole design, and I got it wrong first:

```
both names generate the surface  ->  ambiguous on its own
    names near-identical (>= 80)  ->  DUPLICATE   one thing, two spellings
    names different               ->  COLLISION   two things, one acronym
one name generates it            ->  DUPLICATE   acronym vs long form
neither                          ->  RESOLVER_ERROR   our generator's bug
```

Checking DUPLICATE first labelled `DIA` — Defense **Intelligence** Agency and
Defense **Investment** Agency — a duplicate, and would have invited someone to
merge two unrelated agencies. The 80 threshold is measured, not guessed: on the
live conflicts, spelling variants score 88-91 (`Next Generation C-sUAS Missile`
vs `...Counter-sUAS Missile`) and genuinely different bodies 47-69.

First live run, zero model calls and zero cost: **9 duplicates** — including
`Maj. Gen. Partick Ellis` vs `Patrick Ellis`, a typo — **9 collisions**
(`ACS` = Alpine Combat Systems vs Allen Control Systems; `UAF` = Ukrainian vs
US Air Force), and **52 containment** pairs filed by the resolver itself.

None of those duplicates could have been found by the name-similarity sweep
this slice originally proposed: `token_set_ratio("MDA", "Missile Defense
Agency")` is **8.0**.

Two hyphenation variants (`NGC2` / `Next-Generation`) still land in collision.
The classifier is a triage hint, not an oracle — same as `venona alias audit`.

> **Corrected 2026-08-06.** The "88-91 vs 47-69" gap above is not real. Scoring
> all 21 live non-containment rows gives a continuum — 69.0, 71.4, **77.9**,
> 82.2 — with the two `NGC2`/`NGCC` rows sitting 2.1 points under the threshold
> and filed as collisions. See the next entry.
