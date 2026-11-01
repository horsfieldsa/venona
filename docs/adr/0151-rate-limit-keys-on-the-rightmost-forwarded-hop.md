---
status: superseded
superseded_by: 0162
date: 2026-08-20
---

# 2026-08-20 — The rate limiter keys on the rightmost X-Forwarded-For hop, not on scope["client"]

The per-IP rate-limit key (`middleware._client_key`) reads `X-Forwarded-For`
directly and takes the **rightmost** entry — the address the closest trusted
proxy appended — rather than `scope["client"]`.

**Why.** Production runs uvicorn with `--forwarded-allow-ips '*'`, which makes it
copy the **leftmost** `X-Forwarded-For` value into `scope["client"]`. The
leftmost is whatever the client wrote: a request that rotates the header mints a
fresh bucket every time, and the entire per-IP layer — login, signup, write, and
the API ceiling — evaporates. Nothing of ours is reachable except through the
edge (a reverse proxy in front, the host's edge otherwise), and that edge appends
the address it accepted the connection from to the right of the header, so the
rightmost entry is the one hop a client cannot forge. This was the one HIGH
finding of the 2026-08 whole-surface review (`docs/security/REVIEW-2026-08.md`).

**The stance this reverses.** `_client_key` previously read `scope["client"]` and
its docstring argued *against* parsing the header — "a limiter that trusts
X-Forwarded-For unconditionally is worse than none." That reasoning is right
about the *leftmost* and wrong about the *rightmost*: the fix is not to trust the
whole header but to read the single hop the trusted edge controls. Taking the
rightmost is correct whether the edge appends to a spoofed prefix or replaces the
header outright, so it needs no per-deployment knowledge of the host's internal
CIDRs and no change to `--forwarded-allow-ips`.

**Rejected: pin `--forwarded-allow-ips` to the proxy CIDR.** Clean on the compose
path where the reverse proxy's docker-network address is known, but in production there is no
proxy of ours in front of uvicorn — the platform edge dials the container
directly — so the trusted peer CIDR is neither stable nor documented. Fixing the
key in our own code is deployment-agnostic and unit-testable; fixing it in the
server flag is not.

**Belt-and-braces.** WRITE joined `GLOBAL_BACKSTOP` (a `global-write` aggregate
ceiling) at the same time, so no write path is ever unbounded even if the key is
one day wrong — which also closed the MEDIUM finding that `/api/oauth/register`,
an unauthenticated write, sat in the un-backstopped WRITE bucket.

**Consequence.** `api/middleware.py::_client_key` and
`api/ratelimit.py::GLOBAL_BACKSTOP`/`LIMITS` are the load-bearing sites.
`tests/test_ratelimit.py` pins the properties: a spoofed leftmost prefix cannot
change the bucket key, and the global ceiling refuses a many-address flood.
Reverting to `scope["client"]` reopens the HIGH finding and fails those tests.
