---
status: accepted
date: 2026-08-15
---

# 2026-08-15 — A library's injected CSS outranks every Tailwind utility


`/graph` drew nothing. Header, search, rails and the readout all rendered, and
the readout said **289 nodes · 373 edges**. The API was healthy, Cytoscape had
initialised, `fcose` was registered, the elements were loaded. The container
measured **950 × 0**, and Cytoscape was rendering the entire graph into it,
correctly, at zero height.

Cytoscape injects this into the document at runtime, as the first `<style>`:

```css
.__________cytoscape_container { position: relative; }
```

It is **unlayered**. Tailwind v4 emits `.absolute` inside `@layer utilities`,
and **an unlayered declaration beats a layered one regardless of specificity or
source order.** So the library's rule won, `inset-0` stopped applying, and with
only Cytoscape's out-of-flow canvases as children the box collapsed.

Measured, not reasoned. Three observations, on the live page:

| observation | rules out |
| --- | --- |
| a fresh `<div class="absolute inset-0">` computes `position: absolute` | a missing utility / broken `@source` |
| the container, same classes, computes `position: relative` | anything about the element itself |
| Cytoscape's `<style>` is FIRST in the document, so on source order `.absolute` should win — it loses anyway | specificity and ordering; only the layer explains it |

**This was a `tailwind-migration` regression, and the migration was correct on
paper.** The canvas positioning had been a rule in `GraphView.vue`'s scoped
`<style>` — unlayered, and carrying `[data-v-…]` specificity. It beat Cytoscape
comfortably. Rewritten as `absolute inset-0`, the same two declarations cannot,
because a utility is not merely a shorter spelling of a declaration: it is a
declaration in a **layer**, and layering is a cascade demotion. That is the
general lesson and it is not specific to Cytoscape — any dependency that injects
its own CSS now outranks every utility this project writes.

**The rule: sizing that a library's injected CSS can reach must not depend on a
utility.** `GraphView`'s canvas is now a stretched grid item, so it never
depends on `position` at all — the one property Cytoscape overwrites. Fighting
back with `absolute!` would have worked and would have to be re-won every time
the library touches another property.

**Nothing caught it** because typecheck, lint and the offline suite are all
blind to a computed height. `ui_smoke.py` would have — its "graph draws nodes
inside the viewport" check fails outright at zero height — but it is opt-in,
needs a throwaway instance, and had not been run. It now also asserts the
container's measured box directly, before touching the elements: the older check
needs a populated corpus and reads as "the graph is empty", which is the wrong
diagnosis for a canvas that is not there. `initCytoscape` warns in dev on a
zero-sized mount, because `watchCanvasSize` skips zero-sized boxes on purpose
and so can never recover from one.

**Found alongside, same surface.** The extension's card built
`?mode=focus&subject=<id>` while `fromGraphQuery` reads `focus` (with `seed` as
a legacy alias). `subject` decoded to nothing, so "Open in venona →" opened the
whole graph instead of the entity clicked — and rendered a perfectly good graph
while doing it, which is why it went unnoticed. The extension now writes
`focus`; `subject` joins `seed` as a permanently-read alias, because an unpacked
build survives in a browser long after the repo moves on. `graphRoute.ts` says
nothing should build these URLs by hand, and the one thing that does is the one
thing that drifted.
