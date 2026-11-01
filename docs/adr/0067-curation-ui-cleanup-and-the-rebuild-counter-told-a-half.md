---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Curation UI cleanup, and the rebuild counter told a half-truth


### Consistency

`kbd` had been implemented three times, identically, in `MergeReview`,
`DefinitionQueue` and `CurationView` — the exact multiplication `Button.vue`
exists to stop, regrown in a day. It is now `components/ui/Kbd.vue`.

The rest was a semantic mapping nobody had written down, so each surface guessed:

```
decision            default size · primary when suggested, else outline
destructive         outline + tone="critical"        (Reject)
modifier/navigate   text + size="sm"                 (Swap, Skip, Undo)
sub-form            size="sm"                        (Save/Cancel while editing)
```

Two things were backwards under it. **Undo carried `tone="critical"`** — undo is
recovery; the consequential buttons are the ones above it. And the merge button
read `Merge into "{{ survivorName }}"`, so it **resized between pairs**: the
control under the cursor moved as the queue advanced, which is the one thing a
keyboard queue must never do. The target moved to its own line, where reflow
costs nothing.

`DefinitionQueue` also rendered its actions *inside* the active row, so the list
reflowed on every `J`/`K`. It now uses the same pinned bar as the merge queue,
acting on the current row.

### The counter

Reported: *"as I merge things I don't see the rebuild graph increment as much as
I merge."* That was two separate things, and only one of them was correct.

**The undercount was right.** Only `active` entities are projected, so merging
two uncorroborated ones removes nothing from the graph — neither was ever in it.
47 merged entities held **1** node between them. Counting merges instead would
promise a rebuild that had nothing to do.

**The omission was not.** Every `relate` verdict writes a PART_OF assertion, and
**ten existed with none of them in the graph and nothing saying so.**

They are now counted, but as a *separate* number, because all ten had an
uncorroborated endpoint and therefore cannot project at all. Folding them into
`pending_rebuild` would have left the banner permanently non-zero behind a
button that could not clear it — worse than not counting them, since it trains
the operator to ignore the banner. So:

```
pending_rebuild    1    a rebuild applies this
blocked_relations 10    corroboration applies this (`venona research`)
```

Which is the 79%-invisible finding arriving from a new direction: the curator is
recording real structure — `Amazon Web Services PART_OF Amazon` — and the graph
cannot show it until both ends earn corroboration.
