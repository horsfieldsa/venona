---
status: accepted
date: 2026-09-14
---

# 2026-09-14 — Entitlement is written straight to the repository, not through `user_service`

Role, status and the session epoch are changed only through
`services/user_service.py`. The entitlement columns are not: `api/routers/mgmt.py`
and `api/routers/auth.py` apply the hub's push through `users_repo.set_entitlement`,
and `venona users comp` writes the one local value through `set_comped`.

**The two halves differ because one decides policy and the other records a fact.**
`change_authority` has five refusals to enforce and a lock to hold: it may not
raise authority above the caller's, it may not target the caller, it may not
remove the last active administrator, it takes `pg_advisory_xact_lock` on the
row because the read-then-write is not safe at READ COMMITTED, and it writes an
audit row. Entitlement has none of that. It is a projection of what the hub
already decided, and there is no policy to apply on the way in.

**Putting it behind the service would invent a decision that is not ours to
make.** A refusal in that path would mean an instance overriding the hub on
whether somebody has paid, which is exactly the split ADR 0214 draws: the
switches are thrown from the hub and the instance applies them.

**This asymmetry is currently deducible only by grepping call sites**, and that
is what made `db/repositories/users.py`'s module docstring claim `user_service`
was "the only write path" — true of the half it was written about, false of the
module, and it stood until 2026-09-14.

**Consequence.** `db/repositories/users.py` keeps the load-bearing sentence and
cites this record. The rule to hold is that a NEW column decided by an instance
belongs behind `user_service`; a new column pushed by the hub does not. Related:
ADR 0211 (what a lapsed reader keeps), ADR 0235 (their account), C12's
entitlement addendum.
