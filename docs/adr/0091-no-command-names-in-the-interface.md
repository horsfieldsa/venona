---
status: accepted
date: 2026-08-07
---

# 2026-08-07 — No command names in the interface


Four places told the reader to open a terminal:

| where | said | now |
|---|---|---|
| Curation | "`venona research` chases that" | a button that starts a research run |
| Settings | "run `venona sources --enable <id>`" | a "try again" button per disabled source |
| Runs | "or `venona runs unhide`" | points at the "hidden" chip already on the page |
| Merge history | "and by `venona entity merge`" | "however it was made" |

Three of the four named an action the app can already perform, and the fourth
named one it could not — which is why the copy was written that way, and why the
fix for it was an endpoint rather than a rewording. A command name in an
interface is a dead end twice over: it assumes a terminal, a checkout and a
working `venona` on PATH, and it goes stale silently when the flag changes.

The product name still appears in prose ("venona will not write one it cannot
trace to a source"). That is the app speaking about itself, which is different
from the app asking to be driven from somewhere else.
