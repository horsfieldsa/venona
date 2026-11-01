---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Vendor IR feeds dropped; trade press covers vendors better


`investors.palantir.com/rss/pressrelease.aspx` probed OK during design and was
302-redirecting to a 404 page days later. `venona verify-sources` caught it
immediately, which is the whole reason `source_health` exists — a silently empty
feed is indistinguishable from a quiet news day.

Vendor investor-relations feeds move often and carry mostly earnings noise. The
trade press covers the same vendors with more context and stabler URLs. Removed
rather than repaired; the reason is recorded in `config/sources.yaml` under
`removed:` so nobody re-adds it.
