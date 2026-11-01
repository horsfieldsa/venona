---
status: accepted
date: 2026-08-18
---

# 2026-08-18 — The domain is configuration; code is domain-blind

New architecture tenet, joining the CLAUDE.md invariants: **no domain-specific
code anywhere**. No defense (or any domain) term in `backend/`, `web/src/` or
`extension/src/` outside of `config/`, `profile/` and `fixtures/`. venona must
be copyable for any domain by supplying configuration alone.

The audit that motivated it found the domain leaking well past the ten
editable documents: seven literal system prompts, two f-string lookup queries,
a 20-word stoplist in `alias_index.py`, MCP server copy, the OpenAPI
description, the User-Agent, the access-role enum triplicated across three
files, and test assertions pinning defense strings. Each is either moved into
the domain descriptor (0138) or made generic.

**Enforcement is a wall, not a review habit** — the same reasoning as
`lint-repositories`: the leak looks exactly like normal code at the call site,
so a sweep holds the boundary. `scripts/lint_domain.py` (`make lint-domain`,
part of `make lint`) scans source for a domain-term denylist seeded from the
audit plus the live descriptor's own vocabulary, with a small annotated
allowlist for tests that deliberately pin the checked-in defense instance.

**Consequence for the wizard:** its few-shot exemplars (frozen defense
documents that set the generation quality bar) are data, not code — they live
under `fixtures/exemplars/` (0147), inside the lint's carve-out.
