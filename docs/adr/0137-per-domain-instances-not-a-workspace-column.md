---
status: accepted
date: 2026-08-18
---

# 2026-08-18 — Multi-domain is per-domain instances, not a workspace column

venona becomes multi-domain: defense, AI technology, and any subject a copy is
pointed at. The isolation model is **one full stack per domain** — app, Postgres
and Neo4j each — not a `domain_id` discriminator on the corpus tables.

The repo had already reasoned about this from the other direction: migration
`0002_identity.sql` deliberately declined a `workspace_id` ("a discriminator on
twenty tables that nothing reads is speculative"), and the old roadmap priced
the deferred column against "the first customer paying for a private graph".
That trigger never fired; what fired instead was wanting whole *subject
domains*, which differ in ontology, prompts, sources and anchors — not just in
who may read. A column shares one schema and one ontology; separate instances
let every domain carry its own.

**Consequence:** nothing in the corpus schema changes. Cross-instance concerns
(directory, login, membership) move to the hub (0141). Neo4j Community's
one-database-per-server limit stops mattering because each stack has its own.
The read-scope seam in `db/repositories/` stays as built.
