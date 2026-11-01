# Hand-crafted error envelopes

These four cannot be recorded — you cannot reliably provoke a refusal, a 429, or
a malformed response on demand. They are written by hand against the real
envelope shape (see any recorded fixture under `../analyze/`) and are the entire
error-path test suite.

| File | Exercises |
|---|---|
| `refusal.json` | `is_error: false` but `structured_output: null`. Must set `refused=True`, not raise. |
| `rate_limited.json` | `api_error_status: 429`. Must be classified retryable and drive backoff. |
| `permission_denied.json` | **The dangerous one.** Exit 0, `is_error: false`, a well-formed empty answer, and the denial only visible in `permission_denials`. Must raise `PermissionDenied` — treating it as a result means a tool-using stage silently returns "found nothing" forever. |
| `schema_violation.json` | `structured_output` is not a dict. Must not crash. Note `stop_reason: "end_turn"` here, and `"tool_use"` on real successes — which is why success is never gated on `stop_reason`. |

If the envelope shape changes in a CLI upgrade, `make record` a fresh fixture,
diff it against these, and update them to match.
