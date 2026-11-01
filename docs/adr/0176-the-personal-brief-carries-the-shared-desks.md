---
status: accepted
date: 2026-08-27
supersedes-in-part: 0160 (the no-sections half; the item machinery stands)
---

# 2026-08-27 — The reader's brief carries the shared desks and the shared Sources footer

0160 moved the For You brief onto the shared item machinery and, in the same
step, argued it out of sections: the reader's ranking IS the order, so the
schema dropped `section`, the system prompt said "this brief has no sections",
and the item query aliased `NULL AS section` so the shared assembly would yield
exactly one heading-less run. That reasoning was sound about ranking and wrong
about reading. A flat run of a dozen items has no structure to scan, and the
reader arrives at it already knowing the shared brief's shape — so the personal
document read as a lesser thing rather than a tailored one.

The two are now the same document with different contents. The desks come from
`profile/brief_sections.yaml` on both surfaces, the model is asked which desk a
story belongs on and nothing else about arrangement, and code lays the page out:
desks in the profile's declared order, the reader's ranking preserved inside
each. Nothing about the ranking is discarded — it decides what sits at the top
of a desk instead of what sits at the top of the page.

Decisions that carry weight:

- **The TOP block is chosen before the desks reorder anything.** After the
  regroup, "first in the document" means "first on the earliest desk", which is
  an accident of the profile's desk order. `_top_lines` therefore takes the
  items in the reader's rank order and returns them keyed by cluster; ranks are
  assigned afterwards by document position, because the rail is a contents list
  and walks the page. This is the one place a naive reorder silently changes
  what the rail says, and it has a test.
- **Empty desks never render.** `assemble_body` opens a desk the first time an
  item claims it, so a reader with nothing in Space simply has no Space heading.
  A brief that listed eight desks to say nothing under six of them would be
  reporting the profile, not the day.
- **The Sources footer credits the whole ranked window**, not the stories the
  brief argued about. That is what the word means on the shared brief
  (`pipeline/brief.py` stores every retrieved cluster), and a narrower footer
  would quietly claim a narrower reading than actually happened. It needed a
  `source_ids` column on `personal_briefs`; `brief_service.brief_sources` is now
  shared by both read paths so the join lives in one place.
- **The supply cap reads `pipeline.brief_top_clusters`** rather than a constant
  of its own. Eight items cannot fill eight desks, and two caps that mean the
  same thing drift.
- **`PERSONAL_BRIEF_FORMAT` goes to 3.** It is the only thing that moves the
  inputs hash when the format changes underneath a current document. Every
  reader's stored brief becomes stale and offers Rewrite, which is as far as it
  goes: `should_auto_generate` returns False the moment a document exists for
  the day, and staleness deliberately does not appear in its conditions. So the
  bump costs nobody a generation they did not ask for, and tomorrow's brief
  arrives in the new shape either way.

Unchanged from 0160: the item format, `brief_prompt.md` governing both surfaces
verbatim, `assemble_body` as the single renderer, validation before the delete,
the same-day exclusion in the follow-up diff, and the deliberate absence of
`unresolved_mentions` — a per-reader document still must not feed the shared
curation queue.
