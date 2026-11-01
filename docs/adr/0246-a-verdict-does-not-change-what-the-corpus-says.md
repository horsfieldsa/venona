---
status: accepted
date: 2026-09-16
---

# 2026-09-16 — A verdict does not change what the corpus says about a phrase

`corpus_check.audit` weighs every tier-3 definition against every document body.
It is the heaviest read in the app, and it was memoised behind a single
fingerprint covering both of its inputs: the corpus, and the definitions read
against it.

That stamp includes `MAX(decided_at)`, so **every curation verdict invalidated
it** — and the definitions queue is exactly where verdicts are made. A curator
working the queue threw away the whole corpus audit on each decision, then paid
to rebuild it on the next page. The heaviest read in the app was wired to the
one action that repeats.

**Two memos, because there are two questions.** Which rows get audited is a fact
about the definitions: a verdict changes a status, so the audited set really does
move and `_AUDIT_CACHE` keeps the full five-field stamp. What the corpus says
about one `(surface, expansion)` is a fact about the corpus alone — ruling on
`FY` says nothing new about `BAE` — so `_SUPPORT_CACHE` is stamped on the corpus
half and survives the verdict. Bodies are read only for pairs that miss.

**The corpus half is sound on its own, with one recorded weakness.** There is no
`UPDATE documents` anywhere in `db/repositories/`, so a body cannot change under
a fixed `(count, MAX(created_at))`. The weakness: `newest_doc` has no
`text_nfc IS NOT NULL` filter and the column is `NOT NULL` anyway, so an in-place
rewrite preserving the row count would be invisible. Nothing does that today
(`pipeline/normalize.py` stores text verbatim), but the accidental invalidation
by `MAX(decided_at)` was masking it and no longer does.

**`audit_fingerprint` keeps its exact five fields.**
`POST /glossary/audit/corroborate` uses it as its own skip guard, so narrowing it
would send that endpoint through a full corpus sweep on every press.

**Separately, the scan stopped being O(documents × definitions).** One
Aho-Corasick pass over the distinct surfaces chooses which bodies each definition
can possibly be supported by; the per-document regex is then applied unchanged to
that shortlist. This is equivalence-preserving by construction — a body with no
raw substring occurrence cannot match `\b<surface>\b` — which matters because the
semantics here are subtler than they look and two of them nearly went:

  * `alias_index._is_word_bounded` does **not** agree with `\b` (`C2` in
    `x_C2_y`, `BAE` in `BAEé`, and any surface starting with punctuation, which
    matches nothing at all today). Candidates are confirmed with the existing
    compiled pattern rather than a hand-written predicate.
  * the follower regex is non-overlapping and its optional group CONSUMES the
    next token, so `uses` counts surface-plus-separator matches, not occurrences:
    `"BAE BAE Systems"` gives `uses == 2`, not 3. `NAME_HEAD_SHARE` is a ratio
    over that denominator, so re-deriving it would have moved the contradiction
    verdict this module exists to produce.

Measured on 500 documents / 4MB / 200 definitions: 3.31s → 0.15s cold, identical
results; and zero document reads after a verdict, which is the case the queue
actually hits. `tests/test_corpus_check.py` holds both — the narrowed pass
against a naive whole-corpus sweep, and a verdict costing no corpus read.

**Consequence:** `select_documents` and `select_acronym_definitions_by_method`
gained an `ORDER BY`. Neither had one, and `Support.phrase_documents` is built by
appending in row order, so the golden comparison the rewrite is held to was not
reproducible before it.
