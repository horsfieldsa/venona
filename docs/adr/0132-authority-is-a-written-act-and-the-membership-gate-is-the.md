---
status: accepted
date: 2026-08-17
---

# 2026-08-17 — Authority is a written act, and the membership gate is the row


Two decisions that only make sense together, and the first entries in this file
about identity at all — until now the reasoning lived in migration comments and
docstrings, which is where it goes to be lost.

**The row decides sign-in, not the environment.** `routers/auth.py::callback`
gated Google sign-in on `VENONA_AUTH_ALLOWED_EMAILS` while
`AuthMiddleware._lookup_user` decided every subsequent request from the `users`
row. Two halves of one boundary disagreeing about what membership means, and the
visible cost was that `venona users add stranger@example.com --role reader`
produced a row authorized for everything a reader may do and refused at the
door. The env list is now the bootstrap and the break-glass: it is consulted only
when there is NO row, so a row always wins and suspension is not bypassable.

**This inverted a conclusion four places had written down.**
`routers/access.py`'s docstring and its `notify.alert` body, `cli.py`'s access
footer and its decide message, and RUNBOOK's "Nobody can sign in" all said
approval was an env edit plus a restart. An earlier draft of this phase ordered
them deleted as stale. They were not stale — they were the only accurate
documentation of the real gate, and deleting them would have replaced the truth
with a lie. They stopped being true when the gate moved, and were rewritten in
the same commit that moved it.

**Authority may be reduced over HTTP without limit and extended only as far as
`reader`.** `cli.py::_users` kept user management off HTTP entirely, on the
grounds that "an HTTP endpoint that grants a role is reachable by anything
holding a session, so the first admin would be minted by whoever got there
first". That failure cannot occur behind default-deny — the surface is
unreachable until an administrator exists — but the ceiling is what survives of
the argument, because granting admin hands out model spend, every `config/*`
read including the operator's own relevance profile, and `POST /api/data/purge`.
That is an escalation in DURATION, which is the same thing C12 refuses for token
minting.

**The rules live in `user_service.change_authority`, not in the router**, because
`venona users` reaches them from another process and a check in a route is a
check the CLI does not run. Advisory lock, actor re-read inside it, self-target
refusal, last-admin refusal, audit row. The one worth naming is the third: two
admins demoting each other concurrently is a pair of operations each of which is
individually legal and which together leave an instance with no administrator.
It passes review, passes every single-threaded test, and is unrecoverable
without shell access.

*Trigger to revisit:* a second instance, or a workspace boundary. Everything here
assumes one instance whose administrators trust each other with the corpus, and
the ceiling is calibrated to that.
