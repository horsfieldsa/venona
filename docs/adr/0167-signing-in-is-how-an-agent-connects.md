---
status: accepted
date: 2026-08-27
supersedes: 0128 (the revoke half only; minting stands)
---

# 2026-08-27 — Signing in is how an agent connects, and disconnecting moves onto the page

The OAuth 2.1 authorization server built for the browser extension (0161) is now
the front door for MCP clients too. `/settings/agents` teaches the address and
nothing else: a client that gets a 401 from `/api/mcp` reads the
`resource_metadata` pointer, fetches the two documents at the site root,
registers itself, and sends a person to the consent screen. Nothing is pasted,
so no long-lived secret travels through a clipboard, and a reader who does not
run the instance can connect an assistant without asking the person who does.

`venona mcp token new` stays, demoted on screen and in the CLI's own output, for
the two cases sign-in cannot serve: an agent with no browser, and the day the
identity provider is down.

**What actually blocked this, and it was one thing.** `GET /api/oauth/authorize`
is deliberately outside `EXEMPT_PATHS` and the middleware answered a session-less
request with a JSON 401. The extension works around that by entering at
`/api/auth/login?next=<authorize path+query>`; an MCP client cannot, because it
opens `authorization_endpoint` verbatim out of the RFC 8414 document. So every
operator whose browser had no live cookie met a JSON body where a sign-in should
have been — on Claude Code, Claude Desktop and claude.ai alike. A session-less
**GET** on that exact path now answers 302 to the sign-in instead.

`EXEMPT_PATHS` was deliberately NOT widened to get this, and the distinction is
the whole point: the endpoint still requires a session to do anything, and only
the SHAPE of the refusal changed. `POST` keeps its 401, because that is the
request that mints a code, and exempting the path would have taken the
middleware off it. `tests/test_auth.py`'s sweep asserts the redirect target
rather than skipping the route.

**Revoking moves to the page; minting does not.** This is the half of 0128 that
no longer holds. C12's rule was never "no credential operations over HTTP" — it
is that **over HTTP, access can be taken away and never handed out**, and
revoking is taking away. 0128 drew the line at the terminal for both because
both were terminal-only, and once connecting became a browser flow the CLI
command asked people for a token id nobody had shown them. `DELETE
/api/meta/mcp/tokens/{id}` is the reader's own, ownership-checked because token
ids are printed and rendered and so are not secret; `DELETE
/api/admin/tokens/{id}` is the unscoped one, which has to exist for the same
reason the unscoped LIST does — a list that names a leaked credential beside a
revoke that refuses it leaves the hole where it was.

**Minting stays in a terminal, and 0128's argument for that is untouched.** An
HTTP mint would let a compromised credential manufacture a persistent one that
outlives rotation of the credential that made it. The page says so, and
`scripts/ui_smoke.py` still fails if somebody adds a Generate button.

**Each instance stays its own authorization server.** Discovery hands a client
that instance's own origin, so three instances are three connections. The hub
was rejected as a single MCP authorization server: it would make a hub-issued
JWT a second credential class verified against JWKS on the MCP hot path, break
the C12 addendum's "a code becomes an ordinary `mcp_tokens` row", put the hub on
the critical path of every MCP call, and leave Disconnect unable to end a live
token before it expires. Sign-in is still single: the second instance's
`authorize` lands on a live hub session, so the reader sees consent and Allow
and no account chooser.

**Consequence.** `api/middleware.py` grows `AUTHORIZE_PATH` and one branch;
`routers/oauth.py` accepts and validates RFC 8707 `resource` at both endpoints,
and its consent screen borrows nine values from `web/src/styles/tokens.css`
(pinned by `tests/test_oauth.py`) instead of eight inline hexes and
`color-scheme: dark`. `web/src/lib/agentCopy.ts` is the translation layer that
lets a reader tell an assistant from the browser extension, which only started
mattering with a Disconnect button beside each. Contract C12 and its OAuth
addendum carry all of it.
