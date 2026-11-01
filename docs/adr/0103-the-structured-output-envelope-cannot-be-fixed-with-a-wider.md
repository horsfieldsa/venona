---
status: accepted
date: 2026-08-15
---

# 2026-08-15 — The structured-output envelope cannot be fixed with a wider schema


**Measured, and the answer is no.** `scripts/envelope_report.py` puts the
problem at **39.8% of `analyze`'s structured-output calls** across every run in
the ledger — the model emits the tool-call placeholders `$PARAMETER_NAME` /
`$PARAMETER_VALUE`, or wraps the whole payload in `{'response': '<json string>'}`,
with the correct payload nested inside. `analyze` carries **83%** of all such
waste; `acronym_search`, `brief` and `define_read` sit at 0% across 432 calls.

`llm/prompts.py::output_contract` already established that prompt wording does
not touch this — it is an invocation-layer failure, not a comprehension one.
The obvious remaining fix is to make the schema ACCEPT the wrapper so the first
attempt validates and no second generation is paid for.

**It is not available.** `scripts/envelope_experiment.py`, two documents × three
reps × two arms:

```
API Error: 400 tools.0.custom.input_schema:
input_schema does not support oneOf, allOf, or anyOf at the top level
```

Six consecutive failures, no payload, circuit breaker opened. The API forbids a
top-level `oneOf` in a tool input schema, so the wrapper cannot be expressed as
an alternative branch. The only way to admit it without `oneOf` is to drop the
root's `required` list, which stops the schema validating the real payload at
all — strictly worse than the retries it would save.

Control, same two documents: **0.83 rejections per call**, above the 0.40 corpus
baseline. These were long documents, which is consistent with the artifact
worsening as the payload grows, and is the correlate `analyze` sits at the far
end of (6394 schema bytes, depth 9, 189 keys — 2.7× the next largest).

**What remains** is to stop using the tool-call layer for `analyze`: no
`--json-schema`, JSON in the response text, parsed and validated by us. The
placeholders are tool-invocation scaffolding, so with no tool call there is
nothing to leak and the artifact goes by construction rather than by tolerance.
The cost is real and must be paid deliberately: contract C2 gates on
`structured_output`, `CLAUDE.md` records that `stop_reason` is `"tool_use"` on
success *because* `--json-schema` is used, and we would own both validation and
the retry the CLI currently absorbs. That is a change to `executor._build_argv`
and to C2, not a schema tweak, and it has not been attempted.

**Do not re-propose schema tolerance.** The 400 above is the whole answer.
