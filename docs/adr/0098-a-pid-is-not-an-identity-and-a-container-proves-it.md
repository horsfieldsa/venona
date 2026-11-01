---
status: accepted
date: 2026-08-09
---

# 2026-08-09 — A pid is not an identity, and a container proves it


Preparing venona for a VPS. Everything below is one bug with one cause.

`_process_is_alive(pid)` answered "does this run still have an owner?" with
`os.kill(pid, 0)` plus a `/proc/<pid>/cmdline` match on `venona` or `uvicorn`.
Migration 0008 introduced it for a real and well-understood reason: the API
server starting is not evidence that a CLI run died, and the unqualified version
had marked a live 25-minute `fetch` as `failed` from underneath itself.

That check is exact on a workstation. Pids there are five and six digits, so the
odds that a stale `owner_pid` names a live venona process are negligible.

In a container they are close to one. The API is pid 1, or around 7 under an
init shim, and *every* `owner_pid` written by the previous container is in
exactly that range. So the sequence is: a deploy SIGKILLs a run, its row stays
`running` with `owner_pid=7`, the next container starts, its uvicorn **is** pid
7, `/proc/7/cmdline` contains `uvicorn`, and the check returns True.

`reap_interrupted` skips live pids by design. `lane_conflict` treats them as
busy by design. Both are right; both are being lied to. The research lane is
then wedged permanently — every later run 409s, the nightly run stops happening,
and nothing surfaces it. CLAUDE.md names this failure outright: "a leaked slot
strands every run queued behind it — permanently, and silently."

**Ownership is now `(owner_pid, owner_start_ticks)`.** Field 22 of
`/proc/<pid>/stat` is the process's start time in clock ticks since system boot,
so the pair is unique for as long as the host is up and a recycled pid always
disagrees. It is measured against the *host's* boot, which containers share, so
it survives a container restart; if the host reboots, every pid is gone and
every row reaps anyway.

**Considered and rejected: a random boot id** stamped in `app.state`. It works,
but it needs entropy that `services/` may not reach for (decision 7, enforced by
`lint:determinism`), and it would have to be threaded from the app into a module
that CLI runs also call. The start time is already there, costs one file read,
and is strictly more precise — it identifies the process, not the process's
parent application.

**Unknown ownership still counts as live**, in all three directions: a NULL
`owner_pid` (rows predating 0008), a NULL `owner_start_ticks` (rows predating
0020, or any platform without `/proc`), and a `/proc` entry that vanishes
mid-read. Wrongly deciding a live run is dead is the expensive direction, and
that asymmetry is the whole point of 0008.

**And a reaped run is now `succeeded_partial` when any stage completed.**
Reaping used to follow a crash, so `failed` was nearly always honest. On a
container it is routine — every deploy kills what is in flight — and a pipeline
killed at minute 35 has already written documents, clusters and a brief.
Reporting that as `failed` violates a stated prohibition and throws away the
operator's only signal that the work is there. `stop_grace_period` does not fix
this: 60 seconds is not "not killed", it is "killed 50 seconds later" for a run
that takes forty minutes.
