# Brand

The rules for the venona mark, lockup, colours and type — previously scattered
across `Wordmark.vue`, `Mark.vue`, `AppHeader.vue` and `tests/test_brand_lockup.py`
as component prose. The shipped design is canonical (ADR 0145); the design
handoff kit is a stale ancestor and nothing is restored from it.

**The brand is domain-invariant.** A venona instance for a new domain changes
copy — through `config/domain.yaml` — never the marks, the colours, or the
typefaces. Every deployment states the same identity.

## Colours

| Part | Value | Token |
|---|---|---|
| Plate (the mark's square) | `#1891ac` | `--brand-plate` (= `--aqua-600`) |
| Monogram | `#111111` | `--brand-monogram` (= `--ink-900`, the one brand-kit neutral in tier 1) |
| Wordmark | themed | `--brand-wordmark` = `light-dark(--grey-950, --grey-000)` |

The interface accent (`#3fbdd6`, one rung brighter than the plate) is taken
from the logo rather than invented, so the app and the hub state
the same identity — `docs/DESIGN.md`, "The accent".

The kit's wordmark fill `#d2ecf9` is a **failure signal**:
`tests/test_brand_lockup.py::test_neither_surface_ships_the_kit_wordmark_fill`
pins its absence. The only sanctioned raw `#1891ac` outside tokens and the SVG
sources is `<meta name="theme-color">` in `web/index.html`.

## Sources of truth

| Asset | Source of truth | Copies, and what guards them |
|---|---|---|
| Horizontal lockup | `web/src/assets/logos/venona-logo-primary.svg` (imported by nothing, deliberately) | inline paths in `Wordmark.vue`, `hub/hub/pages.py` and the hub app's `Lockup.vue` — held by `tests/test_brand_lockup.py` |
| The mark | `web/src/assets/icons/venona-mark-aqua.svg` (what `make icons` rasterises) | the icon set in `web/public/` and `hub/hub/static/` (SVG, `favicon.ico`, `apple-touch-icon.png`, manifest PNGs, `manifest.json`) and `extension/icons/*.png` — all written by `make icons`, held byte-identical across surfaces by `tests/test_brand_lockup.py` (docs/adr/0156) |
| Typefaces | `--font-ui`/`--font-mono`/`--font-prose` in `web/src/styles/tokens.css` | extension `fonts.ts` derives names from tokens |

## Usage rules

- **Clearspace**: the lockup carries a 10-unit clear space on every side —
  encoded as the `translate(10, 10)` in the SVG and held by
  `tests/test_brand_lockup.py`. Nothing enters it.
- **Minimum size**: the brand kit sets a **16px floor** for the horizontal
  lockup's box height. App chrome uses 22+ (`Wordmark.vue`'s `size` prop is
  the lockup *box* height).
- **Never typeset the wordmark in a font.** It is drawn paths, always
  (`Wordmark.vue`; the decision record is in `docs/adr/`). If a surface can't
  carry the SVG, it carries no wordmark.
- **Corner radius at small sizes**: `Mark.vue` overrides the asset's 9.6%
  `rx` with `rounded-md` because the drawing's corner reads too soft at
  14–26px. That override is deliberate; keep it.
- The plate and monogram colours never theme; only the wordmark responds to
  light/dark, through `--brand-wordmark`.

## Typography

JetBrains Mono Variable (`--font-ui`, `--font-mono`) and Inter Variable
(`--font-prose`), self-hosted via `@fontsource-variable` on every surface —
never a third-party font host ("a shipped page should not hand a third party a
record of everyone who reads it"). The extension carries latin/latin-ext woff2
subsets registered at runtime; its family names are derived from the tokens,
not written down. Do not name a family that is not in `package.json`.
