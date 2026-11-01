---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Parenthetical extraction trims to the verifying window


**Found by testing.** The regex captures greedily leftwards, so
`"The Army's Next Generation Command and Control (NGC2)"` yielded a phrase with
two words of lead-in, and the initials check then **rejected a correct
definition**.

**Now:** `_best_verifying_window()` walks trailing word windows longest-first
and takes the first that verifies, then re-anchors the stored span onto that
window. Verification lives inside `find_parentheticals()` so an unverified
pairing cannot be obtained by accident.

Note what the check deliberately cannot do: it cannot distinguish
`Next Generation Command and Control (NGC2)` from a hypothetical
`Nebraska Grain Cooperative 2 (NGC2)` — both genuinely produce those initials.
That is correct. Definitions are scoped to the document that grounded them;
cross-document collision is the `ambiguous_surfaces` mechanism's job.
