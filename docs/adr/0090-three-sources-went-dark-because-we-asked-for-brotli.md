---
status: accepted
date: 2026-08-07
---

# 2026-08-07 — Three sources went dark because we asked for Brotli


DefenseScoop, Defense One and War on the Rocks each earned three strikes and
auto-disabled, reporting:

    feed parsed but yielded no items (moved or reformatted?)

None of them had moved or reformatted anything. All three were serving valid RSS
at the configured URL the whole time.

**`Accept-Encoding: gzip, deflate, br`.** Commit `14fc87c` added a browser-shaped
request envelope to defeat .gov/.mil WAFs that match on the ABSENCE of ordinary
headers — a good change, with one line of collateral damage. `httpx[http2]`
ships no Brotli decoder, and httpx does not raise on a `Content-Encoding` it
cannot handle: it falls back to `IdentityDecoder` and hands the caller the raw
compressed bytes, with a 200 and no exception anywhere. Every origin whose CDN
prefers `br` when offered went dark; Breaking Defense (gzip) and DARPA
(identity) were untouched. The split maps exactly onto the healthy/broken split.

The header is now derived from `httpx._decoders.SUPPORTED_DECODERS`, so it
cannot drift from what is installed: adding the `brotli` extra makes `br` appear
on its own, and removing a decoder removes the offer. A literal here is a
promise about a dependency, made in a file that cannot see it.

**The message was the second failure, and it cost more than the first.**
`parse_feed` called `feedparser.parse` and threw away `parsed.bozo` and
`parsed.bozo_exception` — feedparser KNEW, and said `SAXParseException('not
well-formed (invalid token)')`. The adapter discarded that and substituted a
guess about the publisher. Three runs of investigation pointed at three
newsrooms for a bug in our HTTP client. `diagnose_empty` now separates the three
cases that need different actions: an undecodable body (fix the client),
malformed XML (fix the URL), and a clean parse with no entries (wait) — which is
the only one "moved or reformatted" was ever a fair guess for.

**`make record` would have made it permanent.** `_write_fixture` was gated on
`response.ok and response.text`, and mojibake is truthy — so recording would
have overwritten three good fixtures with undecodable bytes and turned a
live-only bug into a permanently green offline suite. It now refuses a body
carrying replacement characters.

**Auto-disable was a one-way door.** `active_specs` excludes a disabled source
from every run, so nothing but a success clears the counter and the source is
shut out of the run that would produce one. The only way back was
`venona sources --enable <id>` — printed in a banner in the browser, which is a
UI telling its reader to leave and know an incantation. There is a
`POST /sources/{id}/enable` and a button beside each disabled source now.
`clear_strikes` deliberately does not stamp `last_success_at`: nothing has
succeeded yet, and a fresh timestamp would hide a source that is still broken.
