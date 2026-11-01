---
status: superseded
date: 2026-08-04
---

# 2026-08-04 — Model work runs on the `claude` CLI, not the API


The project runs on a Claude Max subscription. `claude -p --output-format json --json-schema` was
verified to return a validated `structured_output` field, so this loses nothing
versus the API's structured output.

**Consequence:** there is deliberately no `ANTHROPIC_API_KEY` anywhere.
`claude --bare` is banned — its own docs say auth becomes strictly
`ANTHROPIC_API_KEY`, which would silently move spend onto metered billing.
`claude auth status` reporting `authMethod: "claude.ai"` is the check.

**Superseded in part by ADR 0216.** The finding holds and the CLI is still how
model work runs. The consequence below does not: an API key is now a plan an
administrator may choose, refused only when it contradicts the one configured.
