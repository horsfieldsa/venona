---
status: accepted
date: 2026-08-25
---

# 2026-08-25 — Per-user relevance is derived, never re-scored

Every reader gets their own relevance without the model ever scoring a
cluster twice. The analyze stage stays exactly as ADR 0014 built it -- one
fused call per cluster, per-axis 0-5 scores stored in `clusters.axes`,
conditioned on the sharp first-person instance profile -- and everything
personal is pure Python over those stored scores at read time:

- A reader's inputs are three Postgres rows, not outputs: emphasis WORDS per
  axis (`more`/`normal`/`less`, `relevance_settings`), explicit follows
  (`entity_follows`), and the one prose document a generation produced
  (`personal_briefs`). Absent rows mean the instance defaults; an untouched
  feature stores nothing about anybody (migration 0010, the 0009 posture).
- Weights are DERIVED server-side (instance weight x factor, renormalized to
  1.0) and tier cuts scale with the reader's singular-story reach -- so ADR
  0047's unreachable-cut bug cannot be configured per user, structurally.
  `compute_overall`/`tier_for` gained `weights=`/`cuts=` parameters; the
  instance path (`/api/rescore`, analyze) is unchanged.
- Two signals, never blended. The personal relevance word comes from the
  reweighted axes; "mentions a name you follow" is a separate chip and a
  retrieval GROUP (followed-mention items first). A follow boost into the
  0-100 was rejected: it has no principled magnitude on a calibrated scale.
  1-hop graph expansion was rejected: hub degree makes it fire on most of
  the corpus.
- The personal brief is ONE light-lane model call per reader per day
  (`personal_brief` run kind), keyed (user, date, inputs-hash): lazy on
  first ask, pre-warmed after the daily for readers seen within
  `personal_brief_warm_days`, capped by `personal_briefs_per_day`, coalesced
  by the launcher, and no-op when the day's document already matches the
  reader's inputs. The zero-cost derived list renders instantly and is the
  fallback whenever the document does not exist -- degraded, never blank.
  Spend is O(readers who read), on the cheap stage, never O(corpus x users).
- The shared daily brief is untouched: selection, prose, floors,
  `brief_clusters` and the annotation tier-A chain. `personal_briefs` is a
  separate table pair precisely so no shared query can see it.

Rejected, each for its own reason: per-user analyze passes (O(users x
corpus) spend); embeddings/similarity ranking (no vector infra, and identity
here is by construction, never by similarity); neutralizing the profile into
a "domain lens" (vague profiles produce vague scores, re-analysis under ADR
0025, and it reintroduces per-user conditioning); materialized (user,
cluster) scores (stale on five events, derivable in milliseconds -- ADR
0157's derive-don't-copy); click-derived follow seeding (reverses the
no-passive-tracking stance); eager 06:00 generation for every account (lazy
plus warm bounds spend to actual readership).

**Consequence:** `services/personal_relevance.py` owns the derivation and
`services/personal_brief.py` the surfaces; SQL lives in
`db/repositories/relevance.py`; the reader routes are `/api/relevance/*`,
`/api/briefs/for-you`, `/api/meta/rubric` (the ontology-style reader-safe
rubric projection -- axis ids and descriptions are publishable, the prose
profile stays admin). ADR 0134's owner split extends rather than breaks:
personal emphasis lives under /settings, the instance rubric under /admin.
A rubric axis-set edit makes stored emphasis fall back to instance defaults
with a visible notice -- never a silent renormalization. A reader whose
THESIS (not emphasis) diverges from the instance's is a second-instance
conversation (ADR 0137), not a scoring feature.

*Triggers to revisit:* a reader whose stated interest no setting of weights,
cuts and follows can express -- axis semantics diverging (sit with them,
tune the row, re-derive; still wrong means fired); tens of thousands of
window clusters with the personal read on a hot path (the one case where
materializing buys anything); follows too sparse for niche entities (then
pick-time PART_OF expansion, keeping expansion an explicit choice).
