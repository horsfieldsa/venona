---
status: accepted
superseded-in-part-by: 0167 (revoking only; minting stands)
date: 2026-08-17
---

# 2026-08-17 — An agent token is minted from a terminal, never from a page


`api/security.py` opens by explaining that auth material must be neither
attacker-writable nor operator-visible over HTTP, and `AccountView.vue` refuses
a Rotate button for exactly that reason. A Generate button on
`/settings/agents` is the same door.

The asymmetry — **list on the page, mint and revoke in a terminal** — is
deliberate and is stated on screen so it reads as a decision rather than as an
unfinished feature. (0167 moved the REVOKE half onto the page once connecting
became a browser flow; the mint half below is untouched and is still what
`scripts/ui_smoke.py` enforces.) Only a hash is stored, so the page could not show a token
even if someone added the code to try.

This also settles what "env-only" meant. The rule protects a property — *not
reachable through an HTTP read or write primitive* — and `os.environ` is one way
to get it, not the only one. `data/auth_epoch` has always satisfied it without
being an environment variable. C12 now states the property rather than the
shorthand: **auth material that grants is env-only; auth material that only
restricts, or that is the hash of a path-limited credential, may live in the
data directory. Over HTTP access can be taken away and never handed out.**

*On command names in the interface:* the 2026-08-07 entry banned naming a
command for an action **the app can already perform**. Three of its four cases
were that; the fourth was fixed with an endpoint. This is neither — minting over
HTTP is the thing `security.py` forbids, so the app cannot perform it and an
endpoint is not the fix. It is the same exemption `AccountView` already takes for
`venona auth token`. Written down here so nobody "finishes" the page with a
button.
