---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Entity minting happens from the entity list, never from a relation alone


A relation endpoint the model named only inside a relationship is weak evidence
— it routinely names an organisation in passing without extracting it as an
entity. `_resolve_endpoint` therefore resolves with `allow_mint=False`: it will
link to a known entity but never create one.

Without this, every passing mention in an evidence sentence would mint a node.
