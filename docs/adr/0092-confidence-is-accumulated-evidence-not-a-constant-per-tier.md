---
status: accepted
date: 2026-08-08
---

# 2026-08-08 — Confidence is accumulated evidence, not a constant per tier


`store_definition` wrote whatever `confidence` its caller passed and overwrote
it on every re-grounding, so a definition one web page asserted once and a
definition four documents spell out carried the same 0.7 forever. The function's
own comment already named the case — *"Re-grounding the same expansion from a
second article is corroboration — the common case"* — and then discarded it.

Tier 3 now opens at **0.55** and rises 0.1 per independent source to a ceiling
of 0.85. Three things this is deliberately **not**:

- **Not a relaxation of invariant 3.** All four checks still gate acceptance:
  our own fetch, the quote in our text, the acronym in the quote, the initials.
  Nothing is admitted that was previously refused. What changed is the number
  attached to what passes — 0.7 asserted more certainty than one page carries.
- **Not applied to the deterministic tiers.** A parenthetical either verifies
  against the initials or it does not, and a second one saying the same thing
  does not make the first more true. `in_article`, `in_corpus` and `manual` keep
  the number their caller declares.
- **Not a tally.** `corroborating_sources` stores the source keys, because the
  nightly run re-reads documents it has already ingested and a counter would
  climb on re-analysis of one article — manufacturing confidence out of nothing.

The ceiling sits below `in_corpus`'s 0.9 on purpose: a page on the internet
agreed with three times is still weaker evidence than a sentence in the corpus
this system ingested and can re-read.

Migration 0019 recomputes the old flat 0.7 rather than leaving it, so the column
does not mean two different things depending on when a row was written. It skips
any row a human has ruled on — the number they saw when they ruled is part of
that record.
