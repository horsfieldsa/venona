---
status: accepted
date: 2026-09-12
---

# 2026-09-12 — Keeping two things apart is the safe direction

An operator read the curation queue and said it was leaving too much to him,
and that some of what it asked was obvious — *"like keeping apart two separate
people"*. He was right, and the reason is that the automation was pointed at the
wrong half of the asymmetry the whole design rests on.

## What the queue actually held

254 open conflicts on the defense instance after the first deep read:

| kind | open | carrying a verdict |
|---|---|---|
| containment | 174 | 78 |
| duplicate | 26 | 13 |
| collision | 24 | 2 |
| anchor | 15 | 15 |
| cross_type | 10 | 5 |
| type_conflict | 3 | 2 |

Two facts in that table. **139 of 254 carried no verdict at all** — the
adjudicator never saw them, because `adjudicate_limit_per_run` is 60 and one
deep read filed about 250. And of the ones it had seen, **48 already said
`distinct`** and were waiting on a person to agree.

The collisions are what the complaint was about. `Swedish Air Force` against
`Sudanese Armed Forces`. `Precision Strike Missile` against `PlugX surveillance
malware`. `Portal Aircraft Company` against `Palladyne AI Corp.` Two names that
share an acronym and nothing else, put to a person as a question.

## The asymmetry, and which way it points

CLAUDE.md states it and ADR 0174 is built on it:

> A wrong merge is invisible and poisons every downstream query. A missed merge
> lands in a queue.

That is why `auto_merge_model_verdicts` is gated on `certain` AND a structural
corroboration: the model's judgement alone may not merge.

But `settings.yaml` also said, in its own comment, that *"a `part_of` or
`distinct` verdict is always a suggestion the curator confirms"* — so the
verdict that is **invisible when wrong** was automated and the verdict that is
**harmless when wrong** was not.

A wrong auto-distinction leaves two entities as two entities. That is what they
already were. The pair comes back the next time either name is seen, and the
cost is a missed merge, which is precisely the failure the queue exists to hold.
There is no corrupted read, nothing downstream to poison, and nothing a reader
is told that was not already being told.

**So a `distinct` verdict at `certain` is applied without a curator**, gated by
`pipeline.auto_keep_apart_model_verdicts`, and counted separately as
`kept_apart_by_model` so its cohort is auditable the way `auto: model` merges
are.

## The one veto

A pair the derivational rules would merge is never auto-distinguished.

That is not a similarity check and not a hedge. `identical_pairs` acts on
provable identity — the same string once a gloss and hyphenation are normalised,
an acronym one name generates, a corporate-form token a person listed. A model
calling such a pair distinct is contradicting a token list a human wrote, and
closing it silently would lose a merge nobody would ever be shown again. That is
the only way an automatic distinction can do lasting damage, so it is the only
thing guarded against.

Measured on the live queue the two sets do not intersect. The veto exists so
that a later widening of either rule cannot make them disagree in silence.

`anchor` and `resolver_error` are excluded. Neither asks whether two things are
one — the first proposes a placement, the second reports that the resolver fell
over — and settling either as "kept apart" answers a question nobody put.

## People, whose names carry things that are not their names

Every Person pair open on the queue was one person written twice:

- `Donald Trump` / `President Donald Trump`
- `Gen. Gregory M. Guillot` / `Gen. Gregory Guillot`
- `Lt. Gen. Heath Collins` / `Gen. Heath Collins`
- `Maj. Gen. Jacqueline McPhail` / `Maj. Gen. Jacqueline D. McPhail, USA`

A new derivational class, eligible under ADR 0174's test because the difference
is fully explained by a token list a person wrote (`profile/lexicon.yaml`:
`person_titles`, `person_suffixes`) and what remains must match **entirely**.

Ranks are **stripped, never compared**. The Collins pair is why: two outlets
disagree about the rank because one abbreviated it, and a promotion changes it
anyway — a rule requiring the ranks to agree would hold that pair open forever.
A single initial is dropped for the same reason it exists, which is to
disambiguate against a namesake; a namesake differs in the given name or the
surname too, and both of those still have to match.

Two tokens minimum. One remaining token is a surname or a mononym, and merging
on that is the similarity judgement the ban refuses.

**Writing the guard found a defect in the rule.** `jr`, `sr`, `ii`, `iii` and
`iv` were in `person_suffixes` until `fixtures/curation/decisions.yaml::
ellis-generational-suffix` was written. They are the one kind of post-nominal
that DISTINGUISHES two people — a father and a son share every other token — so
stripping them would have merged them, invisibly. A branch (`USA`) or a
qualification (`PhD`) names nobody and stays.

## The queue outgrew the adjudicator

`adjudicate_limit_per_run` caps ROWS, not calls, and is sized for a daily. After
a deep read files two hundred conflicts at once, the queue drains sixty a day
while a person is asked about the rest.

A run that writes no brief now adjudicates up to its own call ceiling. That is
safe because the row cap was never the real bound: `adjudicate` checks the run's
budget per batch and defers the remainder with a warning a reader can see, which
is the same thing every other stage does.

## What the first run showed, and the two widenings it earned

`run:06aa4eca`, five minutes and $0.23, read 136 pairs and settled 104. The
backlog was the cap and lifting it worked: 2 of the 142 left carry no verdict.

What remained was adjudicated and declined. Two buckets, and both are answered
here rather than left:

**`distinct` at `likely` counts too.** Thirty rows sat waiting for somebody to
agree with a reading the model had already given. A confidence short of certain
is the model keeping a pair in the queue, which is the right instinct when the
act is irreversible and the wrong one when it is not. The merge query still
requires `certain`, and the asymmetry is the reason rather than an
inconsistency.

**`part_of` is applied, under a tighter gate than either.** It was 62 of the
142, the largest bucket, and it is the only verdict that WRITES rather than
settles. Three conditions, all required:

- the ontology has a legal predicate for the two types. `_relation_for` writes
  NULL when it does not, and such a row cannot be applied by anybody: the API
  refuses it with the very message that function exists to prevent. Eight rows
  on the live queue were in that state, every one a pair of generic technology
  concepts (`drones` and `one-way attack drones`), and offering them to a
  curator was offering a button that 422s.
- the child's tokens contain the parent's. `adjudicate` records the model
  getting this backwards on three of twelve hand-checked pairs -- `U.S. Army
  PART_OF Army`, `Anduril Industries PART_OF Anduril`, `Northrop Grumman PART_OF
  Northrop` -- all wrong, all wrong the same way. A subsidiary's name carries
  its parent's and never the reverse, so a verdict naming the shorter name as
  the child is the shape the model is known to invert.
- the model said `certain`.

An edge is not a merge. A wrong one is visible in the graph, reversible by
retracting one assertion, and destroys no id -- which is what lets a structural
corroboration carry it. It is written `method="auto"`, never `manual`: a row
claiming a curator decided something the pipeline decided is the same lie as a
definition claiming a source it does not have.

## What was proposed and measured away

Folding plurals, possessives and leading articles into `_comparable`, to catch
`High-energy lasers` against `high-energy laser` without loosening the collision
rule.

Run against the whole live queue it collapsed exactly two pairs, and the other
was `MH-60 Seahawk` against `MH-60S Seahawk` -- a distinct aircraft whose
variant letter reads as a plural. In this domain designators routinely end in
one: MH-60S, F/A-18E, PAC-3. One correct merge does not pay for a rule that
turns a designator into its own family, invisibly.

Not built. `test_a_variant_letter_is_not_a_plural` is the record.

## Budget and reversal

**False-merge budget.** The person rule may merge two people who are not one.
The shape it would take is two officers with the same rank, given name and
surname, differing only in a middle initial. None exists on the live corpus.
Budget: one. A second means the initial-dropping half is wrong and goes, leaving
the honorific half, which cannot confuse two people at all.

**Reversal trigger for the relations.** A curator retracting an auto-written
edge means the containment gate is not enough on this corpus. Two retractions
and it goes back to suggesting. `curated_relations` finds them by their
`curation:` run-id prefix, and `method='auto'` separates them from a person's.

**Reversal trigger for the distinctions.** A curator reopening an
auto-`kept_apart` row means the model's `certain` is not certain enough on this
corpus; `venona conflicts --kind ... --all` shows the cohort. Two reopenings and
the gate goes back to suggesting.

Both are reversible: a merge undoes with `venona entity unmerge`, and a settled
conflict reopens.

*Extends [0174](0174-auto-merge-widens-to-three-derivational-classes.md); the
asymmetry it turns on is stated there and in CLAUDE.md.*
