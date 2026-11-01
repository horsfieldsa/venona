---
status: accepted
date: 2026-08-17
---

# 2026-08-17 — Rewrite could never have worked, and the retrieval rule is why


`POST /briefs/rewrite` ran the brief stage alone and ended
`succeeded_partial` in under a second with `llm_calls: 0` and the warning
*"brief: nothing new since the last brief"*. It never reached the model.
`run:06a82809` is the one where someone noticed; it would have done this on any
day.

`build_retrieval` excludes every cluster that appears in `brief_clusters`, and
the brief on screen has already claimed all of them. So the button whose entire
job is to re-brief the current window retrieved zero, every time. The endpoint's
own docstring claimed it "re-briefs the current window without re-fetching or
re-analysing anything" — it could not.

The blanket self-exclusion carve-out that once covered this was removed for good
reason: it was `bc.brief_id IS NOT ?`, which with one brief row in the table is
false for EVERY row, so the `NOT EXISTS` was vacuously true, nothing was ever
excluded, and six briefs in one day served the same top stories. Removing it was
right. What was missed is that **nothing replaced the idea**, and the button was
left pressable.

**`rewrite_of`, naming a single brief.** The clause is `bc.brief_id <> ?` inside
the `NOT EXISTS`, so exactly one brief's claim is released and a cluster any
OTHER brief has featured stays excluded — including one featured by both. That
scope is the whole difference from the carve-out it replaces, and it is why this
one cannot fail the same way: `None` exempts nothing, which is every path except
a rewrite. Verified against the live corpus — no exemption retrieves 0 clusters,
`rewrite_of` the two-item brief retrieves its 2, and `rewrite_of` a *different*
brief retrieves that brief's, never today's.

**A rewrite ADDS a brief; it does not replace one.** Ids are unique per
generation and same-day briefs are already separate archive rows (C3), so the
brief being rewritten stays where it is and nothing is deleted. `venona briefs
delete` then re-run remains the way to release a brief's clusters to every
future run, which is a different and genuinely destructive thing.

**The client sends the brief ON SCREEN, not "the latest".** `Rewrite` sits on
`BriefView`, and that view renders the archive too. An absent `brief_id` still
means the latest, so a `curl` or a future CLI caller needs no view state.

`rewrite_of` is the only stage input that names an artefact rather than a state,
which is worth stating plainly rather than leaving as a surprise — C7 carries the
note. It does not weaken the rule: the stage still reads clusters by state, and
the id only widens which state is eligible.

`ui_smoke` asserts the button sends the id, by stubbing `fetch` for exactly one
call and restoring it in the same block. The request never leaves the browser —
letting it through would start an opus brief call, and this smoke test does not
spend. Dropping `brief_id` from the payload is a silent return to the old
behaviour, and a quiet day looks identical.
