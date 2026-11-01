---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — One button, one chip, and the `.wrap` collision


Thirteen button CSS blocks across six files — `.go` twice, `.save`, `.cancel`,
`.link` six times — three of them byte-identical. Buttons carry four states
each, and that multiplication is where drift regenerates: five of six `.link`
rules had already diverged on padding and font-size, and only one underlined on
hover. `components/ui/Button.vue` replaces all of them.

`disabled` is deliberately **not** declared as a prop, so it falls through to
the root `<button>` and the browser keeps its semantics. Declaring it would
swallow the attribute and leave a disabled-looking button that still clicks.

`.chip-btn` in `GlossaryView` and `GraphView` was a **second implementation of
`Chip`**, which `CLAUDE.md` prohibits outright. `Chip` gained `interactive`,
`selected` and `size`; all default false/`md`, and the 23 existing usages were
verified inert by computed-style diff before any call site moved.

**The gate caught a real bug.** After migrating the graph's entity-type filters,
every dot rendered grey instead of its entity colour. The default had been
declared on `.chip__dot` — the child — which shadows the caller's inline
`--chip-dot` on the root. It looked plausible and silently lost the colour
coding. The default now sits on the root, where an inline style beats it.

`.wrap` was a genuine collision: `GlossaryView` and `RunsView` scoped
`.wrap { max-width: 900px; margin: 0 auto }` while also using `class="row gap-2
wrap"` — the flex-wrap utility. The scoped rule applied auto margins to those
flex rows, absorbing all free space and killing `justify-content`. Renamed to
`.column`; measured after the fix, the header rows report `maxWidth=none,
margin=0`.

**Verification was a computed-style diff, not a screenshot.** `fontSize`,
`fontWeight`, `letterSpacing`, `color`, `borderColor`, `padding`, `opacity`,
`cursor` and `backgroundColor` for every button, chip and label on all five
routes, before and after each step. Intentional changes had to appear as exactly
the predicted lines and every other line byte-identical. The label
normalisation produced exactly two: `1.32px → 0.88px` letter-spacing on the
brief, and `700 → 600` weight on the graph (it had been inheriting `h4`'s
browser default). Everything else was identical, including the entire Chip
migration.

Two corrections to the plan, found by measuring: `.center` and `.small` were
listed as dead utilities and are live, so four were deleted rather than six.
And the predicted 11px→12px shift on `.link tiny` never happened — `.link` had
already won specificity over `.tiny`.
