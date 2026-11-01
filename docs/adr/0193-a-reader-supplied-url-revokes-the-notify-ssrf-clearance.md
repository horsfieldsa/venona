---
status: accepted
date: 2026-09-01
---

# 2026-09-01 — A reader-supplied webhook URL revokes notify.py's SSRF clearance, so delivery goes through HttpClient

`docs/security/REVIEW-2026-08.md:350` filed one finding under "Checked, holds":

> A second outbound client (notify.py, oauth token exchange) bypasses urlguard
> and is attacker-reachable. — notify.py fetches `VENONA_NOTIFY_URL` and
> oauth.py posts to the configured OIDC token endpoint, **both are operator-set
> (env var / auth config), not runtime-editable through any API and not derived
> from attacker input or a discovery document.** `config_service` governs
> editable config FILES, not process env. No external attacker can steer either
> URL, so neither is an SSRF vector; both correctly set `trust_env=False` and do
> not follow redirects.

That verdict is entirely load-bearing on one clause, and ADR 0191 breaks it.
Destinations are rows now, created over HTTP, and a reader types the URL into a
form. "Operator-set", "not runtime-editable through any API" and "not derived
from attacker input" are all false of a destination. The clearance does not
transfer, and nothing about `notify.py` changed to earn a new one.

Reading it as still applying, because the module is the same module, is the
shape of mistake this record exists to prevent: a security finding is closed
against a set of facts, and when one of those facts changes the finding reopens
whether or not anybody edits the code it was about.

## Decisions

**Every delivery POST goes through `sources/http.py::HttpClient`.** The control
that replaces the clearance is the one that class already implements for feeds
and for model-nominated grounding URLs: resolve the hostname, vet EVERY answer
through `urlguard.check_resolved`, then dial the address that was vetted.

The pin is the part that matters and is the part a second implementation would
lose. `urlguard.py`'s own docstring says why: these URLs point at domains
somebody OWNS, an owner controls the DNS record, and a one-second TTL is three
lines of config -- so a guard that resolves once and then lets the transport
resolve again hands the owner a rebinding window. Copying fifteen lines of pin
logic into `notify/` is how the two drift apart quietly, which is why
`post_once` widened `_get_once`'s hardcoded method instead.

**`check_static` runs at save time, in the route.** Scheme, userinfo, port, IP
literals, and no DNS -- exactly what `PUT /api/config/sources` does to a feed
URL, and for the same two reasons. A refusal arrives as a sentence in the form
rather than as a delivery that quietly never happens hours later, and a lookup
in a save path would make the form depend on the network and the offline suite
non-hermetic.

**HTTPS only**, which is stricter than a feed. A notice body carries brief text
and sometimes a stranger's words; plaintext to a webhook endpoint is not a
choice anybody makes deliberately.

**No redirects at all.** `get` follows them by hand and re-vets each hop because
a feed genuinely moves. A webhook does not: an endpoint answering 302 is either
misconfigured or steering us, and neither earns a second request.

**No retry inside the request either.** Retrying there would send the same
message twice to a receiver that answered slowly, which for a person's inbox is
worse than a late message. The outbox retries, and it knows not to duplicate the
row.

**A `BlockedUrl` does not count toward a destination's strike total**, the same
carve-out `pipeline/fetch.py` makes for feeds: it is a configuration mistake,
not the endpoint failing.

## The second half: what a channel may put in a message

The same reasoning applies one layer up, and it was found the same way. The
access-request notice splices up to 2000 characters of stranger-authored text --
`coverage`, from a public unauthenticated form -- into its body. That was safe,
and safe **by accident**: the destination was a plain-text POST, and plain text
has no markup to escape into.

Slack blocks and an HTML email body have plenty. So:

- **Untrusted text lives in its own field.** `Notice.quoted` holds what venona
  did not write. Keeping it separate is what makes "escape it" a rule a reader
  of `render.py` can check, rather than something each renderer has to remember
  to look for inside a sentence.
- **Every Slack block is `plain_text`, venona's own included.** `mrkdwn` would
  turn `<https://evil|click me>` inside a coverage note into a link, in a
  message the reader trusts because venona sent it. Marking one field plain and
  leaving another rich is a distinction that survives exactly one refactor.
- **`untrust.scrub` before `html.escape`, in that order.** They are different
  jobs: escaping stops the text being structural, and the scrub drops every
  Unicode format character so injected text cannot be INVISIBLE to the person
  reading it.
- **`untrust.quote` is deliberately not used.** Its `<untrusted>` envelope is a
  boundary marker for a model. On a surface a person reads it is both
  meaningless and this repo's vocabulary.
- **`render.py` reads named fields and writes lines**, the way
  `api/mcp/render.py` does, with no `model_dump()`, no `dict(...)` and no `**`.
  A field added to `Notice` next month for one channel cannot leak into
  another's output by default.

## The offline half

`raise_notice` returns before it ENQUEUES when `VENONA_OFFLINE=1`, not merely
before it sends: a row is output, and a replayed run that queued mail is neither
offline nor reproducible. `ReplayHttpClient.post_once` raises rather than
returning a fake success, so if that guard is ever moved the failure is loud.
Marking a delivery as sent when nothing was sent is the one lie an outbox must
never tell.

## Follow-up

`docs/security/REVIEW-2026-08.md:350` should be annotated rather than left
reading as a live clearance. The finding is not wrong about `oauth.py`, whose
URL remains auth config; it is now wrong about the delivery path, which no
longer bypasses `urlguard` at all.
