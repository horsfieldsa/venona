---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — One app bar, not a nav rail plus a command bar


The first design for the UI refresh had a global nav rail (42px) plus a per-view
command bar (40px). 82px of horizontal chrome, and the graph canvas needed a
floating-bar exception to survive it.

Needing an exception for the most demanding view is evidence the design is
wrong. The actual redundancy was the **view title**: a bar reading "Brief"
underneath a nav where Brief is already the active item says nothing. Drop it,
and everything genuinely view-specific — a date chip, counts, Run now, Save —
fits beside the nav.

**Consequence:** `ViewShell` teleports `#meta` and `#actions` into `AppHeader`.
Two rules follow and both are load-bearing:

- **At most three actions in the bar.** This is a single bar's failure mode.
- `AppHeader` must never be `v-if`'d away — it holds the teleport targets, and a
  teleport whose target is missing throws. Full screen collapses its grid track
  to zero instead.

ADR 0181 split the chrome in two and moved those targets onto `ContextBar`
(`#context-controls`, `#context-actions`, and the search and toggle slots beside
them). The rules survive the move: the count still binds, and it is now
`ContextBar` that is lifted rather than unmounted.
