---
status: accepted
date: 2026-09-14
---

# 2026-09-14 — Blocked curated relations are counted apart from pending rebuild work

`_projection_state` returns two numbers, not one. **Pending** is curated
relations the graph has not caught up with. **Blocked** is those whose endpoint
no longer projects at all.

**Folding them together leaves a banner nobody can clear.** Pending is work a
rebuild finishes; blocked is not, because only `active` entities are projected,
so a relation onto a merged-away or retracted endpoint will still be there after
every rebuild anybody runs. One combined count means an operator rebuilds, sees
the number unchanged, and learns the indicator lies.

**The distinguishing fact is that a merged entity that STILL HAS A NODE is
exactly a merge the projection has not caught up with** — that is pending work.
An endpoint with no node at all is a different thing and needs a different
answer, which is a curation decision rather than a rebuild.

**Consequence.** `api/routers/curation.py::_projection_state` keeps that
sentence and cites this record rather than carrying the thirteen-line argument
in a private helper docstring. The surfaces that render the two counts must keep
them separate; collapsing them in the UI reintroduces the same defect one layer
up. Related: ADR 0085 (a merge follows the chain), ADR 0051.
