---
status: accepted
date: 2026-08-30
---

# 2026-08-30 — A fetch reports what the origin said, and a retry never changes address family


For nine days the program tracker carried this, as a finding rather than a
guess: *"Web grounding from production cannot reach `.gov`/`.mil` hosts ... likely
those hosts refusing the host's shared egress ranges; a static outbound IP or an
egress proxy is the probable fix."*

The host reaches them fine. Measured over 533 recorded candidate fetches:
`www.army.mil` 12 attempts and **0** connection failures, `en.wikipedia.org` 79
and **0**, with `2009-2017.state.gov`, `www.nga.mil`, `www.act.nato.int` and
`www.dvidshub.net` all read successfully from production.

Three defects in series produced the error, and a fourth hid them.

**1. The origin answers 403.** Fetching the same URLs from a laptop on a
domestic line, with venona's own client, returns 403 from `acronymfinder.com`,
`allacronyms.com`, `apps.dtic.mil`, `gao.gov`, `marines.mil`, `esd.whs.mil`,
`congress.gov`, `jcs.mil`, `media.defense.gov` and `aaf.dau.edu`. Identical to
production. This is per-origin bot policy against an honest User-Agent, which
is what ADR 0043 said Tier 3 would run into. It is not IP reputation and no
egress change addresses it.

**2. A 403 earned a retry.** It was in `RETRY_STATUSES` on the theory that
*"some WAFs refuse the first request from an unseen client and pass the
second."* Across those 533 fetches no host that answered 403 ever passed on a
retry. So 403 leaves the set. A bot manager's 403 is a verdict; asking twice
more costs three requests and three per-host delays to be told the same thing,
and is a plausible route from a refusal to a dropped connection.

**3. The retry changed address family.** `answers[attempt % len(answers)]` over
a list `resolve_host` had deliberately sorted v4-first. That sort decides the
first attempt and nothing after it. These hosts answer with one A and two AAAA,
so both retries pinned IPv6, and the host has no v6 route. There is no
happy-eyeballs underneath to rescue it — pinning is the whole point of the SSRF
guard, and it collapses the candidate set to one address.

Two fixes, because either alone leaves the other latent: `getaddrinfo` gains
`AI_ADDRCONFIG` so a host with no v6 address is never offered AAAA at all, and
the rotation walks `routable_family(answers)` so it cannot cross into a family
the first attempt did not use. A v6-only host is still dialled over v6.

**4. And the error reported was the wrong one.** On a transport exception the
loop set `raw = None`, so the 403 it already held was discarded and
`last_error` was raised instead. Every one of these appeared in the trace as
`ConnectError: All connection attempts failed`.

That is the decision worth recording: **a status the origin returned outranks a
transport failure met afterwards.** One says what was decided about the request;
the other only says we stopped being able to ask. A fetch that never got an
answer still raises, so nothing is swallowed.

## Why it stayed hidden

The evidence was sitting in the trace. The same host appears under **both**
reasons in equal counts — `esd.whs.mil` 8 and 8, `media.defense.gov` 4 and 4,
`congress.gov` 2 and 2 — which is what a coupled failure looks like and what a
network-level block does not. And Wikipedia, whose attempt 1 also pins IPv6,
never fails: it answers 200 on attempt 0, so the retry never runs. The IPv6 bug
is reachable only through a retry, and a retry happens only after a refusal.

Two things made it invisible for as long as it was:

- **a self-hosted deployment disables IPv6 at the sysctl level** for self-hosted
  deployments, with the comment *"Nothing venona fetches is v6-only."* That is
  true and it masked the bug everywhere except in production, which does not use that
  file.
- **The rotation test used two IPv4 addresses.**
  `test_retries_rotate_through_the_vetted_answers` covers
  `answers[attempt % len(answers)]` exactly, and passes whether or not the
  rotation is family-safe. A test can pin a line and still not pin the property
  the line exists for. There is now a mixed-family test, and it names this.

## The general lesson, which is the reason this is an ADR and not a commit

**The reported cause of a failure is a design surface, and this one was wrong
in the direction of costing money.** The trace said "cannot reach", so the
tracker proposed a static outbound IP and an egress proxy — infrastructure, for
a bug in a retry loop. The check that would have settled it on day one was
fetching one of those URLs from the laptop, which takes a minute and was never
done, because the error message had already answered the question.

The epic's own second hypothesis said *"or the resolve-and-pin client picks an
address family those hosts reject from that network"*. It was right. It was
listed second and never tested, behind the one the error message suggested.
