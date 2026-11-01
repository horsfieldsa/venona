---
status: accepted
date: 2026-09-08
---

# 2026-09-08 — A lapsed reader keeps the archive as of their lapse date

When a subscription ends, the reader keeps every brief published on or before
the day it ended, for as long as the account exists. They lose everything else:
new briefs, their own tailored brief, the graph, learning, agent access and the
extension. Resubscribing lifts the ceiling and the whole archive returns.

**One date, one comparison, no intervals table.** The alternative — recording
which periods were paid for and showing only those — is more defensible
commercially and much worse in every other way: a returning subscriber has holes
in their archive, the query grows a join per lapse, and nobody can explain it in
a sentence. `entitled_through` is already on the row for other reasons, so the
ceiling is `briefs.date <= entitled_through` and `ix_briefs_date` already
indexes it.

**Someone who paid for a month of reading should keep the month of reading.**
This is not generosity, it is what the money bought. Charging for access to
something and then removing it retroactively is the behaviour that produces
chargebacks and the reason people distrust subscriptions. It also makes the
lapse screen honest: the archive is theirs, today's brief is not.

**And the archive is the best argument for coming back.** The lapsed screen
shows that today's brief exists — its date and its headline — with the body
closed. Nothing sells a subscription like the thing the reader no longer has,
and a reader whose corpus is still there resubscribes far more readily than one
who has to start again.

**The cost is that lapsed accounts stay signed in, and that is expensive.** A
reader who visits their frozen archive refreshes `last_seen_at`, which is what
`users_repo.recently_seen` selects on, which is what
`personal_brief.warm_recent_readers` fans out over at roughly $0.40 a day per
reader. So the entitlement predicate goes into `recently_seen` beside the
`status = 'active'` test that is already there for the same reason — its
docstring says warming a brief nobody can sign in to read is pure spend, and
this is the case where they *can* sign in and still must not be warmed. The gate
also goes on `readers_subscribed`, because `announce_brief` does not merely
announce: it sends the brief, and a delivery path is not fixed by gating a page.

**Where the ceiling is enforced.** Not at the route. `GET /api/briefs/latest`
returns the newest brief at or below the ceiling rather than refusing, because
`/` renders `BriefView` and a 403 there would bounce a lapsed reader to the
paywall instead of the archive we just promised them. The ceiling is a parameter
threaded from the router through `services/brief_service.py` into
`db/repositories/brief.py`, including `select_briefs_by_id` — a bookmarked id
must not walk past it — and `select_brief_neighbours`, or the older/newer arrows
hand out a working link to a brief they cannot open.
