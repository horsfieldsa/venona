---
status: accepted
date: 2026-08-27
---

# 2026-08-27 — The account page stops naming the root credential

`/settings/account` carried a fourth section, headed **api token**, that told
whoever opened it where the root credential lives (`VENONA_AUTH_API_TOKEN`),
what regenerates it (`venona auth token`), that replacing it means editing the
environment and restarting, and that it "reaches everything in Venona, including
the actions that spend and the one that deletes". It is gone. Nothing behind it
moved.

Three reasons, and the third is the one that decides it.

**It is break-glass, and the break-glass path is not a feature.** The root token
is how an operator gets back in when Postgres is unreachable or the hub is down
(`RUNBOOK.md`, `middleware.py`'s note that `verify_bearer` resolves it without
touching Postgres). 0128 and 0161 already moved everything that *is* a feature
onto scoped credentials: the extension pairs by OAuth for an `annotate` token,
and an assistant takes one from `venona mcp token new`. What was left on the
page was an escape hatch advertised to people who will never pull it.

**It was written in operator vocabulary on a reader's surface.** An environment
variable name, the word ROOT, and "update the environment and restart" are this
repo's words, not a reader's — and admins are readers here too.

**`/settings` is the reader's own account.** 0134 settled that split: what is
the reader's lives under `/settings`, what is the instance's lives under
`/admin`. A variable set on the host is the instance's, and there is no third
category. Once the section could neither show the credential nor change it —
`api/security.py` forbids both, which is the same argument 0128 makes against a
Generate button — the only thing it could do was describe a host's configuration
to somebody who is not administering the host.

*What did not change.* `AuthConfig.api_token`, `token_login_enabled`,
`assert_usable`'s four startup refusals, `verify_bearer`'s prefix refusal,
`POST /api/auth/token-login`, `GET /api/auth/mode`'s `token` field, and
`venona auth token` are all untouched. So is the token form on `LoginView`:
`token_login_enabled` is `bool(api_token) and (token_login or not
google_configured)`, so on an OIDC deployment it renders only under
`VENONA_TOKEN_LOGIN=1`, and on a token-only bootstrap instance it is the only
way in. Removing that would lock those instances out.

*On a public API.* If one ships, it gets its own surface with its own
credentials. It is not this one, and the absence of this section is not a
statement about it.

*On 0128.* Its closing paragraph cites `AccountView`'s `venona auth token`
section as the precedent for naming a command in the interface. The precedent
holds — `AgentsView` still names `venona mcp token new` on screen, for the
reason 0128 gives — but the example it points at no longer exists. 0128 is a
dated record and is left as written; this is the record that says where it went.
