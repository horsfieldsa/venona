---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The brief↔graph round trip had no return leg


CLAUDE.md's first sentence is "a brief where every program and acronym is
hoverable, plus a graph explorer". That round trip was a one-way door.

Clicking a mention pushed to `/graph` recording no origin. There was no
`<KeepAlive>` anywhere in `src/`, no `scrollBehavior` — and none would have
helped, because `AppShell`'s content region is `overflow: hidden` and each view
owns its own scroller, so `window` never scrolls. `BriefView.vue` then refetched
unconditionally on mount. You came back to the top of a freshly re-rendered
nineteen-item document.

Worse, clicking a mention was itself the trapdoor: a dense brief carries several
hundred dotted targets, and one stray click while selecting text ejected the
reader out of the brief entirely.

**Consequence:** clicking a mention now **pins** its hover card — which finally
gives `stores/hover.ts::pinned` a caller, having been declared, guarded three
times and never once set to `true`. "Open in graph →" inside the card stays the
deliberate route, and carries the origin item id, so the graph offers "← back to
the brief" and lands on the paragraph rather than the top. `<KeepAlive>` covers
`BriefView` and `GraphView`; Cytoscape's lifecycle moved to
`onActivated`/`onDeactivated` accordingly, so three hops of exploration survive
a trip to Runs and back.

**Related fix:** `restorePositions` had never worked. `layoutInitial()` starts a
400ms *animated* fcose and does not await it, so the snapshot applied
immediately afterwards was overwritten frame by frame. The fix is to skip the
layout entirely when the snapshot covers every node, rather than race it.
