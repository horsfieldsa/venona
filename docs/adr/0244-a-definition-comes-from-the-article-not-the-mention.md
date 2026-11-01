---
status: accepted
date: 2026-09-14
---

# 2026-09-14 — A definition comes from the article, not from the mention

`pipeline/define.py` may read the whole document an entity was asserted in. Its
tier-1 evidence is a sentence the extractor picked out of that text as saying
what the thing IS, verified by code against the text, and stored on
`assertions.definition_quote`. The context sentence stays what it always was:
where the name appeared.

**The two sentences are not the same sentence.** `evidence_quote` answers C1's
question — where was this named — and the answer is usually a contract award, a
quote from an executive, or a list. The sentence that describes the thing is
somewhere else in the same article. Every definition in that gap was
unreachable, because `define` read context sentences and no article text at all.

`plat:vectis` is the worked example and it fails at four layers at once. Two
articles asserted it, both stored a context sentence, and one of those
sentences — "Last year, Skunk Works unveiled Vectis—a concept for a group 5
drone" — was itself a definition that tier 1's regex rejected for using an em
dash where it required a comma. The better sentence was never offered at all:
`documents.text_nfc` held "Lockheed has described Vectis as a large 'Group 5'
stealth drone", in a shape the regex has no rule for, in text nothing read. The
entity was outside the stage's scope anyway, because `targets()` looked only at
the shared brief and Vectis was on the personal one. And the hover card's
lookup button went straight to the web, which from this deployment cannot reach
defense.gov at all. The card said no sourced definition existed. Four
independent failures, one reader, one word.

**Asking for it costs nothing on new documents.** `analyze` already reads every
document in full, once, for extraction and scoring. `definition_sentence` rides
along on that call as a second field per entity. The corpus read before the
field existed is what needs paying for, and that is a backfill (`venona define
backfill`) rather than a standing cost — one call per article, not one per
name, over articles ranked by how many unexplained names they back.

**Code still decides traceable.** `verify_definition_sentence` is the single
gate: the sentence must be a substring of the document we hold, compared
through `web_grounding.comparable` so a model's normalised quotes are not
mistaken for a paraphrase, and it must name the entity. It deliberately does
not re-apply `defining_sentence`'s regexes — those are how the free tier finds
a definition with no model in the loop, and re-imposing them would throw away
exactly the shapes they cannot spell, which is the reason to ask in the first
place. Invariant 3 is unmoved: a definition still exists only if it is
traceable to a sentence in a document.

**The tier ladder is unchanged and this is still `in_article`/1.0.** A verbatim
sentence from an article we hold outranks a synthesis and outranks the web,
whichever of the two routes found it. Where both a marked sentence and a regex
hit exist, the marked one wins — both are `in_article`, so the ladder cannot
separate them, and a thin appositive in a passing mention must not outrank the
sentence the article devotes to the subject. That is the shallow half of the
same defect: 1,622 `in_corpus` definitions averaging 158 characters were
synthesised from passing mentions because the descriptive paragraphs were out
of reach.

**`definition_quote` is a cache, not a claim**, which is why stamping it is the
one exception to "never `UPDATE` an `assertions` row". The assertion — surface,
entity, evidence quote, confidence, method — is untouched and stays
append-only, so invariant 1 and the unmerge path are unaffected. What the
column records is where in this article the description lives. An empty string
means the article was read for this name and describes nothing; null means
nobody has looked. Without that distinction the backfill would re-read the same
articles forever to learn the same nothing.

**Consequence.** `define.targets()` scopes to both kinds of brief, keyed on the
date rather than the run, because a personal brief is written by its own run
minutes after the daily one. `POST /entities/{id}/define` tries tier 1 before
spending on tier 3. The `redefine` run kind is light-lane on the C11 rule that
the lane asks what a run writes, and it is resumable by the column rather than
by any state of its own.
