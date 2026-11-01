---
status: accepted
date: 2026-09-14
---

# 2026-09-14 — A lapsed reader keeps their account, not only their archive

Every route that reads or writes the reader's OWN account is in
`UNENTITLED_ROUTES`: their address and timezone, where their brief is sent and
the history of what was sent, their relevance settings and what they follow,
which assistants they have connected, and disconnecting one. Reading the
product — new briefs, the graph, learning, generating their own brief, using an
agent — stays paid.

**Entitlement is default-deny, so this had to be said rather than assumed.** A
route not named in `UNENTITLED_ROUTES` needs a subscription, which is the
property that makes a reader feature added next month paid by default instead of
free by omission. Applied to settings it produces the opposite of what anybody
intended: an unsubscribed reader locked out of Relevance, Notifications and
Agents — three of the five entries in their own settings rail — each one firing
the global refusal toast, at the moment we most want them to subscribe.

**The line is what the thing IS, not what it costs to serve.** Their address is
not a feature of the product; it is how we reach them. ADR 0211 already draws
this line for reading, and this is the same line drawn around the account.

**Revocation especially.** Somebody must always be able to disconnect an
assistant they have come to distrust, subscription or not. This is the one case
where a paywall would be indefensible rather than merely annoying, and it is
also the one a default-deny table produces silently.

ADR 0211 says a lapsed reader loses agent ACCESS, which stands: USING an agent
is paid. Managing and revoking one is not, and the two were never distinguished
until the table forced the question.

**Rejected: leave settings default-deny and soften the paywall screen.** It
refuses the writes, so a reader could see their notification destinations and
not remove one, which is worse than refusing the page outright.

**Consequence.** `api/authz.py::UNENTITLED_ROUTES` is the server half and
`web/src/router/guards.ts::REACHABLE_UNSUBSCRIBED` the client half; they have to
agree, and a route added to one belongs in the other. Recorded against C12's
entitlement addendum in `docs/CONTRACTS.md`.
