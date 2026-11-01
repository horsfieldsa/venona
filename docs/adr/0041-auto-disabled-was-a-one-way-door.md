---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — `auto_disabled` was a one-way door


`record_failure` set it; nothing reset it but a success; and an auto-disabled
source is excluded from `active_specs`, so it could never produce that success.
Recovery meant hand-editing SQLite. Three offline runs with a missing LLM
fixture would have permanently disabled discovery.

Four changes, all in the same commit as discovery itself:

- **An LLM failure is not a source failure.** `collect_discovery` returns
  `ok=True` *with* an `error` string; `run_fetch` warns and takes no strike.
  "I am not broken, I just produced nothing" is a distinct state.
- **Zero candidates is success.** A quiet day is not a dead source. This is
  where discovery deliberately differs from RSS, where an empty parse usually
  means the feed moved.
- **`venona sources [--enable ID]`** — inspect the counter, and clear it.
- **`GET /api/health` carries `sources`** (`auto_disabled` and `striking`). An
  auto-disabled source is silent by design; without this, "the brief got thin"
  has no visible cause anywhere in the product.

A fifth, found while wiring it: `asyncio.gather(return_exceptions=True)` is what
makes one dead feed survivable, and it would have turned `PermissionDenied` —
contract C2's hard failure — into a warning string plus a strike. It is now
re-raised out of the loop, along with `RunCancelled`.
