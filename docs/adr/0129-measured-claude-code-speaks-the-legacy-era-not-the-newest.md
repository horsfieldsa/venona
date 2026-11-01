---
status: accepted
date: 2026-08-17
---

# 2026-08-17 — Measured: Claude Code speaks the LEGACY era, not the newest spec


Dual-era was designed from the spec text. This is what the shipped client
actually sends, captured with a throwaway echo server rather than inferred —
Claude Code **2.1.233**:

```
POST /mcp   accept: application/json, text/event-stream
{"method":"initialize","params":{"protocolVersion":"2025-11-25", ...},"id":0}

POST /mcp   mcp-protocol-version: <whatever the server answered>
{"method":"notifications/initialized"}

POST /mcp   mcp-protocol-version: <same>
{"method":"tools/list","id":1}
```

Three things follow, and the first is the one that would have cost a day:

1. **It does not call `server/discover`.** A server implementing only the
   current revision — `2026-07-28`, which removed the handshake and made
   discovery mandatory — would be unreachable from Claude Code entirely. Reading
   the newest spec and implementing it faithfully is exactly how you would get
   there.
2. **It echoes the NEGOTIATED version back**, not the one it asked for, so a
   server that answers `initialize` with its own preferred revision is taken at
   its word on every later request.
3. **No `Mcp-Method` / `Mcp-Name` headers**, which `2026-07-28` requires. Being
   lenient about those was the right call; rejecting on their absence would have
   refused the only client we have.

`venona-local: ✔ Connected` in `claude mcp list`, against the real endpoint
through the Vite proxy.

*Trigger to revisit:* a client that calls `server/discover` first. The branch is
already there and `tests/test_mcp_protocol.py` drives it; what changes is which
half of the file is load-bearing.
