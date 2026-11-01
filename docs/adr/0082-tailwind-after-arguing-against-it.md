---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Tailwind, after arguing against it


*2026-08-06.*

DESIGN.md had a section called "Why no Tailwind, no component library". It was
right about component libraries and wrong about the cost it was avoiding.

The complaint that reopened it was "the spacing on our elements, bars, grid is
way off and not very consistent". It was. `scripts/style_audit.py` -- written to
answer it -- walks every element on every route and reports what actually
resolves: nine distinct `padding-top` values, nine `padding-bottom`, eight gaps,
seven `margin-bottom` all doing vertical separation, 139 elements padded below
the 4px floor at 1px or 2px, five unrelated `max-height` clamps, three
hand-typed overlay widths, and **four different gaps inside the one 44px header
bar**, whose nav sat 2.4px off the optical line every control in it shared.

None of that was a missing-tokens problem. `--space-1..8` had existed from the
first commit. It was a missing-enforcement problem: `lint_tokens.py` checked
colour, DESIGN.md specified a type scale and was silent on spacing, and
`ui_smoke.py` asserted "at most two control heights". Nothing in the repo could
see a dimension.

**So the reason to adopt Tailwind is the theme, not the utilities.** Cleared
with `initial` and re-declared from `tokens.css`, it generates venona's rungs
and nothing else -- `p-9` and `bg-red-500` do not exist, and the build says so
rather than rendering them slightly wrong.

**Consequence:** `styles/theme.css` joins C5; `lint_tokens.py` gained the
dimension half it never had; `ui_smoke.py`'s header-height assertion went from
`<= 2` to `== 1`.

### What the migration cost, so the next one is cheaper

**Cascade layers are not optional.** Tailwind emits `@layer utilities`, and an
unlayered declaration beats a layered one no matter how specific. `base.css`'s
`* { padding: 0 }` outranked `px-1`, and `a { color: --accent-primary }`
outranked `text-text-tertiary` on every link. Invisible from both ends: the
production stylesheet contained all three rules, and Wave 3's 253 conversions
diffed clean because `.dim` mostly lands on spans. `base.css` is `@layer base`
now, `prose.css` is `@layer components`.

**Two utilities for the same property do not override -- they tie.** Equal
specificity, so Tailwind's sort order decides, not the class attribute's order.
This shipped four times before the pattern was obvious: `icon-btn--sm` rendered
at 24px, the bare chip kept its padding, the selected settings document rendered
unselected, and the active palette row lost its colour. Every property that
varies is now chosen in exactly one branch.

**A closed theme silently drops anything outside it.** `min-w-0`, `w-24` and
`w-60` generate NO rule -- clearing `--spacing` removes the whole scale
including `-0`. The property just vanishes. `--spacing-0` is declared
explicitly; the rest were caught by the audit, which is the argument for having
one.

**Interpolated class names never generate.** `bg-${tone}`, `seg--${status}` --
Tailwind scans source text. Static maps, always.

**Scoped CSS applies to a child component's ROOT.** `RunsView`'s
`<header class="masthead">` wrapped `<Masthead>`, whose root is also
`class="masthead"`, so the rule hit both and the page drew two hairlines 17px
apart.

**Class attributes are hooks, not just style.** `ui_smoke.py` drives the app
through 41 selectors, about thirty of them classes. Only the RULES moved.

### Fixed on the way past, none of them the migration's doing

- Numeric table headers rendered left-aligned above right-aligned data on
  /runs: `.num` (0,1,0) always lost to `.table th` (0,1,1).
- The command palette's search field rendered at 13px/2px instead of its own
  15px/12-16px: `base.css`'s `input[type=search]` is one selector more specific.
- `PathFinder`'s rail showed two boxed fields and one underlined one, because
  only `depth` carries a `type` attribute for `base.css` to match.
- `RunsView` drew two horizontal rules 17px apart (above).
