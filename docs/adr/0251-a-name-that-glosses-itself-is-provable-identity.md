---
status: accepted
date: 2026-10-25
---

# 2026-10-25 — A name that glosses itself is provable identity

Found by the graph-health audit of 2026-10-25, reading production. Two defects,
one shape: a pair of names for one thing that the curation stage either never
asked about or asked about in a bucket nobody reads.

## The silent split

`PAC-3 MSE` (19 mentions) and `PAC-3 Missile Segment Enhancement (MSE)` (7) were
both active, plus a third id with a trailing `interceptor`. `venona_relations`
showed `Boeing PRODUCES` with two publishers on one id and one on the other, and
`Lockheed Martin PRODUCES PAC-3 MSE`, the prime, as single-source. No conflict
row existed for any pair: `_merge_key` strips only a TRAILING gloss, so the
expansion keys as `pac 3 missile segment enhancement` and the short form as
`pac 3 mse`, and nothing else compares them.

Six live pairs had this shape (`PAE Fires`, `PAC-3 ACE`, `IFPC Increment 2`,
`MUSV marketplace`, `ACV-30`, and PAC-3 MSE), none filed.

**Decision: a new auto-merge class, `gloss form`.** A name `X Y Z (G) T` whose
trailing run `Y Z` generates `G` (by `acronym_variants`, the same generator
everything else uses) declares that `G` abbreviates `Y Z`. So `X G T` IS that
name, written short. The detector files the pair under the short form's key and
`identical_pairs` merges it, optionally with a lexicon tail on top (`PAC-3 MSE
interceptor`). The expansion survives, as for `acronym of`.

This is derivational, read off the name rather than scored, which is the ground
ADR 0174 requires. Its bounds:

- **At least one word must stay outside the gloss.** A bare result is `acronym
  of`'s question, and that class carries the collision guard a bare acronym
  needs (`DIA`).
- **A namesake refuses it.** When any other unretired entity of the type,
  glossed or not and dormant included, also abbreviates to the short form, two
  readings exist and the pair stays for a person.
- **A `+` distinguishes.** `normalize` drops it, and `A330 MRTT+` is the next
  airframe, not a spelling of `A330 MRTT`; curators had already kept `PILICA+`
  and `Patriot-3+` apart. The `same name` class has the same blind spot. No live
  same-type pair differs only by a `+` today, so it is recorded here rather than
  changed.

**False-merge budget:** zero in the measured population. All three pairs the
rule would merge on production at the time of writing were read and are right
(PAC-3 MSE twice, PAE Fires). **Reversal trigger:** any undo in the `auto: gloss
form` cohort on `make graph-health SECTIONS=merges`.

## The bucket nobody reads

All ten open `resolver_error` rows had a surface equal to one side's own name.
`resolver_error` means the acronym generator produced a surface neither name
supports, and is excluded from auto-merge and adjudication. These came from the
detector, whose key strips EVERY trailing generic tail, so `Patriot interceptor`
and `Patriot interceptors` (or `counter-drone system` / `technology`) share a key
but fail the containment test `_name_variant` needs, and the surface is not an
acronym. They fell through to the last line. The only one ever resolved
(`Drone Dominance initiative` / `program`) a person merged.

**Decision:** a same-type pair whose surface is one side's name classifies as
`duplicate`. Nothing about it was generated, so it is a question, and it now
reaches the model and the queue like any other duplicate, where ADR 0174's
structural gate still bounds what a model verdict may merge alone.

## Also

A corroboration sweep that degraded and promoted nothing now reports no
outcome rather than `nothing_found`. On 2026-10-22 a session limit stopped
analysis after 54 of 308 stories and the run read "no coverage": our quota,
reported as a fact about the press. The CLI used `documents_new` for the same
verdict the API derived from `promoted`; both now call
`research.coverage_outcome`.
