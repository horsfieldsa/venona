---
name: design
description: The venona design system. Load before ANY UI or styling work — web app, hub, or extension — and before adding colors, spacing, type, brand assets, or entity hues. Covers the token architecture, the per-surface ownership rules, and the verification steps.
---

# Design

The shipped design is canonical (docs/adr/0145). `docs/DESIGN.md` is the
design language; `docs/design/BRAND.md` is the brand. Read the section of
DESIGN.md that covers what you're touching before changing it.

## The one-paragraph model

Two tiers in `web/src/styles/tokens.css`: tier-1 primitives (raw values,
never referenced from components) and tier-2 semantic tokens, every color a
single `light-dark(light, dark)` — no second palette block. `theme.css`
re-points Tailwind at the tokens and clears every default scale, so a utility
that isn't a venona rung does not exist. Styling is utilities; `<style>`
blocks live only on `lint_tokens.py`'s two-file allowlist.

## Hard rules (CLAUDE.md "Prohibitions" — the linter enforces these)

- No raw color outside `tokens.css`. No arbitrary dimension (`p-[7px]`).
  `h-(--control-h)` is fine — it resolves through a token. Add a token before
  you add a literal.
- No tier-1 primitive referenced from a component.
- Never split a utility and its conditional override across attributes; never
  interpolate a class name (`bg-${tone}`).
- Bare `<input>`/`<textarea>`/`<button>` only inside `components/ui/`; never
  a second implementation of a `components/ui/` primitive.
- Escape hatch, sparingly: `/* token-lint: ok -- why */` on the line, for
  relationships (mask alpha, currentColor math), never for values.
- **Every page works at 390px, on every surface** (DESIGN.md "Responsive",
  ADR 0152). Simpler than desktop is fine; overflowing it is not. Controls
  rise to `--control-h-touch` under the `xs` tier; tables scroll in a
  container or stack into captioned cards; no new breakpoint outside
  `theme.css`. A view that only renders at laptop width is not done.

## Per-surface ownership

| Surface | Styling truth | Notes |
|---|---|---|
| Web app | `tokens.css` + `theme.css` utilities | the source of everything below |
| Extension | `extension/generated/*` — emitted from web tokens by `emit-styles.mjs` | never hand-edit; rebuild with `make ext-build`; host-page color is *translated*, not resolved (docs/adr/0100) |
| Hub — operator app | `web/src/styles/{tokens,theme,base}.css`, imported across the package boundary | `hub/web` is its own Vue package with the app's *exact* utility vocabulary, so its components are copies of the app's class lists. Under `lint_tokens.py`'s full web lane. Build with `make hub-web` (docs/adr/0223). |
| Hub — public pages | rules in `hub/hub/styles/public.css`; values emitted from `tokens.css` by `scripts/hub_public_styles.py` | sign-in, the chooser, the gallery, refusals. No bundle by design (auth pages must not depend on one), so the sheet is generated and committed, served at a digest URL, and carries the phone block by hand. `make hub-public`; `make lint-generated` diffs it. |

Brand assets: sources of truth and copy-guards are tabled in
`docs/design/BRAND.md`. The brand is domain-invariant — a new domain changes
copy, never marks, colors, or type.

## Entity colors

Entity types are colored through `--entity-<prefix>` tokens (becoming slot
aliases under docs/adr/0139: types declare a `color_slot`, the app injects
prefix→slot at boot). Never invent an entity hue in a component. A new hue
family is tier-1 work and must pass `test_contracts.py`'s 3:1 contrast sweep
on every surface in both themes before anything references it.

## Verify before calling design work done

1. `make lint-tokens` (covers web, extension and the hub).
2. `uv run pytest tests/test_contracts.py
   tests/test_brand_lockup.py tests/test_theme_boot.py tests/test_stylesheets.py -q`.
3. `make dev` and look at the surface in the browser — both themes (the
   toggle is in the header), **and at 390px wide** (DevTools device toolbar,
   or the hub page saved to a file and the window resized). A blank canvas,
   mismatched control heights, a wrong-palette graph, or a table pushing the
   page sideways on a phone are exactly what the offline suite cannot see.
4. Extension styling changed? `make ext-build && make ext-smoke`.
