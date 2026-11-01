---
status: accepted
date: 2026-08-18
---

# 2026-08-18 — The domain descriptor is `config/domain.yaml`

Every string that names the subject domain moves into one editable document:
slug, display name, tagline, login blurb, the descriptor phrases the prompts
splice in (`press_descriptor`, `news_descriptor`, `graph_descriptor`,
`define_context`, `search_context`), the User-Agent note, the MCP copy, the
access-request role list, and `prompt_fragments` — whole worked-example
paragraphs for the adjudicate/anchor/acronym prompts.

It lives in `config/` (deployment identity, like `settings.yaml`), not
`profile/` (reader taste). `backend/venona/domain.py` mirrors `ontology.py`:
frozen dataclass, parse-through-the-real-loader validator, `get_domain()`
lru-cached and cleared by `config_service._invalidate`. The slug freezes after
first write, like ontology prefixes.

**The rule that keeps it honest:** templates in code own every invariant
sentence (hard rules, verbatim-quote requirements); the descriptor owns only
domain-flavoured paragraphs and phrases. The defense descriptor is checked in
with strings that reproduce today's assembled prompts **byte-for-byte** — a
golden test holds that, which is what keeps all recorded `fixtures/llm/`
envelopes valid across the refactor (cache keys include the system-prompt sha).
