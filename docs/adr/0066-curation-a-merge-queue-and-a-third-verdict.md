---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Curation: a merge queue, and a third verdict


`Glossary` became `Curation` with two queues, because merges and definitions are
the same job — read the evidence, make a call — and merges had no UI at all.
`services/curate.py` had the whole mechanism since S8, reachable only from the
CLI. `api/routers/curation.py` is transport over it; nothing new decides
anything, because a second merge implementation behind HTTP would be the one
that drifts.

### The third verdict

Reported from use: keep-apart is binary, and `General Dynamics` /
`General Dynamics Information Technology` is neither a merge nor unrelated.
Keeping them apart discards the one fact the conflict surfaced.

So `relate` writes a real assertion — ontology-checked by the same
`check_relation` the pipeline uses — marked `method='manual'` with a quote
naming the decision. That is the seed loader's precedent for a claim with no
article sentence behind it. Undo **retracts rather than deletes**: `assertions`
is append-only, and `retracted_at` is exactly the column for a claim made and
then withdrawn. Nothing in the codebase had ever set it.

### Why the pipeline proposes instead of asserting

Asked for the same capability in the pipeline. The structural version was
measured first, and it fails:

```
strict token superset + both Organization/Program  ->  98 candidates
  RIGHT  GDIT PART_OF General Dynamics · Thales Australia PART_OF Thales
  WRONG  U.S. Army PART_OF Army              <- same entity, wants a MERGE
         Anduril Industries PART_OF Anduril  <- same entity
         Northrop Grumman PART_OF Northrop   <- same entity
  WRONG  Royal Canadian Air Force PART_OF Air Force   <- not related at all
```

Token containment cannot separate "X Corp is the full name of X" from
"X Division is part of X". Roughly half would have been wrong edges.

So `curate` asks the model instead — names, types, aliases and headlines as
evidence — and stores the answer as a **suggestion** (migration 0012). First
live pass, 58 pairs for **$0.19**: 23 part_of, 21 merge, 14 distinct, and
spot-checking every part_of found them correct, including
**`Amazon Web Services PART_OF Amazon`** — the exact pair CLAUDE.md cites as the
reason the containment guard exists. The guard was right to refuse the merge;
the model supplies what the relationship actually is. It also got right every
case the structural rule got wrong (`U.S. Air Force` = `Air Force` is a merge).

A suggestion, not a write. The curator confirms with one key, and the proposed
action is the one highlighted. Acting alone stays reserved for provable identity
(`identical_pairs`) — a wrong suggestion costs a keystroke, a wrong assertion
costs an edge nobody notices.

### Also fixed, found while building

- **A rejected definition did not survive the next run.** `store_definition`
  upserts `status` unconditionally on `key=["id"]`, so re-grounding a rejected
  acronym flipped it back to `active` — nightly, forever. `decided_at`
  (migration 0011) records that a person ruled, and the pipeline now defers to
  it. Accept/reject was previously a bare status write with no trace of who
  decided.
- **`entity_card` returned a hybrid after a merge**: it resolved the row through
  merge chains but passed the *unresolved* id to `_neighbors`/`_aliases`/
  `_signals`, so a just-merged loser rendered the survivor's name with the
  loser's evidence — reachable exactly when a curation UI refetches.
- **Provisional entities showed an empty mini-graph**, because neighbours are
  filtered to `active`. Correct for a reader; useless for a curator comparing
  two provisional entities whose neighbours are also provisional. `curation=True`
  relaxes it.
- **The 303 undefined definitions were unactionable** — accept was hidden without
  an expansion, so the only button was *reject*. Look-up and define are now on
  the row.
- `_row_to_entry` ran one `documents` query per row; a 200-row list was 201
  queries.

### Known gap

`EntityCardBody` was extracted so two cards can render side by side, but
`EntityHoverCard` still has its own copy of that markup. Merging them changes
the brief's most-used interaction, and that was not worth doing unverified at
the end of a long session. They will drift; the hover card should adopt it next.
