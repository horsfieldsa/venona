---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — Full screen hides the graph's rails


`f` collapsed the app header and left 272px of filter rail and 336px of detail
rail on either side, so the one gesture that exists to give the graph the whole
window gave it about two thirds of one.

**A zero grid track, never a `v-if`.** The rail is `overflow: hidden`, so a zero
track clips it away completely and the reader's own rail state survives
untouched — unmounting it would throw away the path finder's inputs and the
detail rail's scroll, and `AppShell`'s note about full screen already explains
why unmounting anything around this view takes Cytoscape with it. `--rail-hidden`
is a token beside the other three rail widths rather than a literal, so the four
stay one scale in one file.

`[` and `]` would otherwise have had nothing visible to toggle. In full screen
they now leave it and OPEN the rail — an assignment rather than a toggle, for the
same reason the selection watcher uses one: asking for a panel is a request to
see it, and silently toggling hidden state would spring a panel open on the way
out that the reader never opened.
