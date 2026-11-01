# Generation exemplars

Frozen copies of the defense instance's documents, used as few-shot exemplars
by the domain-setup meta-prompts (`llm/setup_prompts.py`). The defense set is
the quality bar the generator is asked to match — structure, comment habits,
specificity (docs/adr/0147).

Deliberately NOT reads of the live `profile/` and `config/`: a converted
instance's live files are the OUTPUT of a previous generation, and reading
them back would let a domain use its own draft as its own quality anchor.
Deliberately not inside `backend/` either — domain text is data, and the
domain-blind lint's fixtures carve-out is where it lives.

`anchors_excerpt.yaml` is a slice (the header, the entity shapes, the edge
and ambiguous sections), not the whole 58KB trunk — the exemplar teaches the
file's shape, and the batch schema constrains the content.

Refresh deliberately, never automatically: `cp profile/<f> fixtures/exemplars/`
when the defense set meaningfully improves.
