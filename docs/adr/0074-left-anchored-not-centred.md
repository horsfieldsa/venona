---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Left-anchored, not centred


At 1500px the brief occupied **37% of the window**: a 560px column centred with
~470px of black on each side, while the header's meta cluster sat 800px away in
the opposite corner and the wordmark sat at x=22. Three unrelated alignment
systems on one page, and no composition at all.

The tell was already in the code: `BriefItem` placed an ordinal at
`left: -3.5rem` in `--border-strong` with 400px of nothing beyond it. Somebody
had felt the margin should carry something and then left it empty.

**Consequence:** every route hangs off two vertical lines. `--edge` is where
content starts; the margin rail to its left carries marginalia (item ordinals,
ages, state, right-aligned against the column); `--rail-side` carries
orientation (date, freshness meter, a sticky contents list). `AppHeader` uses
the same two lines, so the wordmark sits in the margin rail and the nav starts
at `--edge`.

`--measure` changed from `70ch` to `38rem` in the process. `ch` is the right
unit for a measure and the wrong one for a grid track: a track sized in `ch`
resolves against the grid CONTAINER's font, so `70ch` would have meant 70
characters of whatever the container was set in, not of the reading serif.
