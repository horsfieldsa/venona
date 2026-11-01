---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Fuzzy matching had silently merged four organisations


`pipeline/resolve.py` scores candidates with `fuzz.token_set_ratio`, which
unions and intersects token sets — so a token **subset** scores exactly 100,
indistinguishable from an exact match. Every parent/subsidiary pair therefore
cleared the 92 accept threshold with a maximal runner-up margin.

Found live, three of them on **active** entities already annotating briefs:

```
active       Amazon                                  -> Amazon Web Services
active       General Dynamics Information Technology -> General Dynamics
active       Joint Interagency Task Force 401        -> Joint Interagency Task Force South
provisional  Thales                                  -> Thales Australia
```

JIATF-401 (counter-drone) and JIATF-South (drug interdiction) are different
organisations; both appear in the reference brief as separate stories.

`alias_conflicts` could not catch any of it: it fires only when a surface is
claimed by a *different* entity of the *same* type, and a fuzzy over-merge onto
a fresh surface never conflicts.

**Rejecting containment outright would have been worse.** "Air Force" →
"United States Air Force" and "Defense Department" → "Department of Defense" are
the same body, and six live aliases depend on matches of exactly that shape. The
signal is in the EXTRA tokens:

```
WRONG  {web, services} · {australia} · {information, technology}
RIGHT  {of} · {united, states} · {laboratories}
```

So a containment match survives only when every extra token is listed in
`profile/lexicon.yaml: non_distinguishing` — user-editable, because the line
between a formalising suffix and a subsidiary is domain knowledge, not code.
`systems`, `technologies` and `services` are deliberately absent: "BAE Systems"
is the company, not a suffix on "BAE", and a wrong merge costs far more than a
missed one, which merely lands in the review queue.

A declined pair is filed in `alias_conflicts` rather than dropped — it is the
highest-precision merge candidate the resolver ever sees.

**The guard is a good rule and a poor retroactive classifier.** Applied
backwards to the 11 existing fuzzy aliases it flags "Northrop Grumman" →
"Northrop" (the same company) and misses the JIATF pair entirely, because
401-vs-South is a token *substitution*, not containment. So `venona alias audit`
marks suspects with `?` and a human decides; the four retractions above were
reviewed individually, then `reannotate` repaired the briefs for free.
