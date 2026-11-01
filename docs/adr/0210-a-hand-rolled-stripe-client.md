---
status: accepted
date: 2026-09-08
---

# 2026-09-08 — A hand-rolled Stripe client, not the SDK

`hub/hub/stripe.py` is about two hundred lines of `httpx` against five REST
endpoints. The `stripe` package is not a dependency and is recorded in
`docs/pm/NOT-DOING.md`.

**The precedent is written and it was measured.** `mcp-types` rather than the
`mcp` SDK, because the types add one package and the SDK adds sixteen, three of
which reverse decisions already made in this repo. The same test applied here:
we need `POST /v1/customers`, `POST /v1/checkout/sessions`,
`POST /v1/billing_portal/sessions`, `GET /v1/subscriptions/{id}` and
`GET /v1/customers/{id}`. Five form-encoded calls with an `Idempotency-Key`
header. That is not a library-shaped problem.

**Webhook verification is stdlib.** Stripe signs `f"{timestamp}.{raw_body}"`
with HMAC-SHA256 and sends it as `Stripe-Signature`. `hmac` and
`hmac.compare_digest` are what the SDK calls too. `cryptography` is sanctioned
for the hub and refused for the instance (ADR 0141), and this needs neither.

**The SDK brings a transport nothing here can see.** `scripts/lint_determinism.py`
matches on source text — `httpx.AsyncClient(...)`, `requests.*` — so a library
that constructs its own client is invisible to it. That is the exact failure the
`mcp` SDK note calls out. It matters less on the hub, which the linter does not
guard at all, and it would matter a great deal the first time somebody moved a
helper across the boundary.

**The offline suite is hermetic by construction, not by configuration.**
`ReplayHttpClient` raises `FileNotFoundError` on a missing recording and
`RuntimeError` on any POST. There is no global "no network" switch to lean on:
the suite is safe because nothing in it can construct a live client. A vendor
SDK with its own transport would be the first thing that could, in a suite that
advertises itself as offline and free.

**So the seam is the same one used twice already.** A module-level
`STRIPE_TRANSPORT: httpx.AsyncBaseTransport | None = None`, exactly as
`google.py::EXCHANGE_TRANSPORT` and `mgmt_client.py::MGMT_TRANSPORT`, and
`httpx.MockTransport` in the tests. One pattern for every outbound call the hub
makes.

**What would reverse this.** Needing Stripe Connect, or metered billing with
usage records, or more than roughly ten endpoints. At that point the surface is
library-shaped and the argument flips; write the ADR that says so and take the
sixteen packages knowingly.
