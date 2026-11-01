---
status: accepted
date: 2026-09-02
---

# 2026-09-02 — A generated trunk is judged on whether it connects, and the exemplar has to be a document the wizard would accept

Wiring the defense trunk by hand (216 edges where there had been 65, isolated
243 → 95) surfaced the question of where that shape was supposed to come from on
a NEW domain. The answer was: nowhere. Four gaps, each of which would hand every
generated instance the state this one had to be dug out of.

**The worked example broke its own rule.** `fixtures/exemplars/anchors_excerpt.yaml`
is the only place the model sees what a trunk looks like. It held 14 entities,
all of one type, and **26 edges of which 26 named an endpoint the excerpt never
declared**. `_draft_anchors` drops exactly those (`setup_service.py`, the
`dropped_edges` branch), so the exemplar fed back through the wizard would lose
every edge it has — while teaching that a mature instance is a list of
unattached names. It now declares every endpoint its edges name, and
`tests/test_setup_wizard.py` refuses a dangling one.

**`excluded_from_paths` reached no generated ontology.** ADR 0195 put the flag in
`config/ontology.yaml` rather than in code precisely because the ontology is
per-domain — but `ontology_system` never asked for it and the exemplar ontology
did not carry it, so every generated domain would rebuild the supernode-path bug
the moment its home-jurisdiction edges were filled in. `BASED_IN` is one of the
five mandatory anchor predicates, so this was not a corner case. It is now in the
prompt, phrased structurally ("a predicate whose range is a small closed set that
most of its domain points at"), and demonstrated in the exemplar.

**Jurisdictions were declared last, and could therefore be pointed at by
nothing.** Each anchor round is shown only what EARLIER rounds declared, and an
edge naming an undeclared entity is dropped. The five expansion categories name
sellers, buyers, products, research bodies and people; jurisdictions appeared
only if a round volunteered them. So the home-jurisdiction edge — the one
attachment nearly every entity can carry — was systematically unexpressible. The
web-grounded first round now asks for them.

**Nothing measured whether the result connected.** `dropped_edges` was counted
and never surfaced; no stage counted components, isolated nodes, or entities with
no edge. `validate_anchors` explicitly declines the question, and the only
connectivity number in the repo (`venona anchors status`) needs a populated
database and cannot be pointed at a draft. A draft of names with no structure
counted as a success. `_draft_anchors` now reports both a mostly-floating trunk
and a large drop as work waiting on a person, and carries `floating` in its
counts.

### What this does not do

It does not validate connectivity as a gate. A domain whose press genuinely has
no hierarchy would fail such a gate for being right, and the wizard's output is a
draft a human reviews before Apply. The reviewer is told; nothing is refused.

### Consequence

- `fixtures/exemplars/anchors_excerpt.yaml` (40 entities, 0 dangling edges) and
  `fixtures/exemplars/ontology.yaml` (`excluded_from_paths` on the
  home-jurisdiction predicate).
- `llm/setup_prompts.py`: the path-exclusion rule in `ontology_system`, and the
  edge-writing judgement in `anchors_system` — most-specific-declared-parent, a
  home jurisdiction for every organisation, a local arm taking its own plus a
  parent edge, and nothing at all for a body belonging to no single jurisdiction.
- `services/setup_service.py`: jurisdictions in the first round; `floating` in
  the counts; two `ctx.todo` messages.
- `services/seed.py::_kind_for` defaults the predicate qualifier from the
  ontology, matching `pipeline/analyze.py`. The wizard's schema has no slot for
  `kind` and a hand-written line usually omits it, so without this a generated
  trunk carried a null qualifier on every edge.

**Every setup fixture is a sha over the assembled prompt, and the exemplars are
spliced INTO those prompts** — so any change here invalidates
`fixtures/llm/setup_ontology/`, `setup_anchors_trunk/` and `setup_anchors_expand/`
and `tests/test_setup_replay.py` fails until they are re-recorded. Only the
documents whose prompt actually changed need it; `params["docs"]` scopes a
recording run to those.
