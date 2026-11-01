---
status: accepted
date: 2026-08-18
---

# 2026-08-18 — The hub pulls, through a `vmgmt_`-scoped surface

Direction: **hub → instance HTTPS**, polled. The instance needs zero knowledge
of the hub beyond its OIDC env vars, no outbound scheduler, and no credential
that can reach the hub; a poll loop gives `last_seen`/status for free.

Credential: a new env-only token `VENONA_MGMT_TOKEN` (`vmgmt_` prefix,
constant-time compare, no DB row — revocation is rotate-and-redeploy, accepted
at this scale). `principal_for` routes on the prefix, `verify_bearer` refuses
it the way it refuses `vmcp_`, and its reach is a frozen route set in
`api/authz.py`:

    /api/mgmt/status          /api/mgmt/provision-user
    /api/mgmt/domain          /api/mgmt/setup/start

That leash is the point: the hub's credential can never touch `/api/config/*`,
runs, tokens or exports without someone naming the route — the
`EXEMPT_PATHS`-run-backwards property. "Config push" is scoped to exactly two
things: the domain descriptor and user provisioning (whose suspend action bumps
`session_epoch`, closing the 14-day cookie gap after a hub membership
removal). What the hub can never do is written into CONTRACTS.md.
