---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — `web_search_requests` was 0 on every row ever written


Asked directly whether the zero needed fixing. It did, and it was a real bug
rather than an unavailable field.

**Measured** on a call that demonstrably searched — two `tool_use` blocks in the
transcript, a URL returned:

```
usage.server_tool_use.web_search_requests          0
modelUsage["claude-haiku-4-5"].webSearchRequests   1
```

The top-level block exists and is always zero. The search is performed by the
haiku sub-model, so the count is attributed to it — which is **exactly the shape
contract C2 already recorded for cost**: "`modelUsage` carries a phantom haiku
entry; cost is the sum over the map." The same was true of tool counts and was
missed, so the one metric that would explain discovery's spend reported nothing.

That mattered more than it looks. Discovery is ~40% of a run and consists almost
entirely of searching; roughly half of each sweep's cost is the haiku line. "Is
discovery expensive because it searches a lot, or because each search is dear?"
was unanswerable, and the earlier conclusion that the lever is *search count
rather than model tier* rested on inference from cost alone.

A column reading 0 is worse than one reading NULL: it says "no searches
happened" rather than "nobody measured".

Fixed by summing across `modelUsage`, with `server_tool_use` kept as a fallback
and combined with `max` rather than a sum so a future CLI that populates both
does not double-count. `web_fetch_requests` was never read at all and is now
ledgered too (migration 0010) — acronym Tier 3 fetches pages, and search and
fetch have very different costs.

Historical rows keep their zeroes. They are honestly unmeasured rather than
honestly zero, and backfilling is impossible because the envelopes were never
stored.

**The general lesson, now stated twice in C2:** anything the CLI reports
per-model must be read from `modelUsage`. The top-level `usage` block is a
summary of the primary model only.
