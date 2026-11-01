---
status: accepted
date: 2026-08-28
---

# 2026-08-28 — The ontology says which of its own boundaries blur, and naming the kind is a verdict

The `cross_type` kind was introduced two days ago to surface one name held by
two entity types, on the reasoning that a Program and the Platform it delivers
are two things and merging them is the expensive mistake. That reasoning is
right and it described the minority of the cases.

Reading the evidence on all eleven live rows, the bucket held three questions:

- **Collision.** `Havoc` is a company, "a leader in all-domain collaborative
  autonomy", and separately "a new hypersonic missile system named HAVOC" from
  another firm. `SAFE` is the EU's €150bn loan instrument and separately
  "Sedaro's SAFE edge safety framework". Two things. Keep apart.
- **Typing inconsistency.** `GE F414` is one engine, read as a Platform in one
  article and a Technology in another. `Air Force Research Laboratory` is one
  lab, seed-declared a ResearchOrg and minted again as a GovernmentBody from a
  single sentence. One thing. These are duplicates.
- **Genuine program and platform.** The case the line exists for. Real, and
  rarer than assumed.

**Nothing in the two names separates the first two.** Both are one string held
by two types. The only thing that can tell them apart is knowledge of which of
this domain's type boundaries are sharp and which blur.

## The ontology declares its own confusions

Each entity type gains `confusable_with`: the types it is routinely READ AS,
which is not the types it relates to. Read symmetrically, so declaring the pair
once is enough, and validated at load like every other type reference — an
ontology naming a type that does not exist must fail startup, not surface weeks
later as a conflict quietly filed under the wrong question.

An empty list is the stronger statement and the default. `Program` declares
nothing, because Program/Platform is the distinction those two types exist to
draw. `Program`/`Technology` is also left empty even though it holds a known
typing slip (DODIN), because it holds a genuine collision too (SAFE) and the
safe direction is to ask.

This is domain configuration, not code: only the domain knows which of its own
boundaries blur, and a domain whose types differ has entirely different
confusions. So `config/ontology.yaml` carries it, the setup wizard is told to
draft it (with "empty is the default" stated), and the exemplar shows one worked
set.

## A confusable pair is a different question, with a different verdict

`_file_entity_duplicates` now files `typeconflict|` where the ontology says the
boundary blurs and `crosstype|` where it does not. The queue asks the right
question of each, and the curator gets a fourth verdict the other three could
not express:

- `keep apart` leaves two entries for one thing.
- `relate` invents a relationship that is not there.
- `merge` refuses to cross a type boundary — correctly, because the rule loose
  enough to merge `GE F414` would also merge `Havoc`.

**Naming the kind is what makes the crossing safe.** A person states which side
of the boundary the thing sits on, and both readings fold into one entity of
that kind.

## Why it mints rather than retyping in place

An id is type-prefixed and immutable (C4), and the entity hues are derived from
that prefix at render time. Rewriting `entities.type` alone leaves an entity
whose id, colour and type disagree, with nothing to say so.

So the correction is expressed the way every other identity change here is
expressed — as a merge, into an entity whose prefix already means what the
curator said. Choosing a kind one side already holds is a plain merge into it,
which is every case the live corpus actually contained. Choosing a third kind
mints it: `research:us-navy` and `unit:us-navy`, corrected to GovernmentBody,
both fold into a newly minted `govt:united-states-navy` and both old ids keep
resolving. The minted entity is `active`, not provisional — a person stating
that a thing exists and what kind it is outranks the sighting count the
promotion gate measures.

## One case resolves without asking

Where exactly one side of a type conflict carries a seed or anchor assertion, a
person has already stated the type, and the single-mention mint that read it
differently merges into it as `auto: declared type`. This is the declared-alias
rule (18 merges, 0 undone) asked of the type instead of the surface.

It crosses a type boundary, which `identical_pairs` refuses on principle. It is
allowed here only because **both** halves hold: the boundary is one the ontology
declared confusable, and a person has stated which side the thing is on. With
neither, this is exactly the merge that would collapse a company into an
unrelated missile.

## Rejected

**Retyping in place.** One UPDATE and no new row, but it breaks the id/type/hue
correspondence silently, and C4 plus the colour contract both lean on it.

**Inferring confusability in code** — "the four organisation types are mutually
confusable" is a domain judgement, and `make lint-domain` exists to keep those
out of code. It would also be wrong for the first domain whose types differ.

**Auto-resolving a type conflict from the model's verdict.** The model already
suggests on these rows. Applying a type correction automatically would cross a
type boundary on a judgement rather than a declaration, and a wrong one is as
invisible as a wrong merge.
