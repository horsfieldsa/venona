---
status: accepted
date: 2026-09-09
supersedes: 0057
---

# 2026-09-09 — The spend cap returns, under metered billing only


ADR 0057 removed `llm.max_budget_usd_per_call` and the `--max-budget-usd` flag,
and its argument was conditional on a fact rather than on a principle:

> On a Max subscription the work is already paid for, so a dollar cap buys
> nothing except a way to abort a call that costs nothing extra to finish.

Under `billing_mode: api` that premise is false. The work is not already paid
for, and the same reasoning now argues the other way.

## What returns, and what does not

`llm.max_spend_usd_per_run` applies **only** when the plan is metered, and is
ignored on a subscription. So ADR 0057 keeps its reasoning intact on the plan it
was written about, rather than being reversed.

It is a **run** ceiling, not a per-call one, and that is the half of 0057 that
survives whole: a per-call cap "fails in the worst direction, killing the long,
expensive-looking calls that are usually the valuable ones." A run ceiling
limits attempted WORK, which is the honest bound `max_calls_per_run` already is.

Hitting it defers the rest and ends the run `succeeded_partial`, never `failed`.
A partially useful run reported as a failure trains you to ignore the status.

## Why it is needed rather than tidy

`docs/ADMIN-GUIDE.md` records that **Look up all** "is the only control that can
spend tens of dollars from one press". On a subscription that sentence describes
a shadow price. Under an API key it describes a bill, and there was nothing
between the press and the card.

## The figure the modal quotes had to be scoped too

`_lookup_stats` averages the cost of recent lookups so the confirmation names a
real number rather than a constant. It read the whole ledger. After a switch
that average is the OTHER service's prices, and under a metered plan it is a
quote for real money derived from a service nobody is being billed for.

`lookup_run_costs` filters by service. With no samples the modal already says
its figures are estimates rather than measurements, so a fresh switch degrades
to the honest state instead of a confident wrong one.

## `cost_usd` now means four things, and the column says which

One column holds a subscription's shadow price at published rates, what a
metered provider billed, tokens multiplied by a price table this repo maintains,
and a plain zero. Summing across them is meaningless, so `llm_calls.cost_basis`
records which -- and the spend ceiling counts only `measured` and `computed`.
Capping a run against money nobody was charged is the exact confusion that put a
spend cap on a flat-rate plan in the first place.
