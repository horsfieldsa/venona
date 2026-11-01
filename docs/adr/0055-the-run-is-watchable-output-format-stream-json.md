---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The run is watchable: `--output-format stream-json`


A run spends nearly all its wall clock inside `claude` subprocesses. Discovery
sweeps take ~3 minutes each and analyze runs ~35s per cluster, so the stage strip
sat on "analyze" for twenty minutes at a stretch — indistinguishable from
"analyze, wedged". The only way to know what was happening was to read
`llm_calls` afterwards.

**Measured before changing anything.** `--output-format stream-json --verbose`
against 2.1.220 emits the session as JSONL and its final `{"type":"result"}`
line carries every field contract C2 gates on: `is_error`, `api_error_status`,
`permission_denials`, `structured_output`, `stop_reason: "tool_use"`,
`modelUsage`, `usage`, `total_cost_usd`. So the switch is additive — the
envelope is unchanged and `parse_envelope` now accepts both shapes, which is
what kept every recorded fixture valid. `cache_key_for` is a function of the
call arguments alone, so nothing was rekeyed.

`--verbose` is not optional: without it the CLI refuses with *"When using
--print, --output-format=stream-json requires --verbose"*.

Three constraints shaped `llm/transcript.py`:

- **Volume.** Every event is persisted before publishing (C6), so the transcript
  lands in `run_events`. `--include-partial-messages` is therefore NOT passed:
  token deltas would be ~100x the rows for text nobody reads twice. Measured on
  a real call, message-level costs **3 rows**, so a 230-call run adds ~700.
- **Leakage.** `user` messages carry tool RESULTS, which here means whole
  fetched web pages. Those are already in `documents`; copying them would double
  the database. Results reduce to size plus a 200-character preview.
- **Silence.** `summarize` returns None for most events. `system/status` fires
  constantly and says nothing actionable; an `allowed` rate-limit notice is the
  boring answer. Only the interesting ones cost a row.

`session_started` earns its place by naming the tools the session was actually
granted. Given that `dontAsk` denies tools while still exiting 0, "which tools
did this call really have" is the first question worth answering when a
tool-using stage comes back empty.

`--forward-subagent-text` is on, so delegated work appears with
`parent_tool_use_id` and renders marked rather than as a silent gap.
