---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — `--permission-mode dontAsk` silently denies tools


**Measured.** A call with `--tools "WebSearch,WebFetch" --permission-mode dontAsk`
returned `is_error: false`, exit 0, `api_error_status: null`, a well-formed
`structured_output` — and `permission_denials: [WebSearch, WebFetch, WebFetch]`.
The model dutifully answered `found: false`.

The earlier design would have read that as "no grounded definition exists."
Discovery and acronym Tier 3 would have returned empty results **forever, with
no error anywhere**.

**Now:** tool-using calls pass `--allowedTools`, and a non-empty
`permission_denials` raises `PermissionDenied` rather than degrading to a
result. There is a negative test.
