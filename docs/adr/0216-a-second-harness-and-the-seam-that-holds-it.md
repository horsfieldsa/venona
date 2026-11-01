---
status: accepted
date: 2026-09-09
supersedes: 0001
---

# 2026-09-09 — A second model service, and the seam that holds it


ADR 0001 decided that model work runs on the `claude` CLI rather than the API,
and drew a consequence from it: "there is deliberately no `ANTHROPIC_API_KEY`
anywhere."

**The finding stands. The consequence does not.** `claude -p --output-format
json --json-schema` still returns a validated `structured_output`, so the CLI
still loses nothing against the API, and every reason to prefer a subscription
is unchanged. What has changed is that a CLI is no longer a Claude-only
arrangement: `codex exec` offers the same shape, and an instance should be able
to say which one it uses without a deploy.

## What is a fact about a vendor, and what is not

The split is the whole design. `executor.py` keeps everything that is true of
any agentic CLI -- one semaphore, one circuit breaker, the outage wait, the
pumps, reaping, the health cache, fixture recording -- and `llm/harness/<name>.py`
owns only what one vendor does and another does not.

Five of the seven driver methods exist because the corresponding logic was
Claude-shaped while wearing a general name, and each was found by reading rather
than by a failure:

| Looked general | Is a fact about one vendor |
|---|---|
| `budget.is_retryable` | Matches substrings like `500` and `429` against error TEXT. Safe where errors are short and structured; a coin flip where they carry a request id and a trace header. |
| `event.get("type") != "result"` | Names one vendor's envelope. Another types nothing `result`, so the whole final message would be persisted as transcript. |
| `version.split()[0]` | One CLI prints `2.1.251 (Claude Code)`; another prints its own name first. |
| effort levels | Per-model in one vendor's catalog, not universal. |
| the response schema | One validates full JSON Schema; another may reject keywords it cannot express. |

## `binary` is a name, and that is what makes the selector editable

`config_service.FROZEN_FIELDS` refuses `llm.cli_path` over HTTP because
`executor.py` execs it, and a session cookie should not equal a shell.
`llm.harness` is editable anyway, and the difference is not a judgement call: it
is an enum that resolves through a table in code to a program NAME. Nothing an
administrator can type reaches argv. A selector that carried a path could not
have been editable on any argument, which is why the table exists rather than a
settings key holding a program name.

`tests/test_harness_table.py` asserts that no `binary` contains a path
separator, so the property that licenses the editability is checked rather than
remembered.

## What a driver may do to a schema

**Subtract, and nothing else.** A driver may drop a keyword its provider
rejects; it may not add, rename or reshape. The cache key is taken from the
schema the CALLER wrote, so what a driver cannot express never rekeys a fixture
and never changes what the key means.

The shared schemas were made strict-clean where it was free: seven optional
properties across five schemas became required-and-nullable. Every consumer
already read them with `.get()` or `or ""`, so `null` and *omitted* are
identical downstream and no Python changed. Range constraints stayed, because
nothing in Python clamps and giving up enforcement for the service that has it
would be a pure loss.

## Consequences

- An API key is no longer forbidden; it is a **plan**, recorded in
  `llm.billing_mode` and refused when it contradicts the environment.
- The guard that enforces that moved from startup to `build_executor`. A boot
  check cannot hold a setting that is editable over HTTP: start in API mode,
  switch to subscription, and every call runs metered with nothing left to
  notice. `main.py` keeps an unconditional environment check for the case a
  boot check *can* catch, which is a deploy that shipped a key nobody meant.
- The guard reads `Harness.secret_env` rather than a literal, so the other
  vendor's key is covered by the same sentence. `OPENAI_API_KEY` is at least as
  conventional and does identical damage.
