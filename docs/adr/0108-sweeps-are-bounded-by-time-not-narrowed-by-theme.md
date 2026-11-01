---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — Sweeps are bounded by time, not narrowed by theme


The eight discovery sweeps cost $5.87 on the first run — 30% of it — across 129
web searches, and returned 47 documents of which **4** were fresh. One was a PR
Newswire release from 313 days earlier.

The tempting fix was to cut themes: `sweep_uas` carries 9 tags for 8 URL slots,
`sweep_programs` names the entire department. Worth noting that the code to do
this already exists and has never run — `discovery._themes` samples
`THEMES_PER_SWEEP = 3` only when a sweep has more than `FOCUSED_SWEEP_MAX = 10`
tags, and every sweep has ≤ 9. The module docstring lists "themes rotate off
`ctx.rng`" as one of three properties that make discovery safe to bolt on. That
rotation has never happened.

**We bounded time instead, and left themes alone.** The two are substitutes: the
reason 9 themes is too many is that the search space is unbounded in time, so
asked about `counter_uas` with no date floor the model has a decade of pages to
rank and returns the most authoritative, which is old. Bound it to 48 hours and
there are a handful of qualifying events per theme.

The number that settled it. Split the 47 sweep documents by whether the domain
is one we already subscribe to via RSS:

| | docs | fresh | p50 age | reached the brief |
|---|---|---|---|---|
| already in our RSS list | 7 | **0** | 409h | 0 |
| a domain RSS does not carry | 40 | 4 | | **2** |

Every re-find of our own feeds was stale, *because* RSS had already delivered
the fresh ones from those domains. The two sweep items that did reach the brief
came from `militarnyi.com` and `navalnews.com` and were both under a day old.
So an unbounded sweep spends its budget re-finding our own archive, and the
bound is what forces it to reach for a source we do not have. Source coverage
and time depth are orthogonal; only one of them was ever the sweep's job.

**Consequence:** `pipeline.discovery_max_age_hours: 48`, dropped after extract
and before cluster — the first point at which a discovered page has a date worth
trusting, since `discovery.py` throws the model's guess away on purpose. The
body fetch is spent either way; the analyze call is what the drop saves.
Undated discovery results are dropped by the same rule: `build_retrieval` treats
a missing `published_at` as always in-window, so undated is the widest state
here, not a neutral one.

RSS is exempt and bounded at analyze instead. A feed we subscribed to on purpose
publishing last week is a different proposition from a search reaching back for
it, and the funnel should report that as `outside_window` rather than losing it
at fetch.

**If it is still not enough**, the next lever is `FOCUSED_SWEEP_MAX: 10 → 3`,
which makes the dead sampling path fire for the first time. Deliberately not
done in the same change as the time bound, so the measurement attributes.
