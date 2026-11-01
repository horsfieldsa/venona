---
status: accepted
date: 2026-09-04
---

# 2026-09-04 — The instance's working surfaces move under /admin


0134 split the app by **owner**: `/settings` is what belongs to the person,
`/admin` is what belongs to the instance. It drew that line through
configuration and stopped there. Curation and Runs stayed as top-level header
destinations, which left the split half-applied — both are `meta.operator`,
both are refused for readers by `api/authz.py`'s default-deny, and neither is
the reader's. They are the instance being *worked* rather than the instance
being *configured*, and that is not a different owner.

So they move: `/admin/curation`, `/admin/runs`, `/admin/runs/:id`,
`/admin/runs/:id/metrics`. The rule 0134 wrote stands unchanged; this is the
rest of the tree obeying it.

**They leave the header nav entirely rather than pointing at the new paths.**
The bar goes from eight destinations to six, and only the last is operator-only
— so a reader is now offered five of six rather than five of eight, and the two
links they could never open are gone. Every operator destination is reached
from `AdminRail`, which is the surface that can give each one a row, a name and
its own count. The cost is that the `/admin` entry's `names` list now claims
eleven routes; `test_every_route_name_is_claimed` is what makes that safe to
forget about.

**The two live indicators went with their destinations, not to the `/admin`
entry.** The header used to carry the curation backlog as an amber count and
the pipeline spine as work in flight, and the two are not interchangeable: a
badge is a queue waiting for you, a spine is something moving. Collapsing both
onto one nav item would have meant one slot with three meanings decided by
precedence, and the backlog disappearing for the length of every run. They are
rows on the rail instead. What the header keeps is the run-status pill, which
already said the stage in words (`analyse · 3/6`) and links straight to the
run — the one in-flight signal available from a page that is not under
`/admin`.

**The old paths were removed, not redirected**, which is the call 0134 made and
the argument has not changed: single-tenant, pre-launch, no third-party links
to keep alive, and a redirect table that outlives its reason is how a router
accumulates paths nobody can explain. The catch-all is what makes it safe.
`ui_smoke` asserts that each retired path lands on the 404 **at its own
address** — that check previously asserted a bounce to the brief, which is the
behaviour `NotFoundView` exists to reject: a silent redirect cannot tell a
reader whether they mistyped or followed a stale link.

**`CurationView` is the first caller of `RailLayout`'s `fill`.** It is the one
page under `/admin` that is a fixed frame rather than a document
(`scroll="none"`, an inner region that scrolls), so the rail had to be added
without breaking the height chain that keeps its action bar on screen. Measured
after the change: the grid takes the shell's `flex:1`, the `Panel` takes the
grid's row, and the action bar sits inside the frame at desktop width and in
the stacked single-column case alike.

*Trigger to revisit:* a curator who is not an administrator. Curation is here
because today those are the same person; a third role would make it a page an
operator opens for someone else, and it would move back out.
