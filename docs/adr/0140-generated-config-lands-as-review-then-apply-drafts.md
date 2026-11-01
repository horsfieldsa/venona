---
status: accepted
date: 2026-08-18
---

# 2026-08-18 — Generated config lands as review-then-apply drafts

The domain-setup run's output — profile, prompts, rubric, sections, lexicon,
ontology, sources, anchors — is never written live. Each document lands as a
server-side draft (`profile_dir/.drafts/<key>` + `<key>.meta.json`), and the
admin reviews it in the existing config editor where **Save is apply**, routed
through `config_service.write_doc` so validation, `.history/` snapshots,
atomic replace and cache invalidation all hold.

Why files and not a table: it is the `.history/` pattern run forward — files
beside the documents they propose to replace, already sandboxed in tests via
`profile_dir` redirection, diffable with ordinary tools, and they survive a
database reset.

Why review-then-apply at all: the generator is allowed to use model background
knowledge (anchors especially), and the ANCHOR_SYSTEM carve-out's justification
carries over verbatim — it is safe because a human approves every line before
it lands. Auto-applying a generated ontology would seed a corpus before anyone
had looked at it. Invalid drafts are stored flagged, never silently dropped;
the editor shows the validator's error on Save. Anchors keep their second,
destructive apply step.

**Amended by 0200.** A restored saved version is a third origin for the editor's
buffer, beside a reader's own edits and a staged draft. It is anchored to the
version on disk by `useConfigDocs::restoreSnapshot`, and restoring over a staged
draft asks first, because the draft cost a run to produce and will not be
written again.
