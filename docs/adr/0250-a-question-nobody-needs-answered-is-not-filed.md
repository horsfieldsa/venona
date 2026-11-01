---
status: accepted
date: 2026-10-02
---

# 2026-10-02 — A question nobody needs answered is not filed

`_file_entity_duplicates` asks the corpus, not the conflict queue, and its net is
deliberately wide:

> The net here is deliberately WIDER than the one that merges: filing a row only
> asks the question, and `identical_pairs` acts only on what it can prove. So a
> loose grouping costs a curator one keystroke, never a wrong merge.

That reasoning is sound and its premise has expired. One keystroke is the cost of
one row. Measured on production, 2026-10-02:

| | |
|---|---|
| open conflicts | 455 |
| with both sides `active` | **23** |
| naming a proposed entity seen fewer than twice | **383** |
| containment rows with a provisional side | 307 of 350 |
| mean mentions on the proposed side | **1** |
| already naming an entity that has drained to `dormant` | **45** |

And the cost landed on a person. Of 76 human `kept_apart` verdicts on containment
pairs, **38 were about an entity that never corroborated** and drained on its
own afterwards. Half of a curator's keep-apart effort was spent answering
questions that answered themselves.

## What changes

**`pipeline.file_duplicates_min_mentions`, default 2.** A `provisional` entity is
not asked about until a second sighting. It DEFERS rather than drops: filing is
asked of the whole corpus every run, so the moment a surface returns the question
is filed then. The bar applies to `provisional` alone, so a declared or
corroborated entity is never held back.

**A `dormant` entity leaves the filing population, and a conflict whose side has
since drained leaves the queue.** ADR 0054's drain already means uncorroborated
AND silent for a whole window, and `select_entities_for_projection` excludes such
an entity (ADR 0247 admitted `provisional` and kept dormancy out). Asking a
curator whether it is the same as something else is asking about a thing that is
not in the graph.

Hidden rather than resolved, because nothing has been decided. A revival puts the
row straight back, and `include_resolved` still returns it: a decision somebody
made about an entity that later drained is still a decision they made.

## Why this does not reopen the splits the wide net was built for

Three of the four splits that fragmented the live corpus (Palantir, two Mavens,
General Atomics) were entities `services/seed.py` wrote straight into the table
without going through `Resolver._add_alias`, so nothing was ever filed about
them. Every one was ACTIVE. The bar applies to `provisional` alone and
`test_two_declared_entities_are_still_caught` holds that.

## What this does not do

It does not lower a merge gate, change a verdict, or touch what `identical_pairs`
can prove. It changes which questions reach a person.

*Trigger to revisit:* a duplicate found in the corpus that was never filed because
one side sat at one mention for a long time. The bar is a setting for that
reason; raising the corroboration requirement elsewhere would be the wrong lever.
