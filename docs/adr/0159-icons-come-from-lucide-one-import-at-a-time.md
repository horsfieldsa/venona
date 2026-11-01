---
status: accepted
date: 2026-08-24
---

# 2026-08-24 — Icons come from Lucide, one import at a time

The icon set grew past what hand-drawing sustains in one slice: the hover
card's footer became an icon bar, and every worded button app-wide gained a
leading glyph through `Button`'s `icon` prop — roughly seventeen new glyphs,
against twelve drawn by hand in total before it. `lucide-vue-next` supplies
the new ones. Lucide is stroked, `currentColor`, hairline (Feather's
successor, ISC), and at stroke 1.875 on its 24 grid it carries the same
ink-to-grid ratio as the house 1.25 on 16 — the two sources are
indistinguishable at the 12–14px the app renders.

The dependency is fenced by `Icon.vue`: every Lucide glyph is imported one
component at a time into its `LUCIDE` map, which is the allowlist — the
bundle carries only what a name in that file can reach, and a call site says
`<Icon name="...">` without knowing the source. The twelve hand-drawn paths
stay: they are shipped design (ADR 0145), and two of them — the cog and the
question mark — exist because generic replacements failed at 14px.

Rejected: Font Awesome, whose free set is filled and chunky and clashes with
every glyph already shipped; and hand-authoring the seventeen, which the old
doctrine ("an icon has to earn its place against a word, and mostly loses")
would have demanded. That doctrine survives in narrower form: an icon alone
rides `IconButton`, whose required `label` keeps the word as the accessible
name and hover title; beside a word it rides `Button`'s `icon` prop; a
keyboard-first row keeps its `Kbd` cap and gets no glyph at all.

**Consequence:** `lucide-vue-next` is a dependency of both `web/` and
`extension/` (the content IIFE bundles `@`-aliased web components), pinned to
the same version in both. New glyphs are added in `Icon.vue` only — an
import plus a map row — never at a call site. One glyph per semantic cluster
(`check` is save AND apply AND approve), so the vocabulary stays learnable.
