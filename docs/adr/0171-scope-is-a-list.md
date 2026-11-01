---
status: accepted
date: 2026-08-27
---

# 2026-08-27 — `scope` is a list, and a token's reach is the union of what it holds

The authorization server read `scope` as a single key into `authz.SCOPES`. RFC
6749 §3.3 makes it a space-delimited LIST, and a client that reads
`scopes_supported` off our own discovery document asks for every name it finds
there. So the Claude Code SDK sent `scope=annotate mcp` and got:

```
invalid_scope: unknown scope 'annotate mcp'
```

The refusal was unreachable by any other route — every hand-written request in
the repo and in the tests named exactly one scope — and total for the clients it
hit. We published the list of scopes and then refused anyone who asked for it.

**Unknown names are dropped, not refused.** That is the RFC's other permitted
answer and the one that survives a client adding `offline_access` next year.
Refusing is reserved for a request where nothing at all is recognised. The
granted set goes back on the token response, normalised and sorted, so a client
is never left believing it holds something it does not.

**Reach is the union.** `authz.reach_for` splits the stored value and unions the
path sets, because a credential carrying two scopes reaches what either names.
An unrecognised value still yields the empty frozenset: admissible nowhere,
failing closed. The leash is unchanged — `AuthMiddleware` still checks the
REQUEST path against that set, so a route added next month stays unreachable
until somebody names it.

**No migration.** A single-scope row is already a valid one-element list, and
`"mcp"` still parses to exactly `MCP_REACH`. What changed is that `"annotate
mcp"` is now a value the column can hold, which is why `connectionReach` in the
web app splits before it looks a label up: an unsplit lookup renders the raw
wire value onto a reader's page.

**The consent screen describes everything it grants.** A two-scope request gets
a page naming both abilities. A screen that describes less than it grants is
worse than no screen, and the branch that would have leaked is the one where the
copy silently kept saying "assistant" while the token also carried the
extension's reach.
