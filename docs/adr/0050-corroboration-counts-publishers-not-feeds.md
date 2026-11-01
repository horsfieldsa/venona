---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Corroboration counts publishers, not feeds


`promote_provisionals` gated promotion on `COUNT(DISTINCT d.source_id) >= 2`.
`source_id` is a **feed**, and one publisher routinely arrives through several:

```
defensedaily.com   -> 4 source_ids
insidedefense.com  -> 3 source_ids
defensescoop.com   -> 2 source_ids
```

Splitting discovery into eight topic sweeps created this. A single outlet found
by two sweeps satisfied "two independent sources" on its own — the opposite of
what corroboration means, and it would have promoted entities into the graph on
one publication's say-so.

Latent rather than historic: no entity had been promoted that way yet, because
the corpus was too thin. Now it counts `DISTINCT site_domain`, which is
populated on all 202 documents.

Same commit: `provisional_promote_sightings` was assigned in the Resolver and
never read, so the thresholds were hardcoded and editing `config/settings.yaml`
did nothing. Now wired, with `provisional_promote_sources` added alongside — it
had no setting at all.
