---
status: accepted
date: 2026-08-21
---

# 2026-08-21 — Lists page by offset, with an `items` + `total` envelope

Every list endpoint over a growing table takes `limit`/`offset` through
`deps.page_window(default, cap)` and returns a model carrying `items` plus a
whole-set `total`. The frontend consumes it through
`web/src/lib/paging.ts::createPagedList` (offset = buffer length, append,
de-dupe by id, in-flight guard, stale guard) and
`useInfiniteScroll` (IntersectionObserver against an explicit scroll root,
with a visible "Load more" as the keyboard path). Freshness re-reads the
loaded window (`revalidate()`) rather than truncating to page one.

**Offset, not keyset — and not because offset is drift-free.** It isn't: the
glossary's sort key includes a mutable `sighting_count`, so a row can migrate
between pages mid-scroll under either scheme — keyset silently skips a row
whose key moved, offset overlaps or skips at the window edge. What decides it:

- offset gives a true `total` and jump-ability for free; keyset needs a
  separate count and a cursor encoding;
- two endpoints (`/runs`, `/briefs`) already paged this way, and the client
  idiom (`stores/runs.ts`) already existed to generalise;
- the app is single-user, so the concurrent-writer window is one run landing
  mid-scroll — and the id de-dupe on append plus freshness revalidation
  absorb exactly that.

**A convention, not a generic `Page[T]`.** Every list model carries
surface-specific extras (`RunList.hidden`, `GlossaryList.counts`,
`DefinitionQueue.total_pending`); a generic base would be subclassed
everywhere and buy nothing. The checklist lives in docs/EXTENDING.md
("Add a paginated list endpoint"); `tests/test_pagination.py` holds the
page-disjointness and total-invariance cases.

**The exception is the conflicts queue.** `GET /curation/conflicts` is
bounded (`limit` default = cap = 500) but the client does not offset-page it:
moot rows are dropped in Python after the SQL window, so a page can come back
short while open rows remain, and neither `total_open` (unfiltered by kind,
counts moot rows) nor "last page was full" is a sound has-more signal. A
worked-down queue needs no scroll anyway — the client takes one bounded bite
and reloads when the buffer runs dry, which is safe precisely because an
empty buffer has no pair under the curator's fingers.

**Consequence:** `GET /curation/merges` stopped being a bare array
(`MergeList{items,total}` — a bare list has nowhere to put a total); the
archive index, glossary, and definitions queue are windows, so anything that
assumed a store held the WHOLE set had to stop — prev/next brief arrows read
server-computed `older_id`/`newer_id` off the brief, the command palette
searches `/briefs/search` instead of the index, and archive content-search
rows render from the search results rather than intersecting the index.
