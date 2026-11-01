---
status: accepted
date: 2026-08-20
---

# 2026-08-20 — Responsive support targets 390px and is simpler than desktop, and the graph degrades rather than reflows

Epic L makes the three surfaces (marketing, hub, app) usable on a phone. Three
decisions frame it, recorded here so the work does not drift into a parity
rewrite and so a later reader does not "restore" full desktop layout on mobile.

**390px is the floor, 360px best-effort.** Every view must render cleanly down to
390px CSS px — iPhone 12–16 and the bulk of modern Android. 360px is handled where
it is cheap; below that is explicitly not guaranteed. A floor makes "does this
work on mobile" a testable question (`ui_smoke` at 390×844) rather than an
open-ended one.

**Mobile is a simpler experience, not desktop parity.** venona is a single-user,
desktop-first tool; the phone is the secondary context. Stacking, hiding a
decorative rail, collapsing a multi-pane layout to one column, and outright
"open on a larger screen" fallbacks are all legitimate answers. The bar is
*usable*, not *identical* — chasing parity on a dense analyst UI would cost far
more than the mobile use warrants.

**The graph explorer degrades, it does not reflow.** Its layout is a fixed
three-pane grid — a 272px filter rail, the canvas, a 336px detail rail
(`GraphView.vue:818`, `tokens.css:494–495`). Two rails alone exceed a phone
viewport, and Cytoscape nodes render at 8–18px where tap-selection is not viable.
So below the phone breakpoint the rails are hidden and the route offers a
read-only fullscreen pan/zoom view (reusing the existing `ui.fullscreen` path),
not a responsive redesign of the rails and PathFinder. This is the largest scope
saving in the epic and reflects that the graph is a desktop power feature.

**Rejected: full touch parity for the graph.** A stacking rail redesign plus
touch-usable node selection and filter chips is real effort for a feature whose
mobile use is marginal on a single-user tool. Not worth it; revisited only if the
product gains a genuine mobile-graph audience.

**Consequence.** Epic L (`docs/pm/epics/l-responsive.md`) is scoped to this. The
app's breakpoint scale (`web/src/styles/theme.css:221–223`, today desktop-only
sm720/md860/lg1180) gains a phone tier; `--edge` (`tokens.css:475–478`) collapses
under it; the header nav becomes a drawer below the tablet breakpoint; `Table.vue`
gains a scroll container; `GraphView.vue` gains the fallback. `scripts/ui_smoke.py`
gains a 390×844 pass. All within the design-system rules (no arbitrary dimensions —
the breakpoint tier and the 44px touch-target rung are tokens; ADR 0146's separate
marketing token copy must move in lockstep).
