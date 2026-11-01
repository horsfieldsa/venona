---
status: accepted
date: 2026-08-08
---

# 2026-08-08 — An acronym's meaning is a question the letters cannot answer


`verify_expansion` has said it since it was written: *"Definitions are scoped to
the document that grounded them — if an article says the second thing, then in
that article that is what NGC2 means."* The data model never honoured it. One
row per surface, one global answer, and when a surface acquired a second
reading the alias index stopped annotating it entirely — so an ambiguous
acronym was either confidently one thing everywhere or invisible everywhere.

Measured, not theorised. `FCC` was grounded as "Fire Control Center" off a
Kongsberg product page: a real definition of a real thing, passing all four
checks, with the phrase appearing in **0 of the 4** documents that use FCC.
`BAE` was grounded as "brigade aviation element" while the corpus contains
"BAE Systems" in **16 of 16** occurrences of those letters. The checks verify
that *a* page defines the acronym that way; nothing compared the answer to how
this corpus uses the term.

Three layers, in dependency order, because none of them works alone:

1. **Gather.** `lookup_web(collect=True)` reads every candidate rather than
   returning the first that passes, keyed by normalised expansion so two
   spellings of one meaning are corroboration and not a second option.
2. **Curate.** `POST /glossary/{surface}/choose` settles a split surface in one
   atomic decision — one reading active, the rest `rejected` (never deleted:
   each was grounded in a real sentence, and "why is FCC not a fire control
   center here" must still have an answer), every row stamped `decided_at` so
   the nightly run cannot reinstate what a curator discarded.
3. **Resolve in context.** `pick_sense` takes exactly one kind of evidence —
   the expansion spelled out in the same text — and **declines in every other
   case**, including when the text spells out both. Declining costs an
   annotation; guessing costs the reader's trust in every annotation on the
   page. The old behaviour (unmarked) is the fallback, not a regression.

The placeholder `ambiguous:<normalized>` never reaches the wire. Indexing every
reading under its own id instead would put two entries with the same surface in
the automaton and let `_resolve_overlaps` break the tie by insertion order —
deciding what FCC means by row order.
