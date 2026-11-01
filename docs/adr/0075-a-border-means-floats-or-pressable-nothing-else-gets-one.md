---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — A border means "floats or pressable"; nothing else gets one


`/runs` carried nine outlined status pills, five outlined stage pills, two
outlined buttons, two outlined panels and three outlined footer chips —
forty-odd rectangles, none out-ranking any other, on a page with one real
button. Settings was four stacked grey boxes, one of them 350px tall to hold a
sentence and an empty state.

**Consequence:** `Panel variant="bare"` and `Chip variant="bare"` are the
default for in-page grouping. The boxed forms survive only where something
genuinely floats. Two related fixes fall out of the same principle:

- **Status is single-encoded.** Eight of nine runs were `succeeded_partial`,
  rendered as an amber outlined pill *beside* an amber warning count. A signal
  that fires on 89% of rows is a background colour. It renders as a quiet
  `partial` now and the warning count carries the degradation, once.
- **The accent means interaction only.** Section rules and the brief's `why` bar
  were olive with a bold olive label, making a divider and an aside the two
  loudest things on the page.

**Note for the bare variants:** `Panel--bare` keeps the `.panel__header` and
`.panel__body` ELEMENTS. `fill`'s flex chain is keyed on them existing, and
losing it takes the merge queue's action bar off screen. `Chip`'s `--chip-dot`
default and its consuming rule both live on unconditional selectors for the same
class of reason — see "One button, one chip".
