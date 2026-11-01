---
status: accepted
date: 2026-09-08
---

# 2026-09-08 — A boundary the ontology holds is still a question worth asking


`Program` declares `confusable_with: []` and `Platform` names only
`Technology`, both with a comment saying the omission is deliberate: a program
and the thing it delivers are two entities on purpose. That decision stands and
nothing here touches it. `identical_pairs` still refuses any pair whose types
differ, `declared_type_pairs` still requires both an ontology declaration that
the boundary blurs and a person having already stated which side the thing sits
on, and `MergeReview.vue` still withholds Merge on `cross_type` and
`type_conflict` whatever anyone suggests.

What the boundary was also doing, silently, was keeping those rows away from
the only stage that reads a pair and says something about it. `adjudicate`
selected `kind IN ('containment', 'collision', 'duplicate')`, so six
`cross_type` and two `type_conflict` rows sat in the live queue having been
filed, classified, and then read by no stage on any run. A curator opening one
got two names, two types, and nothing else.

**So the adjudicator reads them too.** The verdict it can offer on a
`cross_type` row is not merge — the UI will not carry that out — it is the
other two: `part_of`, which is frequently the honest answer for a Program and
the Platform it delivers, and `distinct`, which is the answer for a collision.
Both are actions the UI already offers on those rows and neither crosses the
line the ontology drew.

**The evidence that this is worth a few calls a run.** Read against the live
corpus, four of the five Program/Platform pairs were a seed-declared entity
beside a single-mention provisional mint — `M1E3 Abrams`, `Precision Strike
Missile`, `GenAI.mil`, and the symposium that is neither a Program nor a
GovernmentBody. Those are one thing read twice, not a program and its platform.
The fifth, `Maven Smart System`, is the genuine article and is also the only
one where BOTH sides are seed-declared: a person wrote each of them down on
purpose.

That shape — one declared side, one mint — is exactly the discriminator
`declared_type_pairs` already applies, and it separates these cases far better
than the type pair does. This ADR does not promote it to an auto-merge rule.
Doing that would decide a Program/Platform pair without a person, which is the
thing the ontology's empty `confusable_with` exists to prevent, and it would
need its own record with a stated false-merge budget. What it does say is that
the pairs are worth a reading, and that the reading is the model's rather than
a rule's.

## Changing this requires touching

- `db/repositories/pipeline_curate.py::select_alias_conflicts_2` — the kinds.
- `tests/test_decision_table.py::test_a_pair_the_pipeline_cannot_settle_is_still_read_by_something`
  — every `escalate` and `keep_apart` row in `fixtures/curation/decisions.yaml`
  must reach the pool.
- `web/src/components/curation/MergeReview.vue::isUnmergeable` — the guard that
  makes a suggestion safe on these kinds. Weakening it without amending this is
  what would turn a reading into a merge.
