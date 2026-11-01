---
status: accepted
date: 2026-09-03
---

# 2026-09-03 — The hub looks like the front door it is (amends 0191)

Two changes to the hub, both about a visitor not being able to tell which
Venona property they are on.

**The field goes behind every public page**, not just the refusals: sign-in,
the chooser, request-access. 0191 put it on refusals only, reasoning that 5KB
of markup is free on a page you hit by accident and not on the sign-in page you
hit every day. That reasoning was never measured. It is 7.6KB raw and 1.65KB
gzipped, taking the sign-in page from 5.2KB to 6.8KB over the wire — and the
person arriving at hub.example.com has usually just come from example.com, where
the field IS the page. Landing on a bare panel on a flat ground reads as a
different company, which is a worse cost than 1.65KB.

It stays OFF the operator pages. `/admin` and its three lists are working
surfaces, and a drawing behind a table of addresses competes with the thing
being read. `tests/hub/test_hub_errors.py` holds both halves, so neither can
drift back by accident.

**The hub's refusals and the old landing site are one page** in every part a
reader sees: the lockup at the top left, a `404 · no such page` status line,
one large sentence, a lede, one way out. The hub's shell grows a `bare` variant
for it — no panel, a centred column, the larger heading — because marketing's
404 sets its body straight on the field and a card is the difference a visitor
would notice first.

The 404 wording is now shared word for word. `errors.PAGES[404]` carries
marketing's sentence, and the ops router's non-operator refusal reads from that
same entry instead of writing its own — a second place writing the 404 copy is
a second place for it to drift, and that one is reached by a signed-in
non-operator who has to see byte for byte what a stranger sees or the
difference tells them the page exists.

The table entries become `(title, message, heading)`. A 404 reads best as a
sentence on the page ("There is nothing at this address.") and as a label in
the tab ("Not found"), which is exactly how the old landing site already splits
its `<title>` from its `<h1>`.

**What the hub still cannot copy.** The foot's theme toggle. The old landing page's
animated canvas was refused here on the same grounds — the hub's CSP was
`default-src 'none'` with no `script-src` at all, and matching the animation
means granting it a script origin — and that call was taken the other way the
same day: **ADR 0203 supersedes this paragraph.** The hub now runs one script,
from its own origin, and the field animates there too.

## Consequences

- `pages.error()` takes `status` and `heading`. Every call site names its
  status, including the three routers that render a refusal directly and never
  reach `errors.PAGES`, so the same code gets the same status line everywhere.
- `STATUS_PHRASES` lives in `pages.py`, not `errors.py`: `CODES` there is the
  machine half and says `not_found`, which is this repo's vocabulary and not
  something to show a reader.
- The `bare` shell variant left-aligns the lockup explicitly. An `<svg>` with
  an auto width stretches to the flex column, and `preserveAspectRatio` then
  centres the artwork inside that box — it read as a deliberately centred mark
  and was not one.
