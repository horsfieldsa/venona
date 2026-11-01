---
status: accepted
date: 2026-08-27
---

# 2026-08-27 — Everyone's connections is an admin page, and Settings names no admin function

`/settings/agents` carried a fifth section behind `session.isAdmin()`: every
assistant connected to the instance, whoever owned it, with a Disconnect on each
row and a note pointing at `/admin/people`.

It was in the wrong place, and `SettingsRail`'s own docblock had already written
the rule it broke: Settings is what belongs to the READER, and what belongs to
the instance is on `AdminRail`. Data, People and the config documents were moved
out for exactly this reason. The agent list came back in behind a `v-if`, which
is the shape the rail's docblock warns about.

A reader never opens an admin page. So the section was invisible to almost
everyone who visits the page, and for the one person who could see it, it put an
administrator's job in the middle of their own account settings, ending in a
link to a page nobody else on that screen can open.

It is now `/admin/agents`, a top-level destination on `AdminRail` between People
and Data. `/settings/agents` shows a reader their own connections and how to
make one, and names no admin function at all.

**The hole it fills is unchanged.** `GET /api/meta/mcp` is filtered to the
calling user by design, which left the person who owns the box unable to see
what connections existed on it: a leaked one was invisible to the only person
who could end it. `GET /admin/tokens` is still a second endpoint rather than a
widened first one, and the guard is now the route's `meta.operator` rather than
a component-level `isAdmin()` — a reader never issues the request, so they never
collect a 403 toast for one they did not make.

**Minting is still nowhere.** Over HTTP, access can be taken away and never
handed out (C12); moving the taking-away to an admin page does not change which
half is offered.
