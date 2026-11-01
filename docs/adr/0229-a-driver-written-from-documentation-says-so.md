---
status: accepted
date: 2026-09-12
---

# 2026-09-12 — A driver written from documentation says so

Epic X ended with a claim: *"Adding a third is a module, a table row, an install
line and a fixture directory."* Grok Build is the first test of it, and the
claim held — `grok.py`, one line in `_table()`, two settings keys and a config
migration. Nothing else moved. The thirty-one parametrised tests in
`test_harness_table.py` and `test_transcript_per_service.py` picked the new row
up on their own and found three gaps before any of it ran.

What this record is actually about is the part epic X did not anticipate: this
driver was written **without a binary to run it against**, and that is a
different kind of artifact from the two beside it.

## The two states a driver can be in

`claude_code.py` and `codex.py` encode measurements. Their docstrings name the
run that established each behaviour: `--permission-mode dontAsk` denying
WebSearch while exiting 0, `stop_reason` reading `tool_use` on success, the
phantom haiku entry in `modelUsage`, the mini models a ChatGPT account cannot
call. Every one of those cost a real failure to learn.

This one encodes `xai-org/grok-build`'s headless reference. That is a weaker
thing, and pretending otherwise is how a reference's gaps become production
defects nobody can trace.

**So the driver marks its own uncertainty.** `ASSUMED` appears on
`wants_confinement`, `home_env` and `home_dirname`; the module docstring lists
what the reference establishes and separates it from what was inferred; and
`test_grok_envelope.py` opens by saying its fixtures are hand-written and what
that means it cannot pin. The epic carries the checklist that converts each one.

The alternative — write it confidently and fix it when it breaks — puts the
discovery in a production run, on the stage that had to degrade, at whatever
hour the daily fires.

## Where the uncertainty was resolved defensively

Three places, each choosing the failure that reports itself:

**`wants_confinement: True`.** This service edits files and runs shell commands,
which is the Codex posture. It may be relaxable — `--tools` is an allowlist, the
same mechanism that makes `--tools ""` enough for Claude Code — but proving that
needs a binary. Being wrong in this direction is mild and self-reporting: the
confinement builder finds no home to grant, returns empty, and `/admin/model`
says the call ran unconfined. Being wrong in the other direction is a model that
can read the credential (ADR 0219).

**`capabilities` declares search and not fetch.** `--disable-web-search` proves
search exists. `WebFetch` appears among the permission-rule prefixes, which is
evidence of a tool rather than proof of one reachable here. Declaring a
capability the service lacks is worse than declaring one short: the grounding
stage would stop degrading on a gap it still has, and record nothing about why
its answer was thinner.

**`grok_pinned_version` ships empty.** The other two pins name a version somebody
ran. A number here would be a claim, and `/admin/model` compares the installed
version against it — so a guess reports every correct install as wrong.

**And the tiers differ by effort, not by model.** `grok-4.6` is the only model
name the reference uses. `codex.py` records what guessing costs: its cheap tier
pointed at a mini model a subscription cannot call, so every `fast` call failed
against a model that never existed, until the breaker gave up on a service that
was working. One model with three reasoning efforts is a real cost lever and a
weaker one than three models; splitting them is the first thing to revisit.

## The one that changed the argv

The reference states that the Messages-format output *"omits the schema's
`permission_denials` field because Grok does not collect that data"*.

CLAUDE.md's prohibition is explicit about why that matters:

> **Never treat a call with non-empty `permission_denials` as a result.**
> Verified: `--permission-mode dontAsk` denies WebSearch/WebFetch while exiting 0
> with `is_error: false`. Silently returns "found nothing", forever.

On Claude Code that failure is detectable and the driver checks for it. Here it
would be **undetectable** — a denied search and a search that found nothing are
the same bytes.

So the answer is to make a denial impossible rather than detectable. The driver
never passes a `--deny` rule, never relies on a permission prompt, and passes
`--yolo` so a gated call cannot sit waiting at a terminal that does not exist.
Search is granted for the whole call or turned off at `--disable-web-search`;
there is no middle state to misread. What bounds the call instead is the
`--tools` allowlist, the environment allowlist in `child_env`, and the
confinement — three controls that fail loudly rather than one that fails silent.

`test_grok_envelope.py` asserts the absence of `--deny` and `--permission-mode`
in the argv, so this cannot be undone by someone adding a reasonable-looking
permission rule later.

## Consequences

- The service is selectable at `/admin/model` and **untested against a real
  account**. That is the honest state, and the epic says so rather than the
  admin page implying otherwise.
- Config migration 0007 adds both keys additively. `llm.harness` is untouched,
  so no instance changes service because of this.
- `cost_for` returns None deliberately: this vendor puts `total_cost_usd` on the
  `end` event, so `parse` records the provider's own figure as `measured` and a
  price table here would be a second, worse estimate competing with it. That is
  the opposite of Codex, which reports tokens and never a price.
- `reports_usage_windows: False`. The headless reference exposes no rate-limit
  reading, and the strip must not promise a window that will never fill in.

*Extends [0216](0216-a-second-harness-and-the-seam-that-holds-it.md); inherits
the confinement argument from [0219](0219-a-model-that-runs-code-gets-a-filesystem-of-its-own.md).*
