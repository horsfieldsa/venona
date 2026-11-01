---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — The `scraper` source kind is gone, not deferred


Every site that motivated it is a JS-rendered SPA whose served HTML contains
zero content links — Anduril (shader-driven), DIU (`__NEXT_DATA__`), PEO C3N
(DoD PKI). CSS selectors cannot read any of them; they need a JSON-payload
adapter, which is a different protocol.

Advertising a kind the code cannot serve is worse than not offering it: it reads
as "declare a scraper source and it will work", and the failure arrives at fetch
time as an empty result rather than at startup as a config error. `SourceKind`
is now `rss | discovery`, and `SourceSpec.url` became optional with a validator
requiring it for `rss` — discovery has no endpoint.
