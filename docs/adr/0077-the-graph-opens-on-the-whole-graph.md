---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The graph opens on the whole graph


`GraphView` seeded on a hardcoded `DEFAULT_SEED =
'prog:next-generation-command-and-control'`. Opening the Graph tab therefore
showed one programme's one-hop neighbourhood — **5 nodes and 4 edges** — with no
indication that the corpus held 229. It answered "what is near this?" when the
question on arrival is "what is here?".

Now: no seed in the URL means the overview. `?seed=` remains the exception, and
is what the brief's mentions, the command palette and the curation queue all
push.

**Two things had to be fixed to make it survive contact with 229 nodes**, both
of which presented identically as *a completely blank canvas with 229 nodes
loaded*:

- **`minZoom: 0.15` was clamping the fit.** A third of this corpus has no
  surviving edge, so the layout spreads wide and `fit` asks for a zoom around
  0.05. Clamped, the viewport sat in empty space.
- **Fitting was the wrong goal anyway.** Nodes are 8–18px at zoom 1; at the zoom
  that fits everything they are sub-pixel. `layoutInitial` now clamps to
  `MIN_LEGIBLE_ZOOM` (0.32) on `layoutstop` — arrive on a readable part of the
  graph and zoom out deliberately. Above 120 nodes the layout also drops from
  `quality: 'proof'` to `'default'` and tightens its spacing; 'proof' is a full
  run to convergence and spends its effort spreading disconnected components,
  which is the opposite of what this view needs.

**Watch for:** "the canvas is blank" and "the canvas is drawing everything at a
scale where nothing is visible" look exactly the same, and the second is much
more likely. Check `cy.zoom()` before assuming nothing rendered.

Also moved: the node/edge counts left the app header for the canvas they
describe, and the brief's "N unresolved" left it for the side rail next to the
reference count it qualifies. Header space is global navigation; a count about
one view's content is not.
