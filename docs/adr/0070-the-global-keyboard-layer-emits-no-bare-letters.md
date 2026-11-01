---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The global keyboard layer emits no bare letters


A `g`-prefixed go-to chord is the obvious Linear-style shortcut. It cannot work
here.

`MergeReview` binds bare `r` to *relate (PART_OF)* and `DefinitionQueue` binds
bare `r` to *reject*. Both listened on `window`, and both write to the database.
A pending `g` has to swallow the next keystroke, and two `window` listeners
cannot be separated by `stopPropagation` — so `g r` in Curation would have fired
the route jump **and** the write, silently, mid-queue.

**Consequence:** `composables/useKeyboard.ts` owns one capture-phase listener and
every view registers through it, queues included. Globals carry a modifier
(`⌘K`, `Alt+1..5`, `⌘,`) or are unambiguous punctuation (`?`, `Esc`). That makes
the collision class structurally impossible rather than merely avoided.

The `?` overlay is generated from the registry, replacing a hand-written
cheatsheet in `CurationView` that could drift from the handlers beside it. `Esc`
pops a LIFO stack of dismissables — with a palette over a dialog over a hover
card, a broadcast closes all of them on one press.
