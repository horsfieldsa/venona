---
status: accepted
date: 2026-08-27
---

# 2026-08-27 — A cacheable result carries its directives, and the guard derives which ones are

Signing in worked and the connection came up with no tools:

```
Reconnected to Venona, but fetching tools failed: Invalid result for tools/list:
[{ expected: number, code: invalid_type, path: [ttlMs], message: Invalid input:
expected number, received undefined }, { code: invalid_value, values: [publi…
```

`ttlMs` and `cacheScope` are required on the 2026-07-28 wire for every result
whose model descends from `mcp_types.CacheableResult` — `tools/list`,
`resources/list`, `prompts/list`, `resources/read` and `server/discover`. Only
`server/discover` set them, by hand, at its own call site.

The SDK's models default both fields, so this is only reachable by a hand-built
payload — and `dispatch.py` is hand-built payloads throughout, which is ADR
0127's deliberate choice (types from `mcp-types`, no SDK). The cost of that
choice is exactly this: conformance is ours to hold, and a field added by a
revision lands nowhere until somebody notices.

**One place stamps them.** `protocol.cacheable` sits beside `protocol.result`,
which already backfills `resultType` for the same reason and with the same
always-present-and-ignored-by-older-peers argument.

**`ttlMs=0`, `cacheScope="private"`** — the SDK's own default, meaning
immediately stale and never shared. The tool table IS identical for every caller
on an instance, so `public` would be defensible and would cut re-fetching. It is
not taken here because turning on a shared cache is a decision about somebody
else's infrastructure, and this was a wire-conformance fix arriving while
somebody was trying to connect.

**The guard derives what must carry them.** `_RESULT_MODELS` maps each method to
its `mcp_types` model and the test asks `issubclass(model, CacheableResult)` —
so the next revision that makes another result cacheable is a dependency bump
and a failing test rather than a silent omission. `initialize` is in the sweep as
the control: it is not cacheable, and stamping a result that should not carry
them is drift in the other direction. A hand-maintained list of "these five
methods" is the thing this repo has already learned twice not to write
(`lint_tokens`, `test_header_parity`).
