---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — `reap_interrupted` killed a live run in another process


Caught in the act, which is the only reason it was caught at all.

A full pipeline run started from the CLI was 25 minutes into `fetch`, with three
`claude` subprocesses working, when an unrelated file edit triggered uvicorn's
reloader. The API server ran `reap_interrupted` on startup, and it marked
*every* row in `running`/`queued` as `failed` — including that run, which it did
not own and which was perfectly healthy.

The run continued and overwrote the status when it finished, and that is what
makes the bug nasty rather than merely wrong: the UI reports a failure, the
operator believes it, and an hour later the evidence rewrites itself.

Same root cause as `RunRegistry` being an in-process `asyncio.Lock`: **nothing
in the system knew which process owned a run**, and `venona run` is not the API
server. "The server started" was never evidence that a run died.

Migration 0008 records `runs.owner_pid`. Reaping now requires evidence the owner
is gone — no pid (every historical row), or a pid that is no longer a live
venona process. Liveness is `os.kill(pid, 0)` **plus** a `/proc/<pid>/cmdline`
check, because pid reuse would otherwise resurrect the same bug on a busy
machine.

The negative test is the one that matters: a run whose owner is alive must
survive a reap.
