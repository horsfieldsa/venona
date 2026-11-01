---
status: accepted
date: 2026-08-17
---

# 2026-08-17 — `mcp-types`, not the `mcp` SDK, and the number that settled it


The plan said take the official SDK. Resolving it against the pinned tree said
otherwise, and the measurement is the whole argument:

| | packages added |
| --- | --- |
| `mcp-types` | **1** |
| `mcp` | **16** |

Three of the sixteen reverse decisions already written down in this repo:
`pyjwt[crypto]` pulls **`cryptography`**, which `api/security.py` opens by
saying it deliberately avoided; **`opentelemetry-api`**, which `docs/pm/NOT-DOING.md`
lists under "Not doing: A metrics stack"; and **`httpx2`**, a separate
distribution from the pinned `httpx`, which would put two HTTP stacks in one
process — and `scripts/lint_determinism.py` matches `httpx.(AsyncClient|Client)`,
so it would NOT fire on `httpx2.AsyncClient` and the offline-determinism guard
would have silently stopped covering the second client.

What the SDK offers over the types is transport and dispatch. The transport is
the thing we do not want (see the entry above). Dispatch is `api/mcp/dispatch.py`
— about 120 lines. `mcp-types` still carries what actually rots: the wire models,
`LATEST_PROTOCOL_VERSION`, and the error codes, all maintained upstream.

This is the same trade `api/security.py` made against `cryptography`,
`api/middleware.py` against `BaseHTTPMiddleware`, and `api/ratelimit.py` against
Redis. *Trigger to revisit:* venona needing server-initiated messages —
sampling, elicitation, progress notifications, resource subscriptions — or the
OAuth client registration in ROADMAP. Those are the parts that are genuinely
large, and none is reachable from a read-only server.

**Dual-era, because the spec moved under us.** The `2026-07-28` revision removed
the `initialize` handshake, sessions and `ping`, and added a mandatory
`server/discover`. Clients speaking `2025-11-25` and earlier are still in the
wild. Both are answered; `tests/test_mcp_protocol.py` drives both.
