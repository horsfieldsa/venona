---
status: accepted
date: 2026-08-17
---

# 2026-08-17 — Settings is the reader's account; the instance lives under /admin


The authorization phase gave this app two roles, and the frontend did not follow.
`/settings` held seven destinations and five of them were admin-only, drawn
behind `v-if="session.isAdmin()"` — so the page named for a reader's account was
mostly invisible to readers, and their address and agent tokens sat underneath
seventeen configuration documents they may not read. `GET /api/config` is admin
*including the reads*, so this was never only a question of who may press Save.

The split is by owner, not by sensitivity. **`/settings` is what belongs to the
person**: their address, their display name, their sign-out, their agent tokens.
**`/admin` is what belongs to the instance**: its configuration, its people, and
its one irreversible delete. `/admin` gained the section shell and rail
`/settings` already had, so the two read as one system.

**The documents are namespaced at `/admin/config/:key?`, and that is the whole
reason the old routing hazard is gone.** Under `/settings/:key?` a wildcard sat
at the same depth as the literal sections, so `/settings/people` was one
declaration order away from rendering a config-document editor for a document
named "people" — a trap `router/index.ts` warned about three separate times
because it kept nearly happening. A wildcard one level down behind a static
segment cannot reach a sibling. **Never replace these with `/admin/:section?`**;
that reintroduces exactly what the nesting removes.

**The old paths were removed, not redirected.** `/settings/data`,
`/settings/people`, `/settings/:key?` and `/prompts` fall through the catch-all
to the brief. Redirects would have been three lines and are the obvious call —
the argument against is that this instance is single-tenant and pre-launch, so
there are no third-party links to keep alive, and a redirect table that outlives
its reason is how a router accumulates paths nobody can explain. `ui_smoke` keeps
one check that a retired path still lands on the brief rather than a blank page.

**One `RailLink`, not two rails.** The accent-edge row had three implementations
before this, one of which said so in its own comment; forking the rail would have
made four. The primitive is the ROW rather than the rail, because the guide's
contents column needs `<a href="#slug">` and a data-driven rail could not have
absorbed it.

Two reader-facing dead ends were fixed on the way through, both pre-existing:
`EmptyCorpus` — which renders on the brief, the first thing a reader sees —
offered *Run the pipeline* and *Review sources first*, both admin; and the
account page linked `settings.yaml` at an admin-only address. Neither was
reachable, and both were drawn for everyone.

*Trigger to revisit:* a third role, or per-user configuration. Both would break
the "by owner" line this entry draws, which is the thing that makes it decidable
where a new panel goes.

**Amended by 0200.** The split by owner stands, and so does the routing hazard
this record exists to describe. `/admin/config/:key?` became two records --
`/admin/config` for the index and `/admin/config/:key`, required -- which keeps
the wildcard one level down behind a static segment and removes the last way two
routes could claim one address.

**Extended by 0207.** The split by owner stands and is unchanged; what changed
is how far it reaches. Curation and Runs were the instance's WORKING surfaces
sitting outside it, and they moved to `/admin/curation` and `/admin/runs`. The
"removed, not redirected" call above was made again for them, on the same
argument.
