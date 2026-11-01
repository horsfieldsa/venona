"""Who may reach what. Contract C12's authorization half.

WHY THIS IS A TABLE AND NOT `Depends(require_role(...))`. `api/middleware.py`
enforces AUTHENTICATION pre-routing, by prefix, and rejects the dependency form
twice for a reason that applies with more force here:

    a route added without it is a hole that looks exactly like a route
    added with it.

A missing `Depends` is invisible in review. A route missing from the table below
is not: it defaults to `admin` at runtime, and `tests/test_authz.py` fails until
somebody classifies it. Both halves matter — the default keeps a forgotten route
safe, the test keeps the table honest.

TWO ROLES, and no third one for agents. An MCP token belongs to a user and
inherits that user's role, narrowed by a `reach` scope. An admin's agent token is
still read-only, because C12's read-only-ness is structural rather than a
permission: `make lint-readonly`, the symbol-level import allowlist in
`tests/test_mcp_readonly.py`, and the absence of any write tool.

⚠️ CLASSIFY BY ROUTE, NEVER BY HTTP VERB. `POST /api/graph/query`,
`/api/graph/overview`, `/api/graph/brief` and `/api/entities/cards:batch` are
READS that use POST for body size. A rule like "GET is reader, POST is admin"
breaks the graph explorer and every hover card for every reader, which is the
single biggest trap in this file.
"""

from __future__ import annotations

import os
import re
from typing import Any, Final

ADMIN: Final = "admin"
READER: Final = "reader"

#: The roles a `users.role` value may hold. Checked at request time rather than
#: as a CHECK constraint, for the reason `access_requests.role` is not one: the
#: set is a product decision that will change. A row holding anything else is
#: treated as the least privilege available, never as the most.
ROLES: Final = frozenset({ADMIN, READER})


# ── reach scopes ─────────────────────────────────────────────────────
#
# `Principal.reach` is a frozenset of PATHS a credential is admissible on,
# checked against the request path rather than a route's self-declaration --
# which is what makes a route added next month unreachable by an agent unless
# somebody names it. That is `EXEMPT_PATHS`' guarantee run backwards.

#: The MCP endpoint, and nothing else. `GET /api/meta/mcp` is deliberately
#: OUTSIDE it so an agent token cannot enumerate its siblings.
MCP_REACH: Final = frozenset({"/api/mcp"})

#: What the browser extension needs, and nothing more. The alternative is the
#: ROOT bearer token, defensible for one operator and not for N readers:
#: `chrome.storage.local` holds whatever the extension carries, and a token that
#: leaks should be able to annotate text, not purge a corpus.
#:
#: `/api/annotate` writes nothing and keeps no record of what anybody read; the
#: other two are reads, and `health/live` is how the extension decides whether
#: the instance is reachable at all.
ANNOTATE_REACH: Final = frozenset(
    {
        "/api/annotate",
        "/api/entities/cards:batch",
        "/api/sources/sites",
        "/api/health/live",
    }
)

#: The hub's management credential (docs/adr/0142), on the same leash as the
#: agent scopes above: named paths, checked against the request path.
MGMT_REACH: Final = frozenset(
    {
        "/api/mgmt/status",
        "/api/mgmt/provision-user",
        "/api/mgmt/domain",
        "/api/mgmt/settings",
        "/api/mgmt/setup/start",
    }
)

SCOPES: Final = {"mcp": MCP_REACH, "annotate": ANNOTATE_REACH}


def grantable(requested: str) -> tuple[str, ...]:
    """The scopes this server will grant for an OAuth `scope` request.

    `scope` IS A SPACE-DELIMITED LIST (RFC 6749 §3.3), not one value, and a
    client that reads `scopes_supported` off the discovery document asks for
    every name it finds there. Treating the field as a single key refused
    `"annotate mcp"` as an unknown scope and failed the whole connection, which
    is how the Claude Code SDK asks.

    Names this server does not recognise are DROPPED rather than refused, which
    is the RFC's other permitted answer and the one that survives a client
    adding `offline_access` next year. Refusing belongs to the caller, and only
    when nothing at all is recognised. The granted set is returned to the client
    on the token response, so nobody is left believing they hold more than they
    do.
    """
    return tuple(name for name in sorted(set(requested.split())) if name in SCOPES)


def reach_for(granted: str) -> frozenset[str]:
    """The paths a credential holding `granted` is admissible on.

    The union, because a token carrying two scopes reaches what either names.
    An unrecognised value yields the EMPTY set -- admissible nowhere, which
    fails closed.
    """
    reaches = [SCOPES[name] for name in granted.split() if name in SCOPES]
    return frozenset().union(*reaches) if reaches else frozenset()


def actor_id(request: Any) -> str | None:
    """The `users.id` behind a request, or None. The ONLY way to attribute a write.

    Defensive on every hop, and it has to be: the scheduler passes a stand-in with
    no `state`, tests pass fakes, and `httpx.ASGITransport` never runs a lifespan.
    A missing principal must record NULL rather than raise -- refusing to record a
    curation verdict because attribution failed would trade a working feature for a
    column.

    NULL is honest rather than lazy. It means the CLI decided, or the pipeline did,
    or the verdict predates anyone being asked to sign. Migration 0002 makes the
    same argument for not backfilling: putting a name on a verdict nobody signed
    would be inventing provenance, in a system whose third invariant is that
    provenance is never invented.

    This is the only place a router should reach for identity to hand downstream.
    `services/` takes a plain `actor: str | None` -- a service that can reach a
    `Request` is a service two callers cannot share, and C7 keeps `RunContext`
    identity-free for the same reason.
    """
    return _principal_attr(request, "user_id")


def actor_email(request: Any) -> str | None:
    """The same principal's EMAIL, for showing on screen.

    Two functions rather than one returning a pair, because the callers want
    different things: a write stores the id (a foreign key into `users`), and a
    response carries the email (the id is internal and means nothing to a curator).
    """
    return _principal_attr(request, "email")


def actor_via(request: Any) -> str | None:
    """HOW the request authenticated (`session` | `bearer` | `mcp` |
    `disabled`), or None with no principal at all.

    Exists for the one caller that must tell "no user row" apart from "auth is
    off": the learning surface binds an auth-off session to the local operator
    row rather than refusing, because on a single-user dev box the person at
    the keyboard IS the user (docs/adr/0157 addendum in epic O)."""
    return _principal_attr(request, "via")


def _principal_attr(request: Any, name: str) -> str | None:
    """Defensive on every hop. See `actor_id` for why that is required rather than
    cautious -- and note `Anonymous` in `tests/helpers.py`, a stand-in with no
    `state` at all, which is what a direct router call looks like."""
    state = getattr(request, "state", None)
    principal = getattr(state, "principal", None) if state is not None else None
    return getattr(principal, name, None) or None


# ── the policy ───────────────────────────────────────────────────────
#
# Only the READER set is listed. Everything else is `admin`, so the failure mode
# of forgetting an entry is a route a reader cannot reach — never a write a
# reader can.
#
# Paths are OpenAPI templates including the `/api` prefix. `{param}` matches one
# path segment.

READER_ROUTES: Final = frozenset(
    {
        # Liveness and identity. `logout` and `token-login` act on the caller's
        # OWN session, which is why a reader may call them.
        ("GET", "/api/health"),
        ("GET", "/api/health/live"),
        ("GET", "/api/auth/callback"),
        ("GET", "/api/auth/login"),
        ("GET", "/api/auth/mode"),
        ("GET", "/api/auth/session"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/auth/token-login"),
        # The first reader-reachable WRITE in the application, and it is safe for
        # a structural reason rather than a careful one: the route carries no
        # user id, so its subject is always `actor_id(request)` and there is
        # nothing for a caller to substitute. It writes one bounded field.
        ("PATCH", "/api/auth/me"),
        # Same structural safety, and the same one bounded field: how the
        # reader wants times shown to them. Written through `_actor`, so it
        # also works on the auth-off local box, where `actor_id` is None.
        ("PATCH", "/api/preferences"),
        # Where a reader has their own brief sent, and what they are told
        # about. The same structural argument as the two routes above, and it
        # has to hold harder here: these rows are the only reader-owned state
        # that carries an address. No route takes a user id, every one of them
        # resolves its subject through `learning._actor`, and the scope is part
        # of the lookup rather than a check after it -- so a destination id
        # belonging to somebody else is a 404 and not a 403, which would
        # otherwise confirm that it exists.
        ("GET", "/api/preferences/notifications"),
        ("POST", "/api/preferences/notifications"),
        ("GET", "/api/preferences/notifications/history"),
        ("PATCH", "/api/preferences/notifications/{destination_id}"),
        ("POST", "/api/preferences/notifications/{destination_id}/test"),
        ("DELETE", "/api/preferences/notifications/{destination_id}"),
        # The brief, and the archive behind it.
        ("GET", "/api/briefs"),
        ("GET", "/api/briefs/latest"),
        ("GET", "/api/briefs/search"),
        ("GET", "/api/briefs/{brief_id}"),
        # Hover cards. `cards:batch` is a READ over POST — see the warning above.
        ("POST", "/api/entities/cards:batch"),
        ("GET", "/api/entities/{entity_id}/card"),
        ("GET", "/api/entities/{entity_id}/neighbors"),
        # The entity page's two lists. `record` is deliberately absent: where a
        # name came from is an operator's question, and it is admin by the
        # default-deny above rather than by a check anyone has to remember.
        ("GET", "/api/entities/{entity_id}/connections"),
        ("GET", "/api/entities/{entity_id}/sources"),
        # The graph, and the evidence behind every edge. Three of these are reads
        # over POST because a graph query does not fit in a query string.
        ("POST", "/api/graph/query"),
        ("POST", "/api/graph/overview"),
        ("POST", "/api/graph/brief"),
        ("GET", "/api/graph/path"),
        ("GET", "/api/graph/search"),
        ("GET", "/api/edges/{edge_id}/provenance"),
        # The glossary as a browsable set. Reads only: `lookup` SPENDS and is
        # admin, and so is the audit that precedes it.
        ("GET", "/api/glossary"),
        ("GET", "/api/glossary/{surface}"),
        # Learning (docs/adr/0157). Every route's subject is the acting user
        # -- the PATCH /me structural argument -- so a reader can only ever
        # read or write their own progress. The review POST is the second
        # reader-reachable write; its body forbids extra fields, so there is
        # no user id to smuggle. The question bank and the generation run are
        # NOT here: those are admin by the default-deny.
        ("GET", "/api/learning/queue"),
        ("POST", "/api/learning/review"),
        ("GET", "/api/learning/progress"),
        ("GET", "/api/learning/overview"),
        ("GET", "/api/learning/counts"),
        ("GET", "/api/learning/settings"),
        ("PATCH", "/api/learning/settings"),
        # Personal relevance. Same structural argument as learning: no route
        # carries a user id, so the subject is always `actor_id(request)` and
        # a reader can only ever read or write their own emphasis and follows.
        # The generate POST is the one deliberate reader-reachable spend --
        # one light-lane call, capped per reader per day at the route.
        ("GET", "/api/relevance/settings"),
        ("PATCH", "/api/relevance/settings"),
        ("GET", "/api/relevance/follows"),
        ("PUT", "/api/relevance/follows/{entity_id}"),
        ("DELETE", "/api/relevance/follows/{entity_id}"),
        ("GET", "/api/relevance/suggestions"),
        ("GET", "/api/briefs/for-you"),
        ("POST", "/api/briefs/for-you/generate"),
        # The rubric's reader-safe projection: axis ids and the wording written
        # for a reader. The model's scoring prose and the profile stay admin.
        ("GET", "/api/meta/rubric"),
        # Search, the guide, the ontology, and the corpus-empty check the shell
        # uses to decide whether to show a first-run screen.
        ("GET", "/api/search"),
        ("GET", "/api/meta/guide"),
        ("GET", "/api/meta/ontology"),
        ("GET", "/api/data/stats"),
        # A reader's own agent tokens. `routers/meta.py` filters this to the
        # calling user; without that filter it would list everyone's.
        ("GET", "/api/meta/mcp"),
        # Ending one of their own connections. A reader must be able to: they
        # are the one who signed the assistant in, and on a hosted instance they
        # are not the person with a terminal. The route checks OWNERSHIP -- this
        # entry only says a reader may ask, not that the id is theirs. Revoking
        # anyone's is the admin route in `routers/admin.py`.
        ("DELETE", "/api/meta/mcp/tokens/{token_id}"),
        # The extension. Writes nothing, keeps no record of what was read.
        ("POST", "/api/annotate"),
        ("GET", "/api/sources/sites"),
        # The MCP surface itself. Read-only by construction (C12), so a reader's
        # agent gets a reader's answers.
        ("POST", "/api/mcp"),
        # Authorizing an agent of one's own. A reader may do this: the token that
        # results inherits THEIR role, so approving one grants exactly what they
        # already have and nothing more.
        ("GET", "/api/oauth/authorize"),
        ("POST", "/api/oauth/authorize"),
    }
)

#: Reachable with no credential at all. Mirrors `middleware.EXEMPT_PATHS`, which
#: is the set that actually enforces it — this is here so the sweep in
#: `tests/test_authz.py` can account for every route without special-casing.
#:
#: `POST /api/request-access` is the only write among them, and by design nothing
#: in that path can grant anything: approval is a separate admin action that
#: creates a `users` row.
PUBLIC_ROUTES: Final = frozenset(
    {
        ("GET", "/api/health/live"),
        ("GET", "/api/auth/mode"),
        ("GET", "/api/auth/login"),
        ("GET", "/api/auth/callback"),
        ("POST", "/api/auth/token-login"),
        ("POST", "/api/request-access"),
        # RFC 7591 and RFC 6749 §4.1.3. Both are unauthenticated by specification
        # and neither grants anything on its own: registration hands back an
        # identifier, and the token exchange requires a code a human approved plus
        # the PKCE verifier that binds it to the client that asked.
        ("POST", "/api/oauth/register"),
        ("POST", "/api/oauth/token"),
    }
)


def required_role(method: str, template: str) -> str:
    """The role a route demands. `admin` for anything unclassified."""
    key = (method.upper(), template)
    if key in PUBLIC_ROUTES or key in READER_ROUTES:
        return READER
    return ADMIN


# ── matching a request path to a template ────────────────────────────
#
# The middleware runs BEFORE routing, so it has a concrete path and no route. The
# templates are compiled once into regexes and matched longest-literal-first, so
# `/api/glossary/audit/corpus` cannot be captured by `/api/glossary/{surface}`.


def _to_regex(template: str, *, param: str = r"[^/]+") -> re.Pattern[str]:
    """`param` is what a `{placeholder}` becomes. The default matches any single
    segment; the entitlement table narrows it where a wrong match would give
    something away rather than refuse it (`_TIGHTER`)."""
    parts = re.split(r"(\{[^}]+\})", template)
    out = ["^"]
    for part in parts:
        out.append(param if part.startswith("{") else re.escape(part))
    out.append("$")
    return re.compile("".join(out))


def _specificity(template: str) -> tuple[int, int]:
    """Sort key: fewer parameters first, then longer literal prefix.

    Defence in depth rather than a live requirement. Only the reader and public
    templates are compiled, so the ordering could only matter if one of them
    over-matched an ADMIN path of the same method — and none does: swept over
    every path in the committed OpenAPI schema, 2026-09-14. The example worth
    keeping is `/api/glossary/audit/corpus` against `^/api/glossary/[^/]+$`, held
    by `tests/test_authz.py::test_a_reader_template_does_not_swallow_an_admin_path`.
    """
    return (template.count("{"), -len(template.split("{", 1)[0]))


def _compiled() -> list[tuple[re.Pattern[str], str]]:
    known = sorted(READER_ROUTES | PUBLIC_ROUTES, key=lambda mp: _specificity(mp[1]))
    return [(_to_regex(template), method) for method, template in known]


_MATCHERS = _compiled()


def role_for_request(method: str, path: str) -> str:
    """The role this concrete request needs. `admin` unless proven otherwise.

    NEVER returns "no opinion". An earlier draft returned None for an unmatched
    path so the caller could let it through to an ordinary 404 — which would have
    let every admin route through unchecked, since admin routes are exactly the
    ones absent from the table. Defaulting to `admin` is the whole safety property
    and it has to live here, not in the caller.

    The cost is that an authenticated reader who types a path that does not exist
    gets 403 rather than 404. That is the right way round: it leaks nothing (they
    are already authenticated, and an unauthenticated caller is refused earlier by
    `AuthMiddleware`), and the alternative trades a real permission check for a
    nicer status code on a typo.
    """
    wanted = method.upper()
    for pattern, route_method in _MATCHERS:
        if route_method == wanted and pattern.match(path):
            return READER
    return ADMIN


#: The hub's answer, cached in memory. None until something reads it.
#:
#: A MEMORY READ, because `billing_enforced()` is asked on every request through
#: `AuthMiddleware` and again inside `UserRecord.entitled`. A database round trip
#: there, to answer a question that changes about twice a year, is the wrong
#: trade -- and `UserRecord.entitled` is a property with no connection to make
#: one with.
#:
#: NO TTL, and that is only correct while the API runs as ONE process. NOTHING IN
#: the deployment config HOLDS IT TO ONE: the disk that did is gone (ADR 0234), and what
#: refuses a second replica now is
#: `tests/test_single_process_assumptions.py::test_the_api_is_pinned_to_one_replica`.
#: The single writer is the mgmt push, which happens in this process and refreshes
#: this value on its way through, so a second process reads a value the other
#: one's push updated and goes on reading it -- a paywall stale in one process and
#: not the other, invisible from outside. A zero-downtime deploy overlaps two for
#: about a minute, which is the bounded version of that; a standing second replica
#: is the day this needs a TTL.
_HUB_BILLING: bool | None = None

#: `1`/`true`/`yes` and `0`/`false`/`no`. BOTH directions, so the environment can
#: force the gate OFF as well as on.
_TRUE = ("1", "true", "yes")
_FALSE = ("0", "false", "no")


def billing_enforced() -> bool:
    """Whether this instance charges. ONE answer, for three questions.

    The route gate, the personal-brief warm pass and the notification fan-out
    all ask it, and they must agree: a half-enforced paywall either refuses
    readers whose entitlement nothing is maintaining, or keeps spending on
    readers it has already locked out.

    THREE SOURCES, in this order (ADR 0214).

    `VENONA_BILLING_ENFORCED` wins when it is set to anything recognisable, in
    EITHER direction. It is the break-glass for a day when the hub is wrong or
    unreachable, and it is what lets a local instance ignore a hub entirely --
    so `0` has to mean off, not merely "not on".

    Otherwise the hub's switch, as `refresh_billing_enforced` last read it.
    `config/settings.yaml` is not consulted and never will be: it is editable
    over HTTP, and a paywall an admin session could switch off through the
    config editor is not a paywall.

    Otherwise OFF. The hub and this instance ship from one commit through
    separate deploy build filters (ADR 0209), so there is a window where this
    code is live and nothing has been pushed yet; enforcing then would refuse
    the entire readership. It is also the honest default for every instance that
    takes no money at all, where every account reads `none` and would otherwise
    silently stop receiving the product.
    """
    declared = os.environ.get("VENONA_BILLING_ENFORCED", "").strip().lower()
    if declared in _TRUE:
        return True
    if declared in _FALSE:
        return False
    return bool(_HUB_BILLING)


def refresh_billing_enforced(conn) -> bool:
    """Re-read the hub's switch from the database. Returns what is now cached.

    Called from the two places that can know it changed: the lifespan, once, so
    a restart picks up a switch thrown while the process was down; and the mgmt
    write, so flipping it on the hub's page takes effect on the next request
    rather than the next deploy.

    Takes a connection rather than making one -- the callers both hold one, and
    a repository-shaped function that opens its own connection is how a request
    path acquires a second one nobody counted.
    """
    global _HUB_BILLING
    from venona.db.repositories import hub_flags as flags_repo

    _HUB_BILLING = flags_repo.billing_enforced(conn)
    return _HUB_BILLING


# ── the entitlement axis ─────────────────────────────────────────────
#
# A THIRD question, beside "is this reachable at all" and "which role". Whether
# this reader has paid. Admin routes are never asked it -- an admin operates the
# instance -- so this only ever narrows the reader set.
#
# DEFAULT-DENY FOR READERS: a reader route that is not named in
# `UNENTITLED_ROUTES` needs a subscription. A reader feature added next month is
# therefore paid by default rather than free by omission, which is the same
# property `EXEMPT_PATHS` gives the auth boundary.

#: Reader routes that survive a lapsed subscription.
#:
#: The brief family is here in FULL, and the ceiling is applied to the rows
#: instead (`services/brief_service.py`). `GET /api/briefs/latest` returns the
#: newest brief a lapsed reader may open rather than refusing: `/` renders
#: `BriefView`, so a 403 there would bounce them to the paywall and they would
#: never see the archive ADR 0211 promises them.
#:
#: The shell's boot reads are here for a blunter reason: without them a lapsed
#: reader gets a broken application rather than their archive, which is a worse
#: outcome than being refused honestly.
UNENTITLED_ROUTES: Final = frozenset(
    {
        ("GET", "/api/health"),
        ("GET", "/api/health/live"),
        ("GET", "/api/auth/callback"),
        ("GET", "/api/auth/login"),
        ("GET", "/api/auth/mode"),
        ("GET", "/api/auth/session"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/auth/token-login"),
        ("PATCH", "/api/auth/me"),
        # How the reader wants times shown to them -- including the billing
        # dates on the paywall screen they are looking at. The READ rides
        # `/api/auth/session`, so only the write is a route.
        ("PATCH", "/api/preferences"),
        # The archive they paid for. Row-limited, not route-limited.
        ("GET", "/api/briefs"),
        ("GET", "/api/briefs/latest"),
        ("GET", "/api/briefs/search"),
        ("GET", "/api/briefs/{brief_id}"),
        # What the shell reads before it can render anything at all.
        ("GET", "/api/data/stats"),
        ("GET", "/api/meta/guide"),
        # THE HOVER LAYER, which is part of the archive rather than a feature
        # on top of it -- ADR 0232. `BriefView` prefetches every reference
        # through `cards:batch` and falls back to the single-card read per
        # hover, so refusing these renders the lapsed reader's own archive as
        # prose whose every program and acronym is dead.
        ("POST", "/api/entities/cards:batch"),
        ("GET", "/api/entities/{entity_id}/card"),
        # THE READER'S OWN ACCOUNT, all of it. Settings is not the product --
        # ADR 0235. `web/src/router/guards.ts::REACHABLE_UNSUBSCRIBED` is the
        # client half and has to agree.
        ("GET", "/api/preferences/notifications"),
        ("POST", "/api/preferences/notifications"),
        ("GET", "/api/preferences/notifications/history"),
        ("PATCH", "/api/preferences/notifications/{destination_id}"),
        ("POST", "/api/preferences/notifications/{destination_id}/test"),
        ("DELETE", "/api/preferences/notifications/{destination_id}"),
        ("GET", "/api/relevance/settings"),
        ("PATCH", "/api/relevance/settings"),
        ("GET", "/api/relevance/follows"),
        ("PUT", "/api/relevance/follows/{entity_id}"),
        ("DELETE", "/api/relevance/follows/{entity_id}"),
        ("GET", "/api/relevance/suggestions"),
        ("GET", "/api/meta/rubric"),
        ("GET", "/api/meta/mcp"),
        ("DELETE", "/api/meta/mcp/tokens/{token_id}"),
    }
)

#: Reader routes that need a live subscription, named EXPLICITLY even though the
#: default already covers them.
#:
#: The explicit naming is what makes the matcher correct rather than lucky.
#: `/api/briefs/{brief_id}` compiles to `^/api/briefs/[^/]+$`, which also matches
#: `/api/briefs/latest` and `/api/briefs/for-you`. Sorting only happens WITHIN a
#: set, so a literal in one frozenset can never shadow a wildcard in another --
#: both sets are compiled into ONE ordered table below, and these entries are
#: what let `for-you` win against the wildcard that would otherwise make the
#: personal brief free.
ENTITLED_ROUTES: Final = frozenset(
    {
        ("GET", "/api/briefs/for-you"),
        ("POST", "/api/briefs/for-you/generate"),
        # The graph. A place you GO, unlike the hover layer inside a brief.
        ("GET", "/api/entities/{entity_id}/neighbors"),
        ("GET", "/api/edges/{edge_id}/provenance"),
        # The entity page's two lists, and the same distinction: a name's card
        # is part of the archive a lapsed reader keeps, so `/card` stays free
        # above and they can still read every brief they paid for with its
        # definitions intact. The page those definitions link to is the whole
        # of what Venona holds on that name, which is the product. The surface
        # refuses ONCE and says so, rather than 403ing section by section.
        ("GET", "/api/entities/{entity_id}/connections"),
        ("GET", "/api/entities/{entity_id}/sources"),
        ("GET", "/api/graph/path"),
        ("GET", "/api/graph/search"),
        ("POST", "/api/graph/brief"),
        ("POST", "/api/graph/overview"),
        ("POST", "/api/graph/query"),
        ("GET", "/api/meta/ontology"),
        # Learning. Reading their own progress is still reading the product.
        ("GET", "/api/learning/counts"),
        ("GET", "/api/learning/overview"),
        ("GET", "/api/learning/progress"),
        ("GET", "/api/learning/queue"),
        ("GET", "/api/learning/settings"),
        ("PATCH", "/api/learning/settings"),
        ("POST", "/api/learning/review"),
        # Search across the corpus, and the glossary as a surface of its own.
        # The single card behind a hover is unentitled above; browsing every
        # term is the product.
        ("GET", "/api/search"),
        ("GET", "/api/glossary"),
        ("GET", "/api/glossary/{surface}"),
        ("GET", "/api/sources/sites"),
        # Agents and the extension. CONNECTING one is paid; disconnecting is
        # not, and lives in the unentitled set above.
        ("POST", "/api/mcp"),
        ("POST", "/api/annotate"),
        ("GET", "/api/oauth/authorize"),
        ("POST", "/api/oauth/authorize"),
    }
)


#: Templates whose parameter is compiled TIGHTER than `[^/]+` on this axis.
#:
#: On the role axis an over-matching wildcard costs a refusal, which somebody
#: notices. Here it costs a GIVEAWAY: `^/api/briefs/[^/]+$` is unentitled, so
#: any future `GET /api/briefs/<anything>` -- an export, a feed, a bulk
#: download -- is free the moment it is written, without anybody deciding.
#: Brief ids are type-prefixed (`brief:2026-09-01:...`), so the wildcard can say
#: so and a new literal segment falls through to default-deny instead.
_TIGHTER: Final = {"/api/briefs/{brief_id}": r"brief:[^/]+"}


def _entitlement_table() -> list[tuple[re.Pattern[str], str, bool]]:
    """One ordered table, both sets in it, longest-literal-first.

    Two separate tables would let `^/api/briefs/[^/]+$` from the unentitled set
    swallow `/api/briefs/for-you` from the entitled one, and the personal brief
    -- the only per-reader model spend a reader can trigger -- would be free.
    """
    rows = [(method, template, False) for method, template in UNENTITLED_ROUTES]
    rows += [(method, template, True) for method, template in ENTITLED_ROUTES]
    rows.sort(key=lambda row: _specificity(row[1]))
    return [
        (_to_regex(template, param=_TIGHTER.get(template, r"[^/]+")), method, needs)
        for method, template, needs in rows
    ]


_ENTITLEMENT = _entitlement_table()


def entitlement_required(method: str, path: str) -> bool:
    """Whether this concrete request needs a live subscription.

    Only meaningful for reader-reachable paths; the caller checks the role first
    and never asks about an admin one. An unmatched path returns True, matching
    `role_for_request`'s default-deny: a reader route nobody classified is
    treated as paid, so the failure mode of forgetting is a refusal rather than a
    giveaway.
    """
    wanted = method.upper()
    for pattern, route_method, needs in _ENTITLEMENT:
        if route_method == wanted and pattern.match(path):
            return needs
    return True
