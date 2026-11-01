---
status: accepted
date: 2026-08-08
---

# 2026-08-08 — The focus ring is gone, and it is not coming back by accident


`base.css` carried a global `:focus-visible` ring — 2px `--accent-primary`,
`outline-offset: 2px`, `border-radius: var(--radius-sm)` — under a comment
reading "Never removed -- keyboard navigation is not optional", and
`DESIGN.md` said the same. Four components (`Button`, `Toggle`, `Chip`,
`EntityMiniGraph`) drew their own thinner variant in the utilities layer, which
outranks `@layer base` regardless of specificity.

**The symptom.** A green rounded rectangle around header nav links, appearing
after switching away from the window and back.

**The mechanism.** Chromium tracks input modality per *frame*, not per focus
event. Once any key has been pressed in the document, `:focus-visible` matches
on every later focus whatever caused it. This app sets that flag within seconds
of loading — `mod+k`, `alt+1..4` and `Escape` are global bindings
(`useKeyboard.ts`). Returning to the window makes the browser re-focus
`document.activeElement`; the stale flag is still set; a link the user
**clicked** comes back ringed. `useFocusTrap`'s `restoreTo?.focus?.()` after the
palette closes hits the same path. There is no selector that separates the two
cases, because by the time CSS runs the browser has already decided they are
the same case.

**Considered and rejected:** tracking modality ourselves — `pointerdown` and
`keydown` on `window` writing `data-modality` to `<html>`, with the ring gated
on `[data-modality='key']` and a `@custom-variant` for the four component
sites. That preserves keyboard focus rings and fixes the bug exactly. It was
turned down as more machinery than the problem was worth.

**`outline: none`, not a deleted rule.** Chromium's UA sheet draws its own
`outline: auto` on `:focus-visible`; Tailwind preflight only overrides
Firefox's `:-moz-focusring`. Deleting our block trades a green rectangle for a
white one. The reset names all three selectors.

**The cost, stated so nobody discovers it later.** Keyboard-only navigation now
has no visible position anywhere in the app. `router-link-active` still marks
the current route, but that is a location, not a cursor. The mini-graph's nodes
keep `tabindex="0"` and `role="button"` and still answer Enter and Space — they
just no longer show which one is selected. `.mention:focus-visible` also lost
its wash in `prose.css` for the same reason; `:hover` and `.is-active` keep it.
If keyboard use ever becomes a requirement, the modality-tracking approach
above is the way back, not restoring the unconditional ring.
