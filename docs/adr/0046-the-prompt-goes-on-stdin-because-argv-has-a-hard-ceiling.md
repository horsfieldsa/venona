---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — The prompt goes on stdin, because argv has a hard ceiling


`user_content` was passed as a single argv element. Linux caps one element at
`MAX_ARG_STRLEN` — 131072 bytes, measured on this machine:

```
100000  ok
131000  ok
131072  OSError: [Errno 7] Argument list too long
240000  OSError: [Errno 7] Argument list too long
```

The brief prompt carries every retrieved article, so it approaches this. Today's
is ~55k and works — which is exactly what makes it dangerous. It was
data-dependent: the first day with a dozen long articles would have killed the
brief stage.

And it would have died badly. The `OSError` comes from the **exec**, before
`claude` starts, so `_run` never saw a process, `is_retryable` never classified
it, and the run reported `failed` rather than `succeeded_partial`.

The obvious fix — cap `user_content` — is the wrong one. It "fixes" the crash by
silently truncating article text, which is precisely the evidence the brief
exists to carry, and the loss would be invisible in the output. It also turns a
platform constant into a number someone has to keep tuning.

stdin has no such limit. Verified with the full production flag set
(`--json-schema`, `--output-format json`, `--model`):

```
192,052 bytes piped -> is_error: false, stop_reason: tool_use, result: {"answer":"OK"}
```

The executor already used this reasoning for the system prompt
(`--system-prompt-file`, "keeps argv small"). User content simply never got the
same treatment.

**Not a cache-key change.** `cache_key_for` is a function of the call arguments,
never of `argv`, so every fixture under `fixtures/llm/` stayed valid. A ceiling
still exists — the model's context window — but that returns a clean error
envelope the budget already handles, rather than an exec failure.

`_run` now also catches `OSError` on both the spawn and the communicate, for the
same reason: a failure that happens outside the process has to be translated
into `LlmError` or nothing downstream can reason about it.
