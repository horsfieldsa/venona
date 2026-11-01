---
status: accepted
date: 2026-08-27
---

# 2026-08-27 — Auto-merge widens to lexicon-bounded name variants, and to a model verdict a structural signal corroborates

Asking the read-only MCP tools "what is NGC2 and how does it relate to Palantir"
returned a data defect rather than an answer. The live corpus held:

- `company:palantir` and `company:palantir-technologies`, each with its own
  `PRODUCES` edge to a different Maven id
- `plat:maven-smart-system` and `plat:maven-smart-system-platform`, identical
  names, identical type, identical alias sets
- `prog:maven-smart-system`, a third entity with that name at another type
- `company:general-atomics` beside a provisional duplicate of itself

Source counts are per id, so a split halves an entity's evidence: every Palantir
edge read `SINGLE-SOURCE` while the underlying facts were corroborated. A reader
auditing a claim was told the opposite of the truth.

## Why nothing merged them

Four independent gates, each sufficient alone.

1. **The containment veto files a permanently ineligible conflict.**
   `_is_qualified_containment` declines a token-subset match and `_contained`
   mints separately, filing `contain|…`. `_classify` returned `CONTAINMENT` on
   that prefix and `identical_pairs` reads only `kind = 'duplicate'`.
2. **`profile/lexicon.yaml` excludes the words that matter**, deliberately and
   with a written reason: `systems`, `technologies`, `services` are substantive
   in defense names.
3. **A word-subset duplicate fell through `_classify` into `resolver_error`.**
   The classifier inferred kind entirely from acronym generation and never
   compared the two names to each other. `resolver_error` is excluded from
   auto-merge AND from adjudication, so the pair got no merge, no suggestion,
   and a label saying it was a bug.
4. **The seed and anchor loaders write aliases past the conflict rule.**
   `_load_entities` upserts into `aliases` directly, never through
   `Resolver._add_alias`, and `ix_aliases_normalized` is not unique. Two
   same-type entities could co-own a surface with nothing filed anywhere.

## The decision

**The 2026-08-06 scoping stands and is widened twice.** Auto-merge still never
acts on similarity. What it now also acts on:

- **Corporate form** — the extra tokens are all in a new `corporate_form:` list
  (`technologies`, `technology`, `industries`). The fuller name survives.
- **Generic tail** — the extra tokens are all in `generic_tails:` AND trail the
  longer name. The form without the tail survives.
- **Declared alias** — one entity's whole name is a surface a person declared an
  alias of another, same type. The declaring owner survives.
- **A model `merge` verdict at `confidence: certain`, where the two names ALSO
  corroborate it** structurally (token containment, or `token_sort_ratio` at or
  above `NAME_VARIANT_SCORE`), the types match, and the kind is `duplicate` or
  `containment`.

The first three are bounded by a list a human wrote and can edit in the Admin
rail, so an incomplete list yields a MISSED merge that lands in the queue, never
a wrong one. They are a widening of "provable identity", not a reversal of the
similarity ban.

**The fourth IS a reversal** of "merges are proposals the user approves", and is
recorded as one. Its budget: a wrong verdict can only merge two entities whose
names already look like one another, because the structural half of the gate is
not optional. The reversal trigger is the unmerge rate on the `auto: model`
cohort — every one is tagged in `entity_mappings.reason`, listed by
`venona entity merges`, and reversed by `venona entity unmerge`. If a curator is
undoing them, set `pipeline.auto_merge_model_verdicts: false` and the
derivational classes carry on unaffected.

The structural half is not caution for its own sake. `adjudicate`'s own
docstring records twelve hand-checked containment pairs, of which `U.S. Army
PART_OF Army`, `Anduril Industries PART_OF Anduril` and `Northrop Grumman
PART_OF Northrop` were all wrong — and wrong in the direction of calling one
thing two. A judgement that confident and that mistaken about the inverse
question does not get an unguarded merge.

## What else changed

- **`_classify` compares names before inferring from acronyms**, so a word-subset
  pair is `duplicate` or `containment` rather than `resolver_error`. That one
  change is what makes the whole path work, and reverting it reverts everything:
  the queue re-derives `kind` on every run, so no migration and no sweep.
- **`_file_entity_duplicates` reads duplicates out of the corpus**, not the
  queue, because three of the four live splits had no conflict row at all. It
  files at most one row per pair under any prefix — two rows about one pair can
  carry opposite merge directions, and which wins is then iteration order.
- **`cross_type`** is a new kind for one name held at two types. Never mergeable;
  the curation UI withholds the Merge button as it does for an anchor.
- `profile/lexicon.yaml` gains the two lists; `non_distinguishing` is untouched,
  because a token added there makes the RESOLVER accept a fuzzy containment
  match silently, which is the invisible-merge class the design exists to
  prevent.

## Rejected

**Adding `technologies` to `non_distinguishing`.** It would have fixed Palantir
in one line and merged the surface with no conflict filed and nothing shown to
anyone. The resolver stays paranoid; the curator is the layer that gets to be
clever.

**Filing the collision from the seed loader.** It would have made two writers for
one fact, and the detector already reads it back out of the data on the next run
with better evidence and proper pair deduplication.

**`systems` and `services` in `corporate_form`.** "BAE Systems" is the company.
Left for the operator after reading `venona conflicts --kind duplicate`.
