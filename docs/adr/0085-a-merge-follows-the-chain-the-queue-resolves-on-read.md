---
status: accepted
date: 2026-08-07
---

# 2026-08-07 — A merge follows the chain; the queue resolves on read


Merging what the screen called "U.S. Army" answered
`survivor 'org:army' is merged -- unmerge or reinstate it first`, naming an id
that had never been on screen. Two of four open conflicts were in that state.

`org:army` had been merged into `org:u-s-army` earlier. The conflict filed
before that merge still named `org:army`, and the two halves of the review
screen disagreed about what that id meant: `list_conflicts` joined `entities`
on the raw id and showed `Army`, while the cards resolved it through
`entity_service._entity_row` and showed `U.S. Army`. The curator merged what
the cards said; the button posted what the queue said.

**A merged id is a signpost, not a wall.** It has no independent existence and
every read path already resolves it, so a request naming one can only mean the
survivor. `merge_entities` now resolves BOTH sides before its guards. The
refusal that remains is `rejected`, which is what the old comment was really
protecting: a rejection is a person's verdict that an entity was hallucinated,
and following a chain into one would quietly reinstate it. A tombstone is not
that.

**Resolved on READ, never rewritten.** `list_conflicts` remaps through
`entity_mappings` at query time rather than repointing rows when a merge lands.
The stored ids stay as filed, so an unmerge restores the original pair for
free, and existing rows heal the moment they are next read — no data migration.

**Closing had to follow.** `close_conflicts_for_pair` compared the two ids as
given. Once callers act on resolved ids, a merge of (survivor A, survivor B)
would miss a row filed under (raw a, raw b) and leave answered work in the
queue — the same staleness one layer up. It matches on `merge_closure` now.

`curate` closes conflicts whose sides have collapsed into one entity, as
`merged`, so they leave the open count with a reason rather than being filtered
out of every read forever.

**Consequence:** `test_merging_into_an_already_merged_entity_is_refused` was
asserting the bug and is gone. `test_a_cycle_is_refused` still passes but for a
different reason — chain resolution catches a cycle one step earlier and says
something more useful. The cycle guard stays for data where the mapping is
inactive, which only a hand edit produces.

**Still open:** the default survivor is the longer name, so this pair defaults
to "the U.S. Army" surviving over the active "U.S. Army". Swap fixes it per
row, but the heuristic is wrong when the extra length is a definite article.
