---
status: accepted
date: 2026-10-25
---

# 2026-10-25 — A version of a design is its own relationship

A block, mark or numbered model of a platform (`F-16 Block 70`, `Switchblade
600`, `Oryx H-9`) had no predicate. A curator could only keep the pair apart, and
the adjudicator's nearest legal answer was `part_of`, which `_relation_for`
translates to COMPONENT_OF between two Platforms. `model_relations` applies a
`certain` one, and the direction gate passes it, because a variant's name
contains its base's exactly as a subsidiary's does. So the graph held
`F-16 Block 70 COMPONENT_OF F-16`, `SM-3 Block IIA COMPONENT_OF SM-3` and twenty
more written by the run alone, while the same shape made up 39 of one day's 145
hand keep-aparts.

## Decision

Ontology v6 adds VARIANT_OF (Platform -> Platform). The adjudicator gains a
fourth verdict, `variant`, stored as a `part_of` suggestion carrying VARIANT_OF,
so the queue renders it with no new state. `part_of` never resolves to
VARIANT_OF, so Platform/Platform keeps COMPONENT_OF as its one answer there. An
instance whose ontology has no VARIANT_OF stores a `variant` verdict as
`distinct`, which is what a curator could answer.

The auto-apply gate is unchanged: `certain`, `containment`, the direction check.

## Evidence

Both prompts over 689 labelled pairs from production: the 314 a curator ruled on
2026-10-25, the 22 COMPONENT_OF edges the run had written alone, and 353 pairs a
person resolved earlier. Same batches, same model, the gates simulated as
`run_curate` applies them.

| | current | with `variant` |
|---|---|---|
| applied by the run, right | 229 | 245 |
| applied by the run, wrong | 24 | 8 |
| kept apart where a variant was the answer | 41 | 0 |
| left for a person | 395 | 436 |
| ... of which arrive with the right suggestion | 269 | 308 |
| ... of which need real judgement | 126 | 128 |

The 41 extra queued rows are variants the current prompt settled as `distinct`;
each now arrives with VARIANT_OF preselected.

**Why not apply `variant` at `likely`.** Simulated: 28 right, 20 wrong against
the labels. Several of the twenty are keep-aparts decided before a variant
predicate existed, but K9 Mobile Howitzer / K9 (one thing) and PAC-3 MSE /
Patriot (a component) are not, and 58% is not a gate.

*Trigger to revisit:* an undo of a `curation:` VARIANT_OF edge written with
`method = 'auto'`, or a queue whose VARIANT_OF suggestions a curator overrules
more than one time in ten.
