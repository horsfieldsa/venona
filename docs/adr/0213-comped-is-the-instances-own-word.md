---
status: accepted
date: 2026-09-09
---

# 2026-09-09 — `comped` is the instance's own word, and the projection cannot overwrite it

Complimentary access is set on the instance, by `venona users comp`, and nothing
the hub pushes can take it away. Both writers of the entitlement projection
(`set_entitlement`, and the login fast path that now shares it) carry
`AND entitlement_status <> 'comped'`.

**The hub cannot say it.** ADR 0209 makes the hub the system of record for
billing and gives the instance a projection of it, and that is still right. But
the hub's vocabulary is Stripe's: `none`, `trialing`, `active`, `past_due`,
`lapsed`. A person the hub has never sold anything to is `none` there, and
`billing_state.for_seat` has no branch that could ever return anything else for
them. So `comped` is not a state the projection is capable of carrying, and a
projection that overwrites it can only ever overwrite it with `none`.

**Which it did, at the first sign-in.** Migration 0023 backfills every existing
reader to `comped` — deliberately, and its own comment says why: the hub and the
instance deploy independently from one commit, so there is a window where the
gate is live and nothing has been pushed, and defaulting to `none` in that window
locks out the entire readership. The login path then wrote the hub's word over
it. Every backfilled reader became `none` with a null `entitled_through` the next
time they signed in, which under `VENONA_BILLING_ENFORCED=1` is `entitled=False`
and an archive ceiling of `NOTHING` — an empty product and a bounce to the
paywall, for the readership the backfill existed to protect.

That migration's comment also promised that "the hub's first push corrects anyone
who should be paying". It cannot: the push has nothing to correct them WITH.

**So the two words are about different things, and both are needed.** The hub
answers *what have they bought*. The instance answers *do they pay at all*. An
operator, a design partner and a journalist are the second question, and it has
no Stripe object behind it to project.

**The alternative was teaching the hub `comped`** — a column on `memberships`, an
operator control to set it, and a sixth value in `for_seat`. Rejected because it
puts a non-billing decision inside the billing system, and because the thing
being granted is access to ONE instance's reading, which is the instance's own
business. The hub would be storing a fact it never uses.

## Consequences

- `venona users comp --email …` and `uncomp` are CLI-only, for the same reason
  `venona users role` is: this grants the paid product for nothing, so it needs
  the credential that already implies everything else. No HTTP route sets it.
- Withdrawing writes `none` rather than guessing at Stripe. The reader's next
  sign-in carries the hub's real answer, which is the same path that corrects
  everybody else.
- A comped reader who later subscribes stays comped. That is the operator's
  decision to unwind, and the alternative — a subscription silently cancelling a
  comp — is worse, because it is invisible.
- **Going live now includes reviewing who is comped.** `docs/RUNBOOK.md`'s
  checklist says so, before `VENONA_BILLING_ENFORCED=1` and not after. Everyone
  who predates migration 0023 is in that set.

## Reversal trigger

If a second instance ever needs the same person comped on both, and keeping the
two in step by hand becomes a real chore rather than a hypothetical one, that is
the argument for the hub-side column this rejects.
