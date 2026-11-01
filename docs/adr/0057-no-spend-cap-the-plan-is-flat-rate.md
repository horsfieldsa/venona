---
status: superseded
date: 2026-08-06
---

# 2026-08-06 — No spend cap: the plan is flat-rate


`llm.max_budget_usd_per_call: 1.50` and the `--max-budget-usd` flag are gone.

An earlier entry recorded that `--max-budget-usd` *works* on subscription auth
and kept it as "a runaway guard on any single call". That was true and beside
the point. On a Max subscription the work is already paid for, so a dollar cap
buys nothing except a way to abort a call that costs nothing extra to finish —
and it fails in the worst direction, killing the long, expensive-looking calls
that are usually the valuable ones.

What actually binds on a subscription is RATE LIMITS, and the honest bound on a
run is `max_calls_per_run`: it limits attempted WORK, degrades to
`succeeded_partial`, and has fired in anger.

**Cost is still measured.** `llm_calls` ledgers every call and `cost_usd` stays
a run counter — that is how "67% of this run was discovery" was answerable at
all. Measurement is not management; conflating them is what put a spend cap on a
flat-rate plan in the first place.

**Scope narrowed by ADR 0218.** Every word here holds on a flat-rate plan, which
is what it was written about. Under metered billing the opening premise is false
and a run ceiling applies.
