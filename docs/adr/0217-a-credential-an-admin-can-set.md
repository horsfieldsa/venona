---
status: accepted
date: 2026-09-09
---

# 2026-09-09 — A credential an administrator can set, and the variable that beats it


Standing an instance up ended with a step nobody in the app could perform:
mint a token on a workstation with `claude setup-token`, paste it into an env
file, redeploy. `scripts/new_instance.py` printed it as step 3. An
administrator with a browser and no shell could not finish, and could not
rotate.

## Two kinds, stored differently

A **subscription** credential rotates -- the CLI refreshes its own token -- so
the CLI owns the file and Venona never holds a copy that can go stale. It lives
in an instance-scoped home the child is pointed at, and no route reads it back.
Per instance rather than shared, which also closes the contention
`docs/pm/PROGRAM.md` records: one token across several stacks competes on one
account's limits.

An **API key** does not rotate and cannot live in every vendor's own store, so
it is a row, following `notify_destinations` exactly: plaintext, never returned
by any route, shown only as a hint derived at read time. Plaintext for the
reason that migration already gives -- unlike a password nothing compares it,
and unlike a hash Venona has to replay it, so encrypting here moves the problem
to where the key lives, which is the environment, which is where this started.

**The home is a sibling of the credential, not its parent.** A coding agent
pointed at a directory can read what is in it.

## The thing that would have made this a no-op

Both vendors read an environment variable AHEAD of their own credential store,
and every instance that exists sets one: the deployment config and every generated env
file carry `CLAUDE_CODE_OAUTH_TOKEN`. Writing a credential and stopping there
would have produced a page that reports a connection, a probe that reports a
live session, and every call still using the deployment's key -- with nothing
anywhere to say so.

So `Credential` carries `suppress` as well as `env`, and the child environment
is **inherited, minus, plus**: minus the names this vendor reads on its own,
whenever something connected in the app supersedes them. Only then -- an
instance authenticating by environment variable keeps working, because
stripping unconditionally would disconnect the fleet in the name of preferring
a credential that does not exist.

`/admin/model` reports which of the two is in force, because the sign-in probe
cannot: it reports a session as live whichever credential answered it.

Inherited rather than an allowlist, and that is deliberate too.
`DISABLE_UPDATES` keeps a pinned binary pinned and `HOME` is where the rotating
refresh token lives; both arrive by inheritance, and a curated list drops one of
them the first time somebody adds a third.

## Verified before stored

A pasted credential is checked with the service's own probe, in a child
carrying only the candidate, before anything is written. A credential that does
not work is refused at the dialog -- the only place an administrator can do
anything about it -- rather than discovered by the next run as a stack of
envelope errors naming a 401.

## What this does not do

**No sign-in Venona drives, for Claude Code.** `claude setup-token` is
interactive and wants a browser on the same machine. Driving it over a pty and
scraping its output was considered and rejected: it is a contract nobody
offered, and it would break on the vendor's next release with the failure
landing on the one page an operator opens when nothing works. The honest answer
is a paste field that verifies, and a refusal that says so.
