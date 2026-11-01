---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The brief becomes items: fact and judgement are separate fields


**Complaint:** "It's too much AI opinion, I want more summarisation of actual
articles, and some WHY that ties back to the relationships between things."

The old schema was `title + dek + sections[].markdown`: one model call wrote
flowing prose per section. In that shape an opinion has **nowhere else to live
than inside the facts**, so "summarise more, opine less" is a prompt-engineering
battle against the format — and one you lose a little more every time the model
writes a good sentence.

So the fix is structural, not editorial. Each item now has two required fields:

```
summary         what the article reports. Figures, names, titles, quotes.
why_it_matters  the judgement, and the only field it may appear in.
```

A reader who wants only what happened reads the summaries. The prompt can now be
held to "no interpretation in `summary`" because there is a field the
interpretation belongs in. Verified on the first live brief: the summary reads
*"27 autonomous air intercepts across eight flights in April at Edwards"* and the
judgement reads *"This is the cleanest edge signal of the day… the uncomfortable
part is who is standing in the slot."* Neither leaks.

Four decisions worth keeping:

- **Attribution is joined from `documents` via `cluster_id`, never returned by
  the model.** The model says which cluster an item is about; publisher, URL and
  date come from the record. A brief may be wrong about significance; it must
  never hang a claim on the wrong outlet. Same division as invariant 2 — the
  model decides *which*, code decides the rest.
- **An item whose `cluster_id` is not in the retrieval set is dropped.** An
  invented id would otherwise produce an item with no evidence and no
  attribution, silently, because every other field would look fine.
- **`age_hours` is computed at generation time and stored.** Recomputing on read
  would relabel last Tuesday's "3H" item as "5D" and rewrite what the reader was
  told. A brief is a snapshot.
- **Sections are an `enum` compiled from `profile/brief_sections.yaml`**, which
  mirrors the discovery sweeps. A section the model invents is rejected by the
  validator rather than quietly becoming a one-item desk that reads as coverage.

`state`/`state_note` carry NEW vs FOLLOW-UP, answered against the last week of
item headlines passed in the prompt. A daily brief that cannot say "you have
seen this, here is what changed" makes the reader do the diffing. First live run
produced three follow-ups with real notes — *"Full awardee list and per-track
ceilings now confirmed"*, *"New angle: OTA/IDIQ use is shrinking contract
disclosure"*.

**Annotation is unchanged and that was the constraint.** Both halves go through
the same `markdown_to_blocks` → `annotate_blocks` path, so acronym definitions
and entity hover cards work in the judgement exactly as in the facts — 113 spans
across the summaries and 45 across the why paragraphs on the first brief,
including `gloss:` links. The format changed how the brief READS, not how it
links.

`BriefSection.blocks` is retained and empty for item-format briefs. It renders
the pre-item archive; removing it would have emptied every historical brief the
moment the format changed.
