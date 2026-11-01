---
status: accepted
date: 2026-08-27
supersedes: 0141 (the role claim's authority to RAISE; everything else stands)
---

# 2026-08-27 — The hub may not mint an administrator; the ceiling moves into `change_authority`

C12 has said this since authority moved off the CLI:

> Over HTTP, authority may be REDUCED without limit, and EXTENDED only as far as
> `reader`. Crossing into `admin` requires shell access.

It was true of one door. `api/schemas/admin.py` carried `DemotableRole =
Literal["reader"]` on the admin router's request model, and nothing else enforced
it — so `user_service.change_authority` accepted `role: str | None` and
`user_service.create` accepted any role, from any caller.

The hub is a caller. `memberships.role` is `CHECK (role IN ('admin','reader'))`,
`hub/schemas/admin.py` accepts `^(admin|reader)$` on `POST /admin/grants`, the
claim rides `venona_role` into the id_token, and `routers/auth.py` provisions on
first login and syncs on every later one. So:

    POST hub /admin/grants {"role":"admin"}   ← one shared static bearer,
                                                no per-operator identity, no audit
      → memberships.role = 'admin'
      → id_token venona_role = "admin"
      → routers/auth.py create() / change_authority()
      → a venona admin: model spend, every config/* read including the
        relevance profile, and POST /api/data/purge

**The decision: nothing reached over a wire may grant `admin`.** The ceiling
becomes `allow_admin: bool = False` on both `create` and `change_authority`, and
only `cli.py` passes it.

## Why the fix is in the service and not in the router

`change_authority`'s own docstring already said where this belongs:

> Every rule that makes an HTTP authority surface safe lives here rather than in
> the router, because `venona users` reaches the same rules from another process
> and a check in a route is a check the CLI does not run.

The reader ceiling was the one rule that did not live there, and it failed in
precisely the way that paragraph predicts — it guarded the admin router and left
the login sync and `provision-user` free. Putting it back in the router would
have fixed the hub's two doors and left the next caller open.

`create` gets it too, and that is not symmetry for its own sake: a FIRST hub
login provisions through `create`, never through `change_authority`, so capping
one alone would have been a cap on nothing.

**Default closed.** A caller added next month is capped unless somebody types
`allow_admin=True`, which is the same ratchet `authz.required_role` uses when it
answers `admin` for a route nobody classified.

## What is NOT changed

- **Reduction is untouched.** 0141's "a directory that lies is worse than no
  directory" is right, and the hub can still demote and still suspend. The
  ceiling is one-directional, exactly as C12 words it.
- **Local suspension still wins**, and the last-active-admin rule still applies.
- **A refused elevation does not refuse the login.** The person signs in and
  keeps the role they had; the disagreement goes to the log. Refusing the sign-in
  would punish a reader for their directory being wrong.
- **`ensure_from_env` keeps `allow_admin=True`.** `VENONA_AUTH_ALLOWED_EMAILS` is
  read from the process environment, set by whoever deploys the instance — the
  same authority the CLI has, and not a wire. Capping it would be a self-inflicted
  outage rather than a control: it runs in the lifespan and is how an instance
  gets its first admin at all.
- **The hub's `/admin` bearer is still one shared static token** with no
  per-operator identity and no audit. This decision makes that credential unable
  to mint instance administrators; it does not improve the credential. That
  remains open (epic T, T-7′ option (c)).

## Rejected: verifying the id_token's signature

The first shape of this finding was "the hub's id_token is unsigned, so verify
RS256 against its JWKS". It was investigated and dropped, and the reasoning is
recorded because it sounds right:

1. 0141 decided it explicitly — *"instances need no JWKS fetch and no
   discovery"*.
2. It would not work. A JWKS fetched from `{hub_issuer}/oauth/jwks` arrives over
   the same TLS channel as the token, so an attacker who can forge one can serve
   the other; and a compromised hub signs genuinely valid tokens, because it
   holds the key.
3. It would remove a guard. `api/oauth.py` earns its provenance carve-out by
   enforcing `trust_env=False` **and** a constant token endpoint with no
   `.well-known`, so that no remote document chooses where the client secret
   goes. Fetching a JWKS document is that second condition gone.

Signature verification defends against an attacker who can forge a token without
holding the key or the channel. That is not the attacker here. Capping the
authority the claim can carry is, and it holds even against a fully compromised
hub.

## Consequence

- `services/user_service.py` — `allow_admin` on `create` and `change_authority`;
  the fifth refusal, ahead of the lock because it is about what the caller may
  ask for rather than the state of the row.
- `cli.py` — the only caller that passes it, at both `users add` and `users role`.
- `api/routers/mgmt.py` — `provision-user` returns `status: "refused"` with the
  reason rather than letting `AuthorityError` reach the error middleware as a 500.
- `api/routers/auth.py` — unchanged. Its existing degrade-and-log path already
  does the right thing with the new refusal.
- `docs/CONTRACTS.md` C12 — the ceiling's enforcement point moves from a router
  request model to the service.
- `tests/test_authority.py` holds the rule in both directions and pins that the
  CLI keeps shell authority; `tests/test_membership_gate.py` pins that a hub
  claim of `admin` does not raise authority while a claim of `reader` still
  reduces it. That second file previously asserted the OPPOSITE, which is the
  clearest statement of what changed.
