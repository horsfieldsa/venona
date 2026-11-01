---
status: accepted
date: 2026-08-24
---

# 2026-08-24 — The extension pairs by OAuth, and 0128 still holds

The browser extension's "Connect with Google" button drives the existing OAuth
2.1 flow (`api/routers/oauth.py`) with `scope=annotate`:
`chrome.identity.launchWebAuthFlow` opens
`/api/auth/login?next=<authorize path+query>` — `authorize` deliberately 401s
without a session, and `login` is how a session appears — the person signs in
through the hub, approves a reader-worded consent screen, and the code
exchanges into an ordinary `mcp_tokens` row. Verified against production
before any code changed: registration accepts the extension's
`https://<id>.chromiumapp.org/` redirect as an ordinary https URI (201), and
`_safe_next` passes a same-site path with its query intact.

**Why this does not reopen 0128.** 0128's property is that auth material which
grants is never reachable through an HTTP read or write primitive a page can
drive — its target was a Generate button on `/settings/agents`, minting on a
session alone. The token endpoint mints behind a different gate: a human on a
consent screen, plus PKCE possession, yielding a scoped (four paths), expiring
(30 days), user-owned credential. C12's sentence "over HTTP access can be
taken away and never handed out" is hereby qualified to what it always meant
in practice once the OAuth addendum landed: never handed out *without a human
consenting on the server's own page*. Root and mgmt material, and SPA mint
buttons, stay forbidden exactly as 0128 says.

**Registration is per pairing attempt, not a pre-registered client.** The
redirect URI derives from the runtime extension id, which differs between a
dev load and a store install and would change if the Web Store re-keys the
listing; a fresh RFC 7591 registration each attempt self-heals all of that and
survives a database reset. The rejected alternative — one pinned client row —
saves nothing (registration grants nothing; the session is the security
boundary) and adds an id to keep in sync. Accepted cost: `oauth_clients` rows
accumulate one per pairing.

**Consequence.** `extension/src/shared/pairing.ts` is the second client class
of the C12 OAuth addendum and is named in its touch list. The consent screen's
copy is per-scope (`_consent_copy`): operators approving `mcp` see paths and
the CLI revoke command; readers approving `annotate` see reader words and
expiry as the revocation story — `tests/test_copy_style.py` sweeps the
template. Re-pairing on 401 is the refresh story; there is still no refresh
token and no HTTP revoke, and a self-revoke endpoint remains a recorded
follow-up, not an accident waiting in this flow.
