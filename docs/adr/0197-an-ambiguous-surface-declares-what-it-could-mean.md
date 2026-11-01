---
status: accepted
date: 2026-09-02
---

# 2026-09-02 — An ambiguous surface declares what it could mean, and the document gets a vote

`ambiguous_surfaces` recorded that a surface has more than one plausible owner
and stopped there, so `Resolver` Guard 1 could only decline: no entity id, the
mention dropped, and **both** readings poorer for it. Measured on the defense
corpus, that is `the Army` in 78 documents, `the Navy` in 41, `the Air Force` in
33 and the bare ministries in 9 — every one a mention of something the graph
already holds, discarded because nothing recorded WHICH things it might be.

Declining is still the default and still right when the text does not say. What
was missing is that the text usually does say. A writer spells out "U.S. Army"
once and writes "the Army" for the rest of the piece — which is *why* the short
form is ambiguous. The disambiguator is generally in the same document.

So `ambiguous:` entries take an optional `readings:` list, and Guard 1 resolves
when exactly one reading is named in the surrounding text. **Two named, or none,
and it declines exactly as before**, so the failure mode stays a missing mention
rather than a wrong one — which is the entire reason the surface is on that list.
A surface without `readings:` behaves byte-identically to before.

This is `alias_index.pick_sense`'s discipline, one layer up, and deliberately so:
narrow evidence (the reading's own name, or the country it is based in), ties
decline, absence declines, no similarity score and no model call. It is NOT
`resolve.py`'s rung 3, which stays a no-op: that one is about the mid-confidence
FUZZY band, and Guard 1 returns before the ladder runs at all.

**Readings are declared, not derived.** A person lists the ids beside the reason
they already write, so the routing set cannot silently acquire a new member and
a provisional mint cannot become a destination. An uncorroborated reading may be
listed: it is skipped while inactive and starts working the day it is promoted.

**On the home-jurisdiction half of the evidence.** It fires less than it looks
like it will. In a corpus that names many countries per article, two readings'
countries are often both present, which is a tie, which declines. It costs a
match and never a wrong answer, so it stays — but the full-name evidence carries
almost all of the load.

### Consequence

- Migration 0022 adds `ambiguous_surfaces.readings` (JSON), declared in
  `db/tables.py`; `validate_anchors` checks the id shape and refuses a
  single-reading entry, which is an alias rather than an ambiguity.
- `Resolver._pick_reading`, `_searchable` (word-boundary padded, so `Iran` does
  not match inside `Irani`), and a `context=` argument threaded from
  `analyze.run_analyze` through `persist_cluster` and `_ingest`.
- `resolve.py`'s module docstring changes with it. It said the stoplist "is never
  auto-resolved at all", and leaving that in place would have made the file lie
  about its own most dangerous branch.
- Benefit is PROSPECTIVE. An analysed cluster carries `analyzed_at` and is not
  re-read, so mentions already dropped are not recovered without re-analysis.

### Found while building it, and fixed with it

Both from the same root: loader row ids drawn from a positional counter.

1. **A declared alias could be silently dropped.** Adding one alias to
   `profile/anchors.yaml` shifted every later id onto values an earlier load had
   written, so the insert collided on a primary key it did not name and
   `except IntegrityError: continue` discarded a surface a person had declared.
   Measured: four aliases were missing from the live database.
2. **The loaders' streams could collide with anything else.** Removing that
   consumption freed the low counters, and `load_seed`'s first assertion id then
   equalled the first id of any other `SeededIdGen(0)`. A test caught it at once.

Loader row ids now derive from what the row IS — `(entity_id, surface)` for an
alias, `(run_id, triple)` for an assertion — so they cannot shift and cannot
meet. `ANCHOR_ID_SEED`, a constant that existed only to hold two streams apart,
is gone: they can no longer meet by construction.
