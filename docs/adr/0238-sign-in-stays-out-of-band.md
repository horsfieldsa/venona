---
status: accepted
date: 2026-09-14
---

# 2026-09-14 — Sign-in stays out of band: no harness drives a login flow

`LoginDriver` is declared and **every driver returns `None`**: `claude_code`,
`codex` and `grok` alike (verified 2026-09-14). Connecting a model service is
something a person does at a shell, or by pasting a token into the admin page.

**The reasons differ per vendor and none of them is going away on its own.**
Claude Code's `setup-token` needs a browser on the same machine. Codex's and
Grok's `--device-auth` print nothing Venona has seen documented, so driving
either means scraping output that is not a contract.

**Scraping it would break on the vendor's next release, on the one page an
operator opens when nothing works.** That is the whole argument: the admin model
page is the surface somebody reaches for precisely when a service has stopped
answering, and a sign-in flow that half-works there is worse than one that
honestly says "do this at a shell". A pasted token is the whole of the
in-product path, and it is enough.

**Rejected: drive it over a pty and parse what comes back.** It is a contract
nobody offered, it would need a per-vendor parser, and every one of them would
fail silently in the direction of "we are signed in".

**Reversal trigger:** a vendor documenting its device-auth output as a stable
interface. Then that driver, and only that driver, grows a real `login()`.

**Consequence.** `llm/harness/__init__.py` declares the protocol and keeps the
load-bearing sentence; the three drivers' `login()` docstrings cite this record
instead of each re-arguing it from their own angle. `services/harness_service.py`
owns the pasted credential.
