---
status: accepted
date: 2026-08-17
---

# 2026-08-17 — Run ownership is a Postgres advisory lock, not a pid and `/proc`


**This supersedes "Ownership is now `(owner_pid, owner_start_ticks)`" above.**
Read that entry first: it is not wrong, and its reasoning is why this one is
shaped the way it is.

That design answered a real container bug. The API is pid 1 — or ~7 under an init
shim — so every `owner_pid` the previous container wrote lands on the new one's
own processes; the cmdline check passes because it really is uvicorn; and the
research lane wedges permanently while both `reap_interrupted` and `lane_conflict`
behave exactly as designed. Field 22 of `/proc/<pid>/stat` fixed it by making
`(pid, starttime)` unique for the host's uptime.

**What it could not do is cross a machine.** `/proc/<pid>` is a statement about
*this* kernel. A pid read from a row another host wrote is not a fact about
anything, and the pair silently reads as "unknown, therefore live" — which under
the old asymmetry means the lane is held by a process that does not exist on this
computer. So the mechanism did not merely fail to support a second host; it failed
in the wedging direction.

**A session-level advisory lock is the same idea with the host removed.**
`create_run` takes `pg_try_advisory_lock(ns, hashtext(run_id))` on the connection
the run will use, and Postgres releases it when that connection ends — clean
close, SIGKILL, container gone, machine gone. The database already knows which
sessions exist, which is precisely the fact `/proc` was being asked for.

**Why not a heartbeat column**, which is the obvious alternative and what the
plan for this phase actually named. A heartbeat has a *threshold*, and both sides
of the threshold are a bug: too short and a busy run is declared dead mid-flight,
too long and a dead run holds the lane for the timeout. It also needs a writer on
a timer inside `pipeline/`, which is a clock (decision 7) in a package that may
not have one. The lock has no threshold and no writer: it is held or it is not.

**Why not `pg_advisory_xact_lock`.** It is released at COMMIT, and a run makes
hundreds of transactions. The session form is the only one whose lifetime matches
the thing being modelled.

**Why read `pg_locks` rather than trying to take the lock.** Advisory locks are
reentrant per session, so a caller that already holds one gets `true` from
`pg_try_advisory_lock` and learns nothing — and acquires a second reference it
must then remember to release. `lease_is_held` is a pure question, so it asks a
catalogue.

**The inverted rule, stated plainly because it is the risky part.** Unknown
ownership used to count as LIVE, deliberately: wrongly deciding a live run is dead
is the expensive direction. It now counts as DEAD. That is safe only because the
lease is unambiguous — there is no "unknown" left. A `running` row with no lock is
positive evidence that no session is executing it, not absence of evidence.
Keeping the old asymmetry on top of a lock would wedge a lane on every crash,
forever, which is the failure CLAUDE.md warns about in its strongest terms.
`test_an_unleased_running_row_does_not_block` pins the new rule and
`test_a_lease_held_by_another_session_blocks` pins that the cross-process case
still works.

**What it does NOT buy: a second API replica.** a self-hosted deployment still says
one, and it still means it. The lease makes a second writer *identifiable* across
hosts, which is a precondition. Rate-limit buckets are still process-local and
`RunRegistry` is still an in-process `asyncio.Lock`, so two replicas would admit
two runs to one lane before either wrote a row.

**Consequences.** Migration 0004 drops `owner_start_ticks`; `owner_pid` survives
as diagnostics only, because "which process" is the first thing an operator asks
and a lock id cannot answer it. `process_start_ticks` and `_process_is_alive` are
deleted. Three test files stopped being able to fake ownership by writing a
column, which is the point — they now create a run on a second connection and
close it, which is the same event a crash is.
