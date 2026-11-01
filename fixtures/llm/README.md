# Recorded envelopes

Two jobs, and they fail differently, which is why the distinction is written
down rather than assumed.

**Replay.** `ReplayExecutor` looks a file up by `cache_key_for`, which hashes
the call arguments INCLUDING the schema. Change a schema and every fixture for
that task becomes unreachable — the file is still here and still correct, but
nothing will ever ask for it by that name again. A miss is a hard failure, never
a fall-through to a live call.

**Parsing.** `tests/test_llm_envelope.py` sweeps these by PATH, not by key, and
asserts every one still parses. That sweep is why a hand-trimmed or truncated
envelope cannot pass the suite, and it keeps working whatever the keys say.

## Currently unreachable by key

`analyze` (43), `discovery` (8), `questions` (2) and `setup_survey` (5) were
recorded against schemas that have since named every property in `required`, so
their keys have moved. They are still valid envelopes and the parse sweep still
reads them; nothing replays them today, so nothing fails.

They become replayable again with `make record` over a research run. Until then,
a test that starts replaying one of those tasks will report "no fixture ...
record it with: make record" while the files sit here — which is the confusion
this paragraph exists to prevent.

`discovery`'s keys were already stale before that change, for an unrelated
reason `tests/test_setup_replay.py` records: the prompt carries `Today's date`
only when not offline, so a live recording keys on a prompt an offline run never
assembles.

## Per service

A service records into its own subdirectory, named by `Harness.fixture_root`.
Claude Code's is the root itself, because its fixtures predate there being two.
Codex records into `_codex/`. Without that split, recording on one service
overwrites another's file at the same key and replay reads one vendor's stream
through the other's parser.

`_errors/` and `_stream/` are hand-written, not recorded, and are Claude Code's
shapes: `structured_output`, `permission_denials`, `modelUsage` and
`unifiedWindows` have no counterpart in another vendor's stream.
