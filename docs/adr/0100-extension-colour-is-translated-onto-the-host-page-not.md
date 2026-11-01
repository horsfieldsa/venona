---
status: accepted
date: 2026-08-15
---

# 2026-08-15 — Extension colour is translated onto the host page, not resolved


The extension painted `--text-primary` onto DefenseScoop's white body. Resolved,
that is `#eef2f5`, which measures **1.13:1** on white: the annotated words were
invisible and only their underlines showed. Reported as "the extension doesn't
respect light mode", which names the right symptom and the wrong layer.

`emit-styles.mjs` was right that a host page has none of venona's custom
properties and that a hand-written copy of `prose.css` would drift. It was wrong
about what resolving a token means. **Every colour in `tokens.css` is a position
relative to `--surface-base`.** `--text-primary` never meant "light" — it meant
"legible on the app's surface" — and a literal is only equivalent to the token
on the ground the token was measured against.

So colour is now translated:

| kind | on a page we do not own |
| --- | --- |
| `--text-primary` | no declaration. `all: unset` inherits, and the host's own prose colour is the one colour guaranteed to read on the host's ground |
| `--text-secondary` / `--text-tertiary` | `color-mix(in srgb, currentColor N%, transparent)`, N recovered at build time as the alpha that composites primary over `--surface-base` into that token — 72% and 59% today, and they move if the tokens do |
| `--entity-*` | emitted twice: the literal, and a companion under `[data-venona-ground='light']` scaled down until it clears 4.5:1 on white. Scaling all three channels preserves HSL hue and saturation exactly, so a light-ground org is the same green, darker |

**The ground is measured, not asked for.** Not `prefers-color-scheme`: that is
the reader's OS setting, and DefenseScoop is white whatever it says. A reader in
dark mode on a white article is the exact case that was broken.
`content/ground.ts` walks up from the article root for the first background that
actually paints and stamps `<html>`, every pass, so a site's own dark-mode toggle
is followed. With nothing opaque the whole way up the canvas shows through — not
readable from script — so it asks the prose instead, because light text is only
ever set on a dark ground.

**3:1 was not enough and the token contract is not wrong.** `docs/DESIGN.md`
commits entity hues to ≥3:1 and `tests/test_contracts.py` holds it; on white the
dark-ground hues sit at 2.9–4.2, so most of them pass on paper. That floor was
set for a solid chip. A 1px dotted underline needs more, hence 4.5:1 for the
generated light-ground set only — the app's own tokens are untouched, and so is
`prose.css`.

`scripts/ext_smoke.py` now measures the computed colours rather than naming
them: contrast of the mention and of the underline against the fixture's white
canvas. Note that Chrome serialises `color-mix(in srgb, …)` as
`color(srgb 0 0 0 / 0.72)`, in 0–1 channels — reading that as `rgba()` is a
factor-of-255 error that parses fine and asserts nothing.

ADR 0133 replaced the derivation in the table above. The light-ground hue is no
longer scaled down from the dark one until it clears white; it is a rung in the
token layer, measured against the light page rather than against white. The
rule this record establishes stands — a light ground needs its own value, and
the generator emits it — but the arithmetic moved.
