---
status: accepted
date: 2026-08-18
---

# 2026-08-18 — The wizard's exemplars are fixtures, not code, and never live files

Domain-setup generation is **exemplar-driven, not template-driven**: the
meta-prompts embed frozen snapshots of the defense documents as few-shot
exemplars plus explicit structural contracts (sweep-tag↔heading coupling,
weights sum to 1.0, commented YAML). Placeholder templates underdetermine the
quality bar; the defense set *is* the spec.

Two placement rules, each with a failure mode behind it:

1. **Not the live `profile/` files.** A converted instance's `profile/` holds
   the *output* of a previous generation; reading it back would let a domain
   use its own draft as its quality anchor, and quality would ratchet down
   generation over generation.
2. **Not inside the code package.** The domain-blind invariant (0144) bans
   domain text from `backend/`; the exemplars are reference data teaching
   document shape, so they live under `fixtures/exemplars/`, loaded via
   `settings.fixtures_dir`, inside the domain-lint's fixtures carve-out.
