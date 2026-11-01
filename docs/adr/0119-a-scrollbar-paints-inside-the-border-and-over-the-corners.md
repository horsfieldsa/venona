---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — A scrollbar paints inside the border, and over the corners


The entity hover card lost both right-hand corners: square, with the coloured
border missing along the top-right and bottom-right curves. Nothing was missing.
A scrollbar lives in the PADDING box, so it paints inside the border and across
the two corners on its own edge, and `::-webkit-scrollbar-track` was
`--surface-base` — a 10px opaque block sitting on top of a 6px radius.

It reads as a broken border rather than as something painted over one, which is
why it survived a card that is otherwise nothing but a radius and a hue.

**Transparent track, transparent corner, and the thumb's inset taken as a
transparent border plus `background-clip: padding-box`.** The element's own
background then shows through the gutter and the radius clips the thumb the same
way it clips content. Setting the inset any other way means naming the colour
behind it, which is the bug again in a different place — and the hover rule
writes `background-color`, never the `background` shorthand, because the
shorthand resets `background-clip` and the thumb visibly fattens under the
pointer.

One rule in `base.css`, so it covers every scroll container in the app and the
extension's shadow root, which imports the same file. The quieter half of the
same bug went with it: the track was `--surface-base` regardless of what it sat
in, so every `--surface-inset` and `--surface-overlay` scroller had a stripe of
the page background down its edge.

`scripts/ui_smoke.py` asserts it for every rounded scroll container on the page
rather than for this card, via `getComputedStyle(el, '::-webkit-scrollbar-track')`
— the only way to see this without diffing pixels.
