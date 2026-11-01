---
status: accepted
date: 2026-08-30
---

# 2026-08-30 — The Runs page reports the usage window, not the dollars


ADR 0057 established that on a subscription what binds is RATE LIMITS, not
money, and that `cost_usd` is measured rather than managed. The backend has
behaved that way since. The UI never followed: the Runs page led with a spend
total, the history feed carried a cost column, and the Admin panel charted
dollars under a caption apologising for them.

That figure is worse than useless. It is API list price for tokens nothing is
billed for, so it is not a cost; and it is silent about the window that actually
stops a run, so it is not a warning either. A reader asking "can I start another
run" got a number that could not answer the question.

## Where the answer comes from

The `claude` CLI emits a `rate_limit_event` on the stream `executor.py` already
reads. Its `rate_limit_info` carries `status`, `rateLimitType`, `resetsAt`,
`isUsingOverage` and a `unifiedWindows` map of every window's utilization.
`_run_streaming` was already parsing every line of that transcript and throwing
this one away.

So the reading costs nothing: no second request, no extra spend, and it arrives
on every call the instance was already making. `parse_envelope` attaches it,
`llm/usage.py::record_call` ledgers it into `quota_samples`, and `/health`
serves the latest reading per window.

**There is no Anthropic API that could have answered this instead**, and this
was checked rather than assumed. The Usage & Cost Admin API and the Claude Code
Analytics API both open with "The Admin API is unavailable for individual
accounts", both require an Admin API key from a Console organization, and both
report tokens and estimated USD rather than window utilization. The Rate Limits
API reports configured limits, not consumption. Claude Code's OpenTelemetry
export works headless and on a subscription but carries no rate-limit state. An
individual Max plan has no organization, so no org-scoped endpoint can address
it. This matters because each instance is on its own individual subscription;
the CLI is the only thing that knows.

## What this does not do

**Display only.** Nothing here refuses, defers or blocks a run, and there is no
threshold at which the scheduler holds. That would be management, and ADR 0057's
whole argument is that conflating the two is what put a spend cap on a flat-rate
plan. The strip informs a person who is deciding; it does not decide.

**`cost_usd` is not deleted.** It stays in the ledger and on the run's own page,
because "where did this run's 52 minutes go" is still answered by looking at
where the tokens went. It is removed from the surfaces that implied it was the
binding constraint.

**No capacity abstraction.** Direct API is a plausible future, and it was
deliberately not designed for: under an API key the reading would come from
`anthropic-ratelimit-*` response headers instead, dollars would stop being a
shadow price, and an organization would make the Admin API available. That will
be a schema change, a health-block change and a UI change. Accepted knowingly,
rather than paid for now against a move that may not happen.

## Two version facts worth keeping

**`unifiedWindows` is newer than 2.1.220, so the CLI pin moved to 2.1.251.**
The older shape carries a reset time and a status with no `utilization` and no
per-window map, which is every one of the 67 recorded fixtures. A reading
arrives and yields no measurable window, so `available` stays false and the
strip says nothing rather than drawing an unmeasured bar at zero.

This was not hypothetical. The first production lookup after this shipped wrote
its `llm_calls` rows and no `quota_samples` at all, because the API image
pinned `CLAUDE_VERSION=2.1.220` — the feature was live against a CLI that could
not feed it. The pin moved in three places that must stay in step:
the API image's `ARG CLAUDE_VERSION`, `LlmSettings.pinned_version`, and
`config/settings.yaml`. The first is what the image installs; the other two are
only what `/api/health` compares against, so moving the settings without the
image would turn the pin check green while changing nothing.

The degradation is kept rather than removed: a downgrade must lose the reading,
not crash, and the parser still drops a window missing either half rather than
inventing a level for it.

**What the version bump does and does not re-verify.** Contract C2's list at the
top of `executor.py` was measured against 2.1.220. Those docstrings still say
so, because they record what was measured. 2.1.251 has been running real
`--json-schema` calls on the development machine since 2026-08-29 — the acronym
and analyze stages both produce valid `structured_output` — which is evidence
for the envelope shape but is not a re-measurement of the permission-denial and
`server_tool_use` findings. Re-measure those if either starts behaving oddly.

`ReplayExecutor` strips the reading. This is load-bearing, not defensive: those
67 fixtures already carry events, all months old, and replaying one would
otherwise assert a stale window utilization as though it were now. Replay
reports no CLI health for the same reason.
