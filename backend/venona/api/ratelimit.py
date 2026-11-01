"""In-process token buckets.

WHY IN-PROCESS. The same reason `RunRegistry` is an `asyncio.Lock` and not a
table (contract C11): one user, one process, and a self-hosted deployment says in a
comment that the API must never be scaled past one replica. Redis here would be
a second piece of infrastructure to run, back up and lose, to count requests for
one person.

WHY VOLUME AND RATE, NEVER SPEND. `llm/budget.py` argues this at length and the
argument does not change because the app is now on the internet: on a
subscription the scarce resources are rate limits and operator attention, not
dollars, and conflating measurement with management is what put a spend cap on a
flat-rate plan once already. Nothing here has a currency in it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Bucket names. `login` is the credential-GUESSING budget: `token-login` is the
# one route where a caller presents a secret that can be wrong, and it is
# otherwise a non-GET that would land in `write` and be brute-forceable at
# 60/min.
LOGIN = "login"
# The OIDC round trip, which is TWO requests per attempt -- the 302 out to the
# provider and the callback back -- and which is priced separately from `login`
# because neither half accepts a guessable secret. `/auth/login` mints a state
# cookie and redirects; `/auth/callback` verifies that cookie's signature
# before it will talk to the provider at all. Charged at the guessing rate, one
# operator's whole budget buys two and a half attempts, and a sign-in that has
# to be retried -- an expired state cookie, a slow hop through the hub -- is a
# documented ordinary outcome (docs/RUNBOOK.md, "Nobody can sign in").
SIGNIN = "signin"
API = "api"
WRITE = "write"
# `/api/request-access` is unauthenticated AND a write, which no other route in
# the app is. Left in `write` it would be a 60-per-minute anonymous INSERT --
# a form anyone can point a script at, filling a table nobody is watching.
SIGNUP = "signup"
# The MCP surface. Every JSON-RPC message is a POST, so without this every tool
# call is charged to `write` at 60/min -- and one `annotate` plus a dozen
# `evidence` calls trips it. The 429 then reaches the agent as an opaque tool
# failure, which it will retry immediately.
#
# `write` is the wrong price for two reasons, not one. It is the wrong SIZE (the
# work is read-only; the binding cost is CPU -- `build_index` is ~84ms -- not
# writes), and it is the wrong KEY: `_client_key` charges the client address, so
# an agent on the operator's own laptop shares a bucket with their browser and a
# runaway loop locks the operator out of their own app.
#
# This bucket fixes the size. The KEY is fixed separately, in `AuthMiddleware`,
# by a second post-auth check against `principal.token_id` -- which is only
# possible because a restricted credential has a row in `mcp_tokens`.
MCP = "mcp"
# Writes, keyed on the USER rather than on the address.
#
# `_client_key` charges the edge-observed client address, so every bucket in this
# module is per-address: two people behind one NAT share `write`, and the
# operator's browser shares with their own extension. That key is also the one
# thing that shifts under a signed-in caller -- a laptop that moves between a
# phone hotspot and an office network is three addresses and three full budgets.
#
# So this one is charged AFTER the per-IP check, on `principal.user_id`. It can
# only ever tighten: a request refused by the address bucket never reaches it.
# Same capacity as `WRITE` deliberately, which makes it a no-op for a single
# person at one address -- the operator's own limit does not move -- while
# turning "60 writes a minute per address" into "60 writes a minute, whatever
# address they come from".
#
# WHAT IT DOES NOT FIX, because the plan for it claimed otherwise until the
# ordering was checked: two users behind one NAT still share `write`, because
# the address bucket is charged first and neither this bucket nor any other
# post-auth check can give back a token already spent. Fixing that means keying
# the PRE-auth bucket on a user, and it structurally cannot be -- the check at
# `middleware.__call__` precedes `principal_for`, which is what makes it a
# defence rather than something an unauthenticated caller walks past.
USER_WRITE = "user-write"


@dataclass(frozen=True)
class Limit:
    capacity: int
    per_seconds: float

    @property
    def refill_per_second(self) -> float:
        return self.capacity / self.per_seconds


LIMITS = {
    # Deliberately tight. There is one operator and they present a break-glass
    # token perhaps never, so anything above single digits is somebody else.
    LOGIN: Limit(capacity=5, per_seconds=900),
    # Ten round trips a quarter of an hour, per address. A person who cannot get
    # in retries immediately and keeps retrying, and every retry is two of these
    # -- so the budget has to hold a stubborn human, not a tidy one.
    SIGNIN: Limit(capacity=20, per_seconds=900),
    # Generous: one tab polling health every 3s during a light run is ~40/min
    # on its own, and the graph view bursts on load.
    API: Limit(capacity=600, per_seconds=60),
    WRITE: Limit(capacity=60, per_seconds=60),
    # Three an hour. A person asking for access does it once; a person who
    # mistypes their address does it twice. Anything past that is not a person.
    SIGNUP: Limit(capacity=3, per_seconds=3600),
    # An agent walking 200 entities is one legitimate burst that completes and
    # refills inside a minute. Four a second is already faster than any research
    # loop with a model in it, so anything sustained above this is a bug or a
    # loop -- a ceiling on surprise, not a budget.
    #
    # NOT in GLOBAL_BACKSTOP: here the caller has a row, so per-IP is already the
    # right key. A global ceiling would only let one agent starve another, which
    # is the failure it exists to prevent, inverted.
    #
    # And no `spend` bucket, deliberately: nothing on the MCP surface spends.
    # `llm/budget.py` argues at length that measuring is not managing, and the
    # control for spend here is that the tools do not exist. Nothing in this
    # module has a currency in it.
    MCP: Limit(capacity=240, per_seconds=60),
    # Equal to WRITE, and that equality is the design: see USER_WRITE.
    USER_WRITE: Limit(capacity=60, per_seconds=60),
    # The global backstops, keyed "*" rather than by client. Named in LIMITS
    # rather than relying on `check`'s fallback so that adding a third one is a
    # visible line here instead of an accidental reuse of the login ceiling.
    "global-login": Limit(capacity=20, per_seconds=3600),
    "global-signup": Limit(capacity=40, per_seconds=3600),
    # Set FAR above one operator's sign-in rate, and that is the whole design of
    # it. What it bounds is a distributed flood: a caller who has collected a
    # state cookie can make the callback spend one outbound request on the
    # provider's token endpoint, and per-IP does not reach an attacker with many
    # addresses. What it must never do is refuse the operator, because a global
    # ceiling on the way IN is the one that locks them out of their own instance
    # from someone else's traffic -- with no way to sign in and turn it off.
    "global-signin": Limit(capacity=600, per_seconds=3600),
    # Belt-and-braces over WRITE. `_client_key` charges the edge-observed client
    # address, so the per-IP WRITE bucket already bounds each real caller; this
    # aggregate ceiling bounds the SUM, so no write path is ever unbounded even if
    # that key is wrong -- including the unauthenticated `/api/oauth/register` and
    # `/api/oauth/token`, which land in WRITE. Set well above one operator's write
    # rate: a global ceiling must never become the operator's own outage.
    "global-write": Limit(capacity=600, per_seconds=60),
}

# A second, global bucket for logins, because per-IP is weak here: behind a
# reverse proxy every request can share one source address, and an attacker on
# many addresses defeats per-IP anyway. This is the backstop that does not care.
# Kept as the fallback for any bucket name not in LIMITS.
GLOBAL_LOGIN = LIMITS["global-login"]

# Which per-IP buckets get a global ceiling on top, and what it is called.
# LOGIN, SIGNIN and SIGNUP are unauthenticated, where per-IP alone is weak
# because the caller is a stranger. WRITE is here as belt-and-braces: it is the
# fallback bucket for every non-GET, so backstopping it means no write path --
# including the unauthenticated `/api/oauth/register` and `/api/oauth/token` --
# is ever unbounded, whatever the per-IP key resolves to.
#
# THE SIZE OF A GLOBAL CEILING IS NOT A FREE CHOICE, and the two here are sized
# by opposite arguments. A ceiling on guessing (`global-login`) is meant to bind
# and 20/hour does; a ceiling on the way in (`global-signin`) that binds is an
# outage the operator cannot sign in to fix, so it sits far above them.
GLOBAL_BACKSTOP = {
    LOGIN: "global-login",
    SIGNIN: "global-signin",
    SIGNUP: "global-signup",
    WRITE: "global-write",
}

# Bucket state is keyed by client address and would otherwise grow without
# bound once the app is public -- a slow memory leak driven by strangers. Swept
# whenever the map gets large; a bucket at full capacity carries no information,
# so dropping it is free.
MAX_KEYS = 10_000


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class Verdict:
    allowed: bool
    retry_after: int = 0


@dataclass
class RateLimiter:
    _buckets: dict[tuple[str, str], _Bucket] = field(default_factory=dict)

    def check(self, bucket: str, key: str, *, now: float | None = None) -> Verdict:
        limit = LIMITS[bucket] if bucket in LIMITS else GLOBAL_LOGIN
        now = time.monotonic() if now is None else now
        entry = self._buckets.get((bucket, key))

        if entry is None:
            if len(self._buckets) >= MAX_KEYS:
                self._evict(now)
            entry = _Bucket(tokens=float(limit.capacity), updated=now)
            self._buckets[(bucket, key)] = entry
        else:
            elapsed = max(0.0, now - entry.updated)
            entry.tokens = min(limit.capacity, entry.tokens + elapsed * limit.refill_per_second)
            entry.updated = now

        if entry.tokens < 1.0:
            missing = 1.0 - entry.tokens
            return Verdict(
                allowed=False, retry_after=max(1, int(missing / limit.refill_per_second))
            )

        entry.tokens -= 1.0
        return Verdict(allowed=True)

    def _evict(self, now: float) -> None:
        """Drop every bucket that has refilled to capacity. Those are exactly
        the keys carrying no information."""
        for composite, entry in list(self._buckets.items()):
            limit = LIMITS.get(composite[0], GLOBAL_LOGIN)
            elapsed = max(0.0, now - entry.updated)
            if entry.tokens + elapsed * limit.refill_per_second >= limit.capacity:
                del self._buckets[composite]
        # Still full of live buckets: this is an attack, not a working set.
        # Losing the oldest state fails OPEN for those keys, which is the right
        # direction -- a rate limiter must never become the outage.
        if len(self._buckets) >= MAX_KEYS:
            for composite in list(self._buckets)[: MAX_KEYS // 2]:
                del self._buckets[composite]


# One limiter for the process, rather than one per middleware instance. The two
# are the same thing today -- `AuthMiddleware` is added once -- but a module
# singleton is honest about the fact that these buckets are process-wide state,
# and it gives the tests something to reset that does not involve reaching into
# a built ASGI stack.
limiter = RateLimiter()


def reset() -> None:
    """Tests only. Bucket state is process-wide and the app is a singleton, so
    without this the order tests happen to run in decides their outcome."""
    limiter._buckets.clear()


# Both sets are named explicitly, not matched on the `/api/auth/` prefix.
#
# The prefix was the obvious rule and it was wrong: it swept in `/auth/mode`,
# which the login page polls on every render, `/auth/session`, which the SPA
# calls once before mount, and `/auth/logout`. Spending a five-per-fifteen-
# minutes budget on those means the operator's own app locks them out of
# signing in -- and the global ceiling would be gone before anyone tried.
#
# The route where a caller PRESENTS A SECRET that can be wrong, which is what
# `LOGIN` prices. It is the only one, and it is reachable without a session by
# necessity: it is how you get in when the identity provider is broken.
TOKEN_LOGIN_PATHS = frozenset({"/api/auth/token-login"})

# The two halves of one OIDC round trip. A visitor spends both to sign in once,
# which is what makes pricing them as guesses wrong.
SIGNIN_PATHS = frozenset({"/api/auth/login", "/api/auth/callback"})

# Unauthenticated writes. Not a credential path -- nothing here turns into a
# session -- so it gets its own budget rather than borrowing the login one,
# which the operator needs intact even while a bot is hammering the form.
SIGNUP_PATHS = frozenset({"/api/request-access"})

# Read-only JSON-RPC, so it must not fall through to `write`. Named here rather
# than imported from `middleware.MCP_PATHS` to keep this module a pure function
# of (path, method) with no import cycle -- `tests/test_ratelimit.py` asserts the
# two sets agree, which is the same shape of check as the EXEMPT_PATHS pin.
MCP_PATHS = frozenset({"/api/mcp"})


def bucket_for(path: str, method: str) -> str:
    if path in TOKEN_LOGIN_PATHS:
        return LOGIN
    if path in SIGNIN_PATHS:
        return SIGNIN
    # GET-guarded so a HEAD or a preflight against the form is not charged a
    # third of somebody's hourly budget.
    if path in SIGNUP_PATHS and method not in ("GET", "HEAD", "OPTIONS"):
        return SIGNUP
    # Before the WRITE fallback. A POST here is read-only work; pricing it as a
    # write prices the wrong thing, and at 60/min it prices it wrongly enough to
    # break a single ordinary agent task.
    if path.rstrip("/") in MCP_PATHS:
        return MCP
    return API if method in ("GET", "HEAD", "OPTIONS") else WRITE
