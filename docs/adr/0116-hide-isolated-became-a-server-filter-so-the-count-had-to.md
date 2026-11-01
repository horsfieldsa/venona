---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — `hide_isolated` became a server filter, so the count had to follow it


`hideIsolated` defaults to true and was applied on arrival, so the server sent
298 nodes and the client immediately set 183 of them to `display: none` — 61% of
the payload and 61% of the constructed elements, drawn never. It is a request
filter now, and `/graph/overview` defines isolated as "not an endpoint of any
edge in this result", derived from the returned edge set rather than from a
second Cypher predicate, so the server and `applyVisibility` cannot drift into
disagreeing about which nodes are unconnected.

**It stays a FILTER with a default of False, never a new default.** Toggling
"show unconnected" still works; it costs a refetch, which is 30ms.

**The part that nearly shipped broken.** The canvas has always said "183
unconnected hidden · show", counted client-side — because the client was the one
hiding them. Move the filter and that count becomes zero, the clause stops
rendering, the `show` link with it, and the app quietly displays 115 of 298
entities with nothing on screen saying so. The payload carries
`isolated_hidden` for exactly this, and the readout adds it to what the client
hid. It was caught by loading the page and reading it, not by any test.

Three client-side consequences, each of which would otherwise be a silent lie:

- `graphShapeKey` includes `hideIsolated` in `all` mode, since it now decides
  what is fetched rather than what is painted.
- The store records whether the resident corpus is MISSING those entities
  (`withheldIsolated`), because "show unconnected" is a question a corpus fetched
  without them cannot answer.
- Choosing the "whole graph" scope out of focus mode refetches instead of
  restoring the position stash. The stash can only put back what is on the
  canvas, and that control's whole point is that it is not allowed to claim the
  whole graph over 61% of one.

The overview's node query also had **no `LIMIT` at all** — the only unbounded
result set in the read path — and its sizing rationale was anchored to "184
active entities, 908 relationships", a snapshot the corpus had already overtaken.
Both caps are sized from the projection's measured SHAPE now, which does not go
stale the same way, and either one tripping sets `truncated`.
