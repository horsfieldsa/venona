---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — The top block has a floor, not just a cap


`top_line` is per-item and may be empty, and the prompt asked for it on "at most
three items — the ones that would change what the reader does this week". A model
can read that as a quality bar, and on a two-item day it did: both empty, so
`brief:2026-08-16:06a822cf` shipped with no top block at all. The rail drops its
contents list silently and there is nothing on screen that separates that from a
rendering fault.

The cap was always right — a "top 3" of six items is not a top 3, and the schema
cannot express "at most three across the array", so it has to be applied in
`_persist`. What was missing was the other half.

**Exactly `min(3, len(items))`, enforced in the assembler.** The prompt now asks
for the same number, and says that three is the shape of the document rather than
a bar to clear; the assembler makes it true whichever way a model reads it.

**The fallback line is the item's own headline.** No second model call and
nothing invented: the prompt already requires a headline to state the finding in
one sentence that could not sit above a different article, which is what a top
line is. Gaps fill from the front of the editorial order — sections in profile
order, the model's ranking by consequence within each — so the promoted items are
the ones the brief already leads with.

`min(3, n)` and not 3: two items cannot make a top 3, and the rail labels the
block from the count it is given, so "Top 2" is the honest heading.
