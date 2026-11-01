# Stream transcripts

The `_errors/` fixtures are single envelopes. These are whole `--output-format
stream-json` transcripts, because some of what the CLI tells us arrives on lines
*before* the result line and is invisible to a one-object fixture.

| File | Exercises |
|---|---|
| `rate_limit_event.jsonl` | A real `rate_limit_event` (captured 2026-08-30 from claude 2.1.251) spliced ahead of a real recorded `analyze` envelope. Proves a reading is extracted from a transcript that ALSO parses as a success, which is the ordinary case in production and the one a hand-written fixture would get wrong. |

The event is recorded verbatim, including `utilization` as a 0-to-1 fraction and
`resetsAt` as unix epoch seconds. Both are the CLI's spelling, not ours, and the
conversion to a percentage and to ISO-8601 is what the parser is being tested on.
Do not "tidy" either into our own units here — that would make the fixture agree
with the parser by construction and test nothing.
