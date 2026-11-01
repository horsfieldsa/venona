---
status: accepted
date: 2026-08-27
---

# 2026-08-27 — Corpus text is enveloped on the way INTO our own prompts, not only on the way out to an agent

`api/mcp/untrust.py` was written for one direction: venona hands scraped prose to
somebody else's agent, so it wraps it in `<untrusted>`, neutralises a forged
delimiter, strips every Unicode `Cf` character and bounds the length. Its
docstring names what is adversary-influenceable — `documents.title`,
`assertions.evidence_quote`, `acronym_definitions.grounding_quote`, entity names,
brief block text — and how cheaply: publish a page the discovery sweep will
fetch.

Every one of those fields is also spliced into venona's OWN prompts, raw. It is
imported by four files and all four are under `api/mcp/`. The pipeline uses a
bare label and a rule:

```
llm/runner.py:222    "\n".join(meta) + "\n\n---\n\n" + body
define.py:348        f"ENTITY: {subject}\n\nTEXT:\n{extracted}"
personal_brief.py    f"### [{position}] {item['headline']}"
```

The threat model was correct and was applied to one of the two directions.

**This decision: the same envelope applies to both.** `untrust` moves to
`venona/untrust.py` and every prompt-assembly site passes corpus-derived text
through it.

Top level, not `llm/untrust.py`, which was the first choice and is wrong. The
module is a leaf text utility with no venona imports — the shape `domain.py`,
`timeutil.py` and `ontology.py` already have — and it is consumed by `llm/`,
`pipeline/`, `services/` and `api/mcp/` alike, so it belongs to none of them.
The deciding argument is C12: `tests/test_mcp_readonly.py` bans the token
`executor` anywhere under `api/mcp/`, and `render.py` must keep importing this.
Putting it in `llm/` makes that ban depend on `llm/__init__.py` staying empty
forever. It is empty today; one convenience import in it would transitively hand
the MCP package the one module C12 exists to keep away from it, and nothing
would fail. A leaf module cannot develop that problem.

## Why the "it is only a prompt" reading is not enough

Epic K's review recorded this as *checked, holds*:

> Config strings are spliced into model PROMPTS (data to the LLM) not into
> eval/format-on-attacker-input code paths

True, and it answers a question about code injection. It does not answer the
question about prompt injection, which was never asked of the pipeline. The
asymmetry that makes the pipeline the more interesting target, not the less:

**MCP has nothing to reach.** Its mitigation is structural and total — every tool
is read-only, held by `make lint-readonly`, a symbol-level import allowlist and a
row-count sweep (C12). An injection there buys a wrong sentence in somebody's
chat window.

**A pipeline run has plenty to reach.** It writes assertions, mints aliases,
renders prose to readers, spends the subscription, and in `web_grounding.
search_round` holds `WebSearch` + `WebFetch` — the one call in the app where
corpus-derived text meets a model that can make outbound requests. `urlguard` is
not in front of those two tools; they run inside the CLI.

## What this buys, stated as honestly as the original

`untrust.py`'s own words, which survive the move: it *moves the odds and closes
nothing*. There is no known reliable defence against a model following
instructions embedded in retrieved text. Two things are worth having anyway.

The first is real rather than probabilistic: **NFC is not a sanitiser.**
`normalize.py` normalises document bodies to NFC, which leaves zero-width joiners,
bidirectional overrides and the Tags block exactly where they were. `scrub`'s
category-`Cf` strip removes the class. Without it, injected text is invisible to
the operator reading the run transcript as well as to the model — the operator
cannot review what they cannot see, which is a defect independent of whether any
model is ever fooled.

The second is that the envelope is what a delimiter check can hook onto later. It
costs one function call per splice site.

**The load-bearing mitigations remain structural, and this record does not move
them:** ids are never taken from the model, `overall` is computed in Python, an
acronym expansion needs an article id plus a verbatim quote plus an initials
check, `define` verifies cited sentence numbers, Cypher labels pass ontology
membership *and* an anchored regex, tools default to `--tools ""`. Wiring in an
envelope must not be allowed to read as a reason to relax any of them.

## Rejected: a stricter marker than the MCP one

A second, different envelope for the pipeline — a nonce delimiter, say — was
considered and dropped. Two spellings of one idea drift, and the shared one is
already tested from both ends (`tests/test_untrust.py`,
`tests/test_mcp_injection.py`). One implementation, applied twice.

## Consequence

- `api/mcp/untrust.py` → `venona/untrust.py`. `api/mcp/render.py` imports it from
  the new home; the two test files follow. The module keeps its no-venona-imports
  property, and that property is now load-bearing rather than incidental.
- Corpus-derived text passes `inline()` (titles, headlines, entity names) or
  `quote()` (document bodies, fetched page text) at every site in
  `llm/runner.py`, `pipeline/{define,acronyms,curate,web_grounding}.py`,
  `services/personal_brief.py` and `api/routers/learning.py`.
- **Only USER-turn content is affected.** The cached system prefixes are
  untouched, so prompt-cache economics do not move;
  `tests/test_domain_prompts.py`'s byte-identity snapshots must pass unchanged,
  and a diff there means the change went into the wrong turn.
- A subject reaching a tool-carrying call is capped at
  `web_grounding.MAX_SUBJECT_CHARS` and refused rather than truncated (ADR 0043:
  a rejected lookup has to say why). Measured before the cap existed: a
  5,000-character subject reached a call holding `('WebSearch', 'WebFetch')`.
- **No golden file moved, and that is the result rather than a surprise.** The
  planning note here predicted diffs in `fixtures/golden/prompts/`. There are
  none, because `scrub` is the identity on text that carries no `Cf` character,
  no C0/C1 control and no forged delimiter — which every legitimate headline,
  name and acronym is. The envelope changes what a hostile document produces and
  nothing about what a real one produces. `acronym_search_query.txt` and
  `define_search_query.txt` staying byte-identical is the cheapest available
  evidence of that.
- `tests/test_prompt_untrust.py` is the standing guard, and
  `tests/test_domain_prompts.py`'s snapshots passing unchanged is what proves the
  cached system prefixes did not move.
- Epic T (`docs/pm/epics/t-prompt-safety.md`) carries the work.
