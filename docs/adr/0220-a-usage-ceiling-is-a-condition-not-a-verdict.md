---
status: accepted
date: 2026-09-10
---

# 2026-09-10 — A usage ceiling is a condition, not a verdict

The first full research run under Codex finished `succeeded_partial` with 36 of
73 calls failed. Every one of them said the same thing:

```
You've hit your usage limit. Upgrade to Pro (...), visit (...) to purchase
more credits or try again at 2:25 PM.
```

The run lost `acronym_search` (16 calls), `define_write` (9), `analyze` (6),
`adjudicate` (3) and its **brief** — to a condition whose own message says when
it ends.

## Why it was graded terminal

`codex.classify` matched a machine-readable code and `_TRANSIENT` already
contained `usage_limit_reached`. The provider did not send one. The ceiling
arrives as prose, so the lookup returned nothing and the default applied.

Six of those in a row opened the breaker, and because the verdict was
`terminal` the circuit reported `transient: false` — which is precisely the
flag the research lane reads to decide whether to wait. ADR 0155's outage wait
was never entered. The lane that is built to sit out a provider having a bad
morning aborted instead.

## What changed

Classification falls through to a **phrase** match when no code is present:
`usage limit`, `rate limit`, `try again at`, `timed out`, and three more.

**Words, never numbers.** Claude Code's driver can match `500` in error text
because its errors are short and structured. This vendor appends a 16-hex
`cf-ray` and a 32-hex request id, so roughly one error in sixteen contains
`500` or `429` by accident and a numeric match is a coin flip. There is a test
that a request id full of digits stays terminal.

## And the reading that would have seen it coming

The same run exposed the second half. `/admin/runs` said Venona could not know
how much of the subscription was left, because `codex exec` carries no window
in any of its seven event types and there is no `usage` or `limits`
subcommand. That was true, and it was the wrong surface.

`account/rateLimits/read` on the app-server protocol answers in about 190ms,
works while four `codex exec` processes are mid-run, and returns exactly what
the strip wants:

```
five_hour   100.0%   resets 2026-09-10T21:25:13Z
seven_day    16.0%   resets 2026-09-17T04:07:41Z
```

That `21:25:13Z` is the "2:25 PM" in the error above. The window was full and
nothing in the app could say so.

**The pipe has to stay open.** The server reads until EOF and exits when it
gets one, so writing the whole request and closing — which is what every other
probe here does — returns the handshake reply and nothing else. `QuotaSpec`
carries a `done_when` sentinel and `_request_response` reads until it, then
reaps the child. That is the only spawn in the file that is not fire-and-read.

**Cached for two minutes, failures included.** The reading is a spawn and it
feeds a strip that already reads one TTL behind by design. A service that
cannot answer must not be re-asked once per model call for the length of a run.
A failed read is swallowed: a run must never fail because the app could not
draw a progress bar.

## Consequences

- `Harness.reports_usage_windows` stays, and both services now say `True`. It
  is not dead: it distinguishes "has not been asked yet" from "never will",
  and the strip's copy differs between them. A third service that genuinely
  cannot report needs it.
- **The protocol is structural.** `Harness` is a `Protocol` and the drivers
  satisfy it by shape, so a default implementation in the protocol body
  reaches no implementer. `quota()` was added with a default and every Claude
  Code call raised `AttributeError` at the point of use — caught by the call
  TIMEOUT tests, which is nobody's idea of where to find that.
  `test_every_service_answers_the_whole_protocol` now walks the whole surface.
