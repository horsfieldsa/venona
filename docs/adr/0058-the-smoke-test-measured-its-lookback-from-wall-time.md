---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The smoke test measured its lookback from wall time


`test_a_full_run_reaches_a_brief` began failing mid-session with
`assert 'brief' in ['analyze']` — the brief stage ran and made no model call.

Nothing had changed. The stub feed carries a hardcoded
`Mon, 04 Aug 2026 12:00:00 GMT`, `pipeline.lookback_hours` is 36, and the
fixture never set `offline`, so `build_context` handed the run a `SystemClock`.
At `2026-08-06T00:12Z` the article fell **12 minutes** outside the window,
`build_retrieval` returned nothing, and `run_brief` returned early.

So the test passed for about a day and a half after it was written and then
broke at a UTC midnight, with a failure message pointing nowhere near the cause.

The fix is the mechanism that already existed: `offline: True` in the fixture's
settings. `FrozenClock` starts at `2026-08-04T12:00:00.000Z` — the same
timestamp as the stub's `pubDate`, because the two were always meant to line up.

Worth stating plainly: `make test` is required to be offline, and that was
satisfied — the executor and HTTP client were stubbed. It was not required to be
**reproducible**, and a stub is not a frozen clock. Any test that exercises a
time window needs the clock injected, not just the network removed.
