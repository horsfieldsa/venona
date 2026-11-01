---
status: accepted
date: 2026-08-07
---

# 2026-08-07 — The brief's entity set is resolved server-side


`POST /graph/brief` takes a brief id, not the id list the client already has in
`briefs.current.entities`. Sending ids would have been one fewer moving part and
is wrong: a brief written before a merge names the loser, `resolve_mappings`
collapsed that id onto the survivor, and the loser is therefore not a node in
Neo4j at all. A client-sent id set silently omits exactly the entities a merge
touched, and the client cannot see `entity_mappings` and should not be able to.

It also dissolves a problem rather than working around it. The graph never loads
a brief document, so it cannot put the wrong brief into `briefs.current` — which
`BriefView`'s identity line, its Top-N rail and its prev/next arrows all read
live.

`queries.subgraph` still takes an id SET, because that is what Cypher can be
handed; only the endpoint is brief-shaped. Two details are load-bearing:

**Directed (`-[r]->`), not undirected.** An induced query must bind each
relationship exactly once, and `neighborhood`'s `-[r]-` binds twice whenever both
endpoints are in the set — which here is always. That is the duplicate-edge-id
bug `neighborhood` had to fix with `DISTINCT r`; the directed pattern avoids it
by construction.

**Isolated members are included**, for the reason `overview` gives and more
strongly: "this brief named Anduril and nothing else it named connects to it" is
precisely the finding a brief-scoped view exists to surface. They are also shown
by DEFAULT here, unlike on the whole graph — hiding a third of a 324-node field
is what makes it legible; hiding a third of the thirty-five entities one brief
named removes things the reader has already met in prose, in a view small enough
to have no legibility problem to solve. The `orphans` key is tri-state on the
wire for exactly this reason.

The response carries `scope`, not the corpus-wide `coverage` block: "showing 97
of 468 known relationships" printed under a twelve-edge canvas describes a
different population from the one on screen, which is a worse statement than
silence.

**A tap in brief mode selects and does not expand.** Pulling in entities the
brief never named dissolves the frame the view exists to provide, and leaves the
canvas quietly disagreeing with its own address. "explore beyond the brief" in
the detail rail is a real navigation the URL describes truthfully.
