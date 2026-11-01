---
status: accepted
date: 2026-09-14
---

# 2026-09-14 — A full-screen mode leaves a visible way out

Any mode that hides the application's chrome keeps one visible control that
exits it. The graph's full screen has an "exit full screen" button
(`GraphView.vue`); the brief's folded rail carries the same strip.

**A mode whose only exits are a key and a control it has itself put out of reach
is a trap.** ADR 0118 hid the graph's rails and left `[`, `]` and `Esc`. On a
phone there is no `Esc` and no top edge to reach for, so the gesture that got
you in was the only one there was, and it does not reverse.

**The keyboard exits stay.** This is an addition, not a replacement: `Esc` is
faster for anybody who knows it, and the visible control is for everybody else
and for every device without a keyboard.

**This reversal already happened in the code and was never recorded.** The
button exists and both comments re-argue the reasoning from their own side;
`grep "exit full screen" docs/adr/` was empty until this record.

**Consequence.** `web/src/views/GraphView.vue` and `web/src/views/BriefView.vue`
each keep the load-bearing sentence and cite this record instead of re-arguing
it. A new full-screen or focus mode inherits the rule. Amends ADR 0118's
consequence; ADR 0199 owns the chrome-recede half.
