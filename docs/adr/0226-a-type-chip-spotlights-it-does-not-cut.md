---
status: accepted
date: 2026-09-11
---

# 2026-09-11 — A type chip spotlights; it does not cut

Selecting `Technology` on `/graph` drew **nothing**. The readout said `0 nodes ·
0 edges · 32 unconnected hidden · 768 filtered` and the empty state said "No name
survives the filters on the left" while thirty-two names did, every one of them
connected to something.

**The rule was an induced subgraph**: an edge survived only if BOTH endpoints
carried a selected type (`queries.py`, and `applyVisibility` client-side). That
is a defensible rule on a graph whose edges run within a type. This ontology's
do not. `USES_TECH`, `PRODUCES`, `WORKS_FOR`, `BASED_IN`, `MANAGES`, `LEADS` and
`PART_OF` all run between two DIFFERENT types by construction, so a single-type
selection cut every edge it could have had. The isolated pass then ran last, over
the edges the type filter had just removed, found each node with none left, and
swept the lot. The filter was not merely unhelpful for one selection: it was
structurally incapable of answering the question anybody opens it to ask.

**An edge now survives when EITHER endpoint carries a selected type, and its far
endpoint is drawn whatever its own type.** `types=[Technology]` answers "the
technologies, and who uses them" rather than "technologies joined to
technologies". The far endpoint is drawn at reduced opacity (`node.context`) and
counted in the readout as `N shown for context`, because a reader who deselected
a type and still sees it needs the sentence that explains why, or the rail reads
as broken in the other direction.

**What this gives up, named.** You can no longer remove a type from the picture
by deselecting it: `Country` is a hub every organisation shares, and under the
old rule dropping it thinned the graph. Under this one it comes back as context
wherever an edge reaches it. That is a real loss and it was weighed against a
filter that returned an empty canvas for every single-type selection. If removing
a type earns its own control later, it is a second control and not this one --
`shortest_paths` already shows the shape that would take, excluding the
nationality predicate from traversal rather than from the ontology.

**The isolated pass is unchanged and still runs last.** It was never the bug: a
node orphaned by a PREDICATE chip genuinely has nothing to show, and hiding it is
what makes "show me the org chart" a picture rather than a field of dots. What
was wrong was the type filter manufacturing orphans for it to hide.

## Three more, found in the same sweep

**`neighborhood` had a third semantic.** It filtered on `b.type` alone -- the far
endpoint, seed exempt -- so the same chips meant one thing on the overview and
another on an expansion. It now spells the either-endpoint rule, which
incidentally is what it was reaching for.

**The focus walk dropped declared edges.** `neighbourhoodIds` tested
`source_count < minSourceCount` without the `declared` exemption every other
surface applies. Raise the slider in focus mode and the trunk left the scope
while the canvas went on drawing it -- an edge pointing at a hidden node. It now
spells all three rules exactly as `applyVisibility` does, and `graphFocus.spec.ts`
holds them equal.

**`TRACE A CONNECTION` ignored the filter rail entirely.** The client sent no
filters and `graph_path` passed none, so a route could be walked over a predicate
the reader had deselected or an edge below their source floor -- and the canvas
would then hide the answer it had just returned. Predicates and the source floor
now go with the request. Types do not: they decide what is drawn rather than what
is reachable, so the traced route is PINNED past them instead, nodes and edges
both. A route whose nodes were pinned and whose edges were not draws as loose
dots, which is the answer with the answer taken out.

The path's relationship projection also omitted `declared`, so every trunk edge
on a traced route arrived as unconfirmed and drew dashed.

## Consequence

`queries.overview`, `queries.subgraph` and `queries.neighborhood` share one type
rule; both node queries admit `n.id IN $connected` so a kept edge's far endpoint
is returned, and the `matching` count admits it too -- counting selected types
alone against a `connected` set holding their neighbours drove `isolated_hidden`
negative, which `max(0, ...)` renders as "nothing is hidden" on a view hiding
plenty.

`applyVisibility` runs four ordered passes instead of three, and the order is the
correctness requirement: edges are ruled on FIRST, because which nodes are on
screen now depends on which edges survived. `VisibilityCounts` gains
`nodesAsContext`.

`emptyCanvas` reports `isolated` before `filtered`. Each cause is picked for the
undo it offers, and only one of those undos is guaranteed to produce something:
`show` has withheld names behind it by definition, where `reset filters` throws
away the reader's whole selection to maybe reveal some.

`scripts/ui_smoke.py` asserts it, because nothing else can: the payload was
correct, the counts were correct, and the canvas was empty.
