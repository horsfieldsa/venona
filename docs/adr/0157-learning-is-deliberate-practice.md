---
status: accepted
date: 2026-08-23
---

# 2026-08-23 — Learning is deliberate practice: derived cards, FSRS, learner-only progress

The product gets people fluent by reading for them; epic O adds the active
half — flashcards, multiple choice, cloze and relationship questions over
the corpus, with per-learner progress. Four decisions, made with the
operator at planning time (2026-08-23).

**Only explicit study actions are recorded, and only the learner sees the
result.** The operator's direction was unambiguous: no passive tracking,
users initiate learning. So the write path is one endpoint whose subject is
always `actor_id(request)` (the `PATCH /api/auth/me` structural argument —
the body carries no user id to substitute, `extra="forbid"` makes a smuggled
one a 422), progress endpoints return only the caller's rows, and there is
no admin per-user surface at all. Epic J's passive per-seat capture remains
blocked on its own step-zero decision; `POST /api/annotate` keeps writing
zero rows and its test stands. The rejected alternative — a manager/team
coverage view, which the enablement pitch wants — is exactly the thing that
reverses the product's privacy promise, and it is not bought back piecemeal
by a study feature.

**Cards are derived at read time, never materialized.** A card is a
rendering of a row that already carries invariant 3's provenance — an
entity definition, a gloss, a curated assertion, an approved generated
question. Materializing cards would copy sentence-traceable facts into rows
that go stale when a definition is edited, rejected, or its entity merged.
Derivation makes "card exists ⇔ sourced fact exists" structural: merges
resolve through `entity_mappings` to the survivor's key, disappeared facts
stop deriving, and the per-user FSRS row for a vanished card goes dormant
rather than being deleted — memory state is history, and the review log is
append-only. One scheduling unit per fact; formats rotate deterministically
(`sha256(card_key:reps)`), so the deck is not quadrupled by having four
formats.

**Scheduling is FSRS with default weights.** The benchmarked
state of the art (Anki's default; ~20–30% fewer reviews than SM-2 at equal
retention on the public 500M-review benchmark), and below ~1,000 reviews the
default weights perform like a tuned SM-2 anyway, so per-user weight
optimization is deliberately out of scope. The math lives in `services/`
pure and injected (`now: str`; interval fuzz from a hash, not `random`), so
`make lint-determinism` holds and a replay is byte-identical.

**Generated questions are proposals.** The `questions` run (light lane,
operator-triggered with a call preview) writes rows as `proposed`; a person
approves or rejects them in the curation queue before they enter any deck —
the same posture as merges and definitions: model output is never
self-admitting. The model returns a `source_index` and a quote; code
verifies the quote is a substring of our own `documents.text_nfc` and
computes the span, or the row is never written. The prompt is
`profile/learning_prompt.md`, user-editable through the Admin rail like
every other prompt (`config_service.EDITABLE`).

**Consequence.** Migration 0009 adds `learning_cards`, `learning_reviews`,
`llm_questions`, `learning_settings` — the first genuinely per-user data
tables in the corpus schema. `/api/learning/*` reader routes join
`READER_ROUTES`; the deck ranks in Postgres (not Neo4j — the projection
must stay rebuildable and optional); the web app gains `/learn` and a
fourth curation tab. Epic O tracks the slices.
