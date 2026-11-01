---
status: accepted
date: 2026-08-07
---

# 2026-08-07 — Entities get grounded definitions, and the card gets one shape


`entities.summary` has existed since migration 0001 and **nothing has ever
written it**. Measured before this change: 1,460 active or provisional
entities, one summary, and that one arrived in a seed fixture. The entity hover
card's most prominent slot has always rendered empty, which is why the card led
with a relevance score instead of with what the thing is.

The raw material was already there. `analyze` asks for a verbatim
`context_sentence` per entity mention and stores it in
`assertions.evidence_quote` against an `article_id`; every entity has at least
one, and 1,265 of 1,601 have exactly one.

`pipeline/define.py` therefore mirrors `pipeline/acronyms.py` tier for tier:
quote a sentence we already hold, else write one from the sentences we hold,
else the web on demand, else say so. It runs AFTER `brief`, because that is the
first point at which we know which entities the brief references — 215 of 1,601
entities have ever appeared in one, and those are the only ones a reader can
hover.

**Three things this cost, all worth writing down.**

**Tier 1's first gate was far too loose.** "A comma followed by a determiner"
matched `Australia, the UK, and the UK` and `Next Generation Command and
Control, the service has emphasized…`, and both were stored as definitions and
shown to the reader. Two anchors fixed it: the noun phrase must contain a KIND
NOUN — a word naming what something is — and must not be followed by a finite
verb, which is what tells an appositive from the subject of the next clause.
Coverage fell from 31 of 180 to 16. That is the right trade: a wrong definition
reads exactly like a right one, which is the whole reason invariant 3 exists.

**Tier 2 asked the model for article ids, and it dropped the prefix.** Given
sentences tagged `[doc:2ad78187…]`, it returned `2ad78187…`. The intersection
check then rejected all seventeen good definitions it produced in the first live
run — a $0.32 run that stored nothing. Sentences are numbered `[1] [2] [3]` now
and the model returns numbers. This is the same rule as CLAUDE.md's "never let a
model emit a canonical entity id" and the same fix `curate.py` already used for
adjudication: the model names a position, code maps the position to a row.
Asking a model to echo an identifier is asking it to invent one.

**A synthesis is not a quotation.** Tier 2 writes one sentence from several
evidence sentences, which is looser than the acronym pipeline has ever been. It
is kept visibly distinct end to end: stored with `source_article_ids` and no
`grounding_quote`, and rendered as "drawn from N articles" rather than in a
blockquote. If invariant 3 is ever read strictly, drop tier 2 and let those
entities stay undefined until someone presses the button — everything else
survives unchanged.

**Consequence:** the hover card, the graph's detail rail and the merge queue
now all draw `EntityCardBody`, which renders Definition → Sources →
Connections for every kind. A section a kind cannot fill states its absence
("An acronym is not a graph node") rather than disappearing, so the shape of
the card never depends on the data.
