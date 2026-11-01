---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — Clusters with no readable body are not briefable


**Found by the model.** The first real brief flagged its own highest-scoring
item: *"came through with no body text... I cannot tell you tier assignments,
interface mandates, or vendor positioning from a headline."*

It was right, and the cause was worse than a truncated article: a **seed fixture
document** (56 characters, score 88) was competing with real news and winning.
Test data was leaking into production output.

**Now:** `build_retrieval` requires the canonical document to have at least
`MIN_BRIEFABLE_CHARS` (600) of text. That fixes the fixture leak and also guards
the general case of a genuinely truncated article. Annotations on the
regenerated brief went from 10 to 18.

Worth noting how this was caught: not by a test, but by the model refusing to
write confidently about something it could not see. That behaviour is worth
protecting — it comes from the instruction in `profile/brief_prompt.md` that
every claim must trace to a supplied article.
