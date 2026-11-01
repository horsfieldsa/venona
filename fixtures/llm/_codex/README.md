# Codex envelopes

Recorded from codex-cli 0.151.0 against a real ChatGPT account, on 2026-09-10.
None of these is hand-written. The point of keeping real ones is the shapes
nobody would think to invent.

## What is here

`_stream/` and `_errors/` mirror the layout beside them, and are read by
`tests/test_codex_envelope.py`.

| file | what it proves |
|---|---|
| `_stream/structured_reply.jsonl` | the ordinary shape: the answer arrives as TEXT inside an `agent_message` item, and this driver parses and validates it |
| `_stream/with_web_search.jsonl` | a `web_search` item completes BEFORE the answer, and its JSON object carries `id` twice |
| `_errors/model_not_available.jsonl` | a provider refusal, as a JSON document nested inside a string field, inside another one |
| `_quota/rate_limits.jsonl` | the app-server exchange that reports how much of the subscription is left, handshake reply and an interleaved notification included, because the parse has to walk past both |
| `_quota/rate_limits_two_buckets.jsonl` | the same read on a different PLAN, where the session window has moved into a second limit bucket and `primary` means the weekly one |

The quota fixture is scrubbed: the home path, user agent, installation id and
credit ids are replaced. The readings themselves are real.

## What is not here yet

**A replay corpus.** These are envelope samples for the parser, not fixtures
keyed for replay. `cache_key_for` is a function of the call arguments, so a
fixture only replays when a run asks the same question — which means the
per-task directories (`analyze/`, `discovery/`, `brief/` …) can only be filled
by `make record`, a real research run with real spend.

Until that exists, the Codex driver is covered by these tests and by live
calls, and `make test` exercises its parsing but not a whole run through it.

## Reading one

The stream is JSONL, one event per line:

```
thread.started    once, carries the thread id
turn.started
item.completed    zero or more; `item.item_type` says what it is
turn.completed    the usage counts, as plain integers
```

`turn.failed` replaces `turn.completed` when the provider refused.

**Take the LAST `agent_message`, not the first.** A placeholder can arrive
ahead of the real answer.

**The searches are in the items, not the usage block.** `turn.completed.usage`
carries token counts only, so a call that searched reports no searches unless
the `web_search` items are counted. Count `item.completed` only — the same
search also arrives as `item.started`.

## The subscription windows are not in this stream

They are real and they are elsewhere: `account/rateLimits/read` on the
app-server protocol, one JSON-RPC request, about 190ms. Two things about that
exchange are worth knowing before touching it:

- **The pipe must stay open.** The server reads until EOF and exits when it
  gets one, so piping the whole request in and closing returns the handshake
  reply and nothing else.
- **The reply is not the last line.** A `remoteControl/status/changed`
  notification can arrive between the handshake and the answer, which is why
  the recorded fixture keeps it.
- **Read every bucket, and name windows by their LENGTH.** `rateLimits` is one
  bucket and `rateLimitsByLimitId` is all of them, and which windows live where
  depends on the plan: measured across one upgrade, the five-hour window moved
  out of the top-level bucket and `primary` came to mean the weekly one. Two
  fixtures are kept for exactly that reason.
