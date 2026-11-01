---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — The Vite proxy strips `Last-Event-ID`


**Measured.** SSE reconnect worked direct to uvicorn and returned an empty body
through the dev proxy. `Last-Event-ID` is the header the browser sends
*automatically* on reconnect, so losing it means every dropped connection
silently replays from zero — or, as observed, yields nothing at all.

`vite.config.ts` now forwards it explicitly in a `proxyReq` hook, alongside the
existing `proxyRes` hook that disables buffering. Two distinct SSE hazards, both
invisible unless you test *through the proxy*. Verifying against uvicorn
directly proves neither.

Verified end to end afterwards: disconnected after seq 7, waited while the run
advanced to seq 10, reconnected with `Last-Event-ID: 7`, received exactly
8, 9, 10 — contiguous, no duplicates.
