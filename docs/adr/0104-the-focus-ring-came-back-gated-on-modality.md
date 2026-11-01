---
status: accepted
date: 2026-08-15
---

# 2026-08-15 — The focus ring came back, gated on modality


The aqua refresh asked for a focus affordance. That reopens the 2026-08-08
entry above, which is titled "The focus ring is gone, and it is not coming back
by accident" — so this is the deliberate half it left room for, and it takes the
route that entry named rather than the one it rejected.

**What the design bundle asked for**, and what is NOT implemented:

```css
:where(a, button, input, textarea, select, [tabindex]):focus-visible {
  outline: none;
  box-shadow: var(--ring-focus);
  border-radius: var(--radius-md);
}
```

An unconditional `:focus-visible` ring is precisely the thing that was removed.
The bundle's justification is about APPEARANCE — "a hard ring in a border colour
reads as a second border; a 3px translucent glow does not" — and the recorded
bug is about MECHANISM. Chromium tracks input modality per frame: after any
keypress, `:focus-visible` matches on every later focus whatever caused it, so
returning to the window re-focuses `document.activeElement` and a link the user
CLICKED comes back ringed. A translucent aqua glow around a clicked nav link is
the same defect in a nicer colour.

**What shipped instead.** `lib/focusModality.ts` publishes `data-modality` on
`<html>`, and the rule is gated on it:

```css
html[data-modality='key'] :where(a, button, input, textarea, select, [tabindex]):focus-visible {
  box-shadow: var(--ring-focus);
}
```

`key` is set only by keys that MOVE focus — `Tab`, the arrows, Home/End. Setting
it on any keydown would rebuild the original bug in our own state, because
`mod+k` would arm it and the next pointer-driven focus would ring. Both
listeners are capture-phase, so a component calling `stopPropagation()` on a
handled binding cannot leave the modality stale.

**Verified, both directions, with real input rather than synthetic events** —
`el.focus()` does not make Chromium agree an element is `:focus-visible`, which
is the same reason `ui_smoke.py` presses Tab instead:

- Tab → `data-modality="key"`, `:focus-visible` matches, `box-shadow` is the
  aqua glow.
- Click, *after* a keypress has already occurred in the frame → `pointer`, and
  no ring, even though Chromium still reports `:focus-visible`.

**`border-radius` is deliberately dropped** from the bundle's rule. A spread
box-shadow already follows the element's own border-box shape, so that
declaration's only effect is to square off anything rounder than `--radius-md`
at the moment it takes focus — the `Toggle` track and the `Spinner` are both
`--radius-pill`. Changing an element's shape on focus is the same class of
defect as moving it.

**What this does not fix.** `box-shadow` does not apply to SVG child elements,
so `EntityMiniGraph`'s `tabindex="0"` nodes still have no visible focus
position. That was true before this change and is not made worse by it, but it
is now the only place the gap remains.

**`ui_smoke.py` could not have caught any of this.** Its focus check reads
`outlineStyle`/`outlineWidth`/`outlineColor` only — there is no `box-shadow`
anywhere in that file — so a box-shadow ring, correct or not, passes it
silently. Its assertion that "no control draws a focus ring" is now wrong in the
keyboard case and should be inverted to assert the ring IS drawn after Tab and
is NOT drawn after a click, which is the property that actually matters.
