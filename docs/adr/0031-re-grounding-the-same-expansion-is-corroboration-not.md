---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — Re-grounding the same expansion is corroboration, not ambiguity


The ambiguity check originally fired on any existing row with a *different* id.
Because the seed already carried MOSA, a newly grounded MOSA from a real article
made the surface "ambiguous" against itself — which stops the annotator matching
it at all.

**Now:** only a genuinely different *expansion* creates ambiguity, and a repeat
sighting updates the existing row rather than duplicating it. Related:
`mark_undefined` checks for any grounded row for the SURFACE, not just an
`active` row under the base id — the original guard missed both the ambiguous
case and rows stored under a suffixed id.
