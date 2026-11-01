---
status: accepted
date: 2026-09-08
---

# 2026-09-08 — Billing lives in the hub, and the instance receives an entitlement

Venona is about to take money. Stripe lives in `hub/`, and no instance ever
holds a Stripe credential, calls `api.stripe.com`, or stores a customer id.
What an instance gets is a projection: four columns on `users` saying whether
this reader is entitled, until when, and what to tell them about it.

**A subscriber is a person, and only the hub knows people.** The hub already
owns `users`, `memberships` and the request-access queue; it is the OIDC issuer
and the front door (ADR 0141, 0202). An instance's `users` table is downstream
of that — `users.role` is already a cached projection of `memberships.role`,
synced on every login and pushed through `POST /api/mgmt/provision-user`. A
Stripe customer belongs beside the membership it pays for, not beside the
corpus it reads. Putting billing in the instance would mean a second Stripe
integration for every domain we ever add, and a person reading two domains
would be two customers with two cards on file, which is a worse product and a
worse ledger.

**The hub is also the only side that can do this safely.** It has no
determinism linter (`lint_determinism.py`'s `GUARDED_DIRS` is
`pipeline/services/llm/graph` under `backend/venona`), so an outbound HTTP
client is ordinary there; it already hand-rolls two of them behind a
module-level transport seam (`hub/hub/google.py`, `hub/hub/mgmt_client.py`);
and it runs `numInstances: 1`, which the poller already depends on. The
instance, by contrast, is the place where an outbound client has to be argued
past `urlguard` and a determinism guard, for a URL that is operator-set and
never runtime-editable. That argument is winnable and it is also unnecessary.

**The entitlement travels on the channel that already exists.** Two ways in,
and neither is a new hole. At login the id_token gains `venona_entitlement` and
`venona_entitled_through` beside `venona_role`, read by
`api/oauth.py::identity_for_code`. On change, the hub calls
`POST /api/mgmt/provision-user` with `action="update"` and four optional extra
fields. **Not a new action and not a new route**: `authz.MGMT_REACH` is exactly
the four paths it was, so contract C12's leash does not grow, and
`schemas/mgmt.py`'s `action` is a pydantic *pattern* rather than a `Literal`,
which means an unknown action is a 422 from validation before any handler runs.
The hub and the instance deploy independently from the same commit
(the deployment config build filters), so an `action="entitle"` would have 422'd on
every membership for as long as the instance lagged. Extra fields are ignored by
an old instance (`extra='ignore'`) and honoured by a new one, and no deployment
order breaks.

**The instance still holds a local brake.** A projection can go stale — the hub
can be down, a push can fail, a sweep can miss — and the failure mode is
expensive: `personal_brief.warm_recent_readers` fans out one model call per
active reader per day, so an instance that believes a churned account is still
`trialing` keeps spending on it. So `recently_seen` compares `entitled_through`
against a `now` its caller passes in, as well as reading the pushed status. The
projection is a cache; the date is the fact.

**What this costs.** Two databases now disagree about a person for up to one
poll interval, and the reconciler that closes that gap is real work in
`hub/poller.py`. We take it because the alternative — Stripe credentials on
every instance — is worse in every direction that matters.
