"""Rate limiting.

Volume and rate, never spend — `llm/budget.py` argues that at length and the
argument survives the app going on the internet. Nothing here has a currency in
it.
"""

from __future__ import annotations

import httpx
import pytest
from venona.api import ratelimit, security
from venona.api.middleware import EXEMPT_PATHS, _client_key
from venona.api.ratelimit import (
    API,
    GLOBAL_BACKSTOP,
    GLOBAL_LOGIN,
    LIMITS,
    LOGIN,
    SIGNIN,
    WRITE,
    RateLimiter,
    bucket_for,
)

SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef"
TOKEN = "f" * 64


def test_a_bucket_allows_up_to_capacity_then_refuses() -> None:
    limiter = RateLimiter()
    capacity = LIMITS[LOGIN].capacity

    for _ in range(capacity):
        assert limiter.check(LOGIN, "1.2.3.4", now=0.0).allowed

    verdict = limiter.check(LOGIN, "1.2.3.4", now=0.0)
    assert not verdict.allowed
    assert verdict.retry_after >= 1


def test_a_bucket_refills_over_time() -> None:
    limiter = RateLimiter()
    for _ in range(LIMITS[LOGIN].capacity):
        limiter.check(LOGIN, "1.2.3.4", now=0.0)
    assert not limiter.check(LOGIN, "1.2.3.4", now=0.0).allowed

    # One full window later, the bucket is back.
    assert limiter.check(LOGIN, "1.2.3.4", now=LIMITS[LOGIN].per_seconds).allowed


def test_buckets_are_per_key() -> None:
    limiter = RateLimiter()
    for _ in range(LIMITS[LOGIN].capacity):
        limiter.check(LOGIN, "1.2.3.4", now=0.0)

    assert not limiter.check(LOGIN, "1.2.3.4", now=0.0).allowed
    assert limiter.check(LOGIN, "5.6.7.8", now=0.0).allowed


# ── the client key: the hop the trusted edge observed ────────────────
#
# The limiter is only as strong as the address it charges, and there are two
# ways to get that address wrong -- one per deployment.
#
# Behind the reverse proxy the danger is believing the client. Uvicorn's
# `--forwarded-allow-ips '*'` copies the LEFTMOST X-Forwarded-For entry into
# `scope["client"]`, and the leftmost is whatever the client wrote -- so keying on
# it lets a request rotate the header and mint a fresh bucket every time, voiding
# every per-IP limit. The rightmost hop is the one the reverse proxy appended.
#
# Behind Cloudflare -> Render the danger is the opposite: the rightmost hop is
# the platform's own router, identical for every visitor, so keying on it merges
# the whole internet into one bucket. `CF-Connecting-IP` is the real client
# there, and is trustworthy only there.


def _scope(*, xff: str | None = None, cf: str | None = None, client=("10.9.9.9", 0)) -> dict:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    if cf is not None:
        headers.append((b"cf-connecting-ip", cf.encode()))
    return {"type": "http", "headers": headers, "client": client}


def test_client_key_takes_the_rightmost_forwarded_hop() -> None:
    # `client, edge` -> the edge (rightmost) is the address the proxy observed.
    assert _client_key(_scope(xff="9.9.9.9, 203.0.113.7")) == "203.0.113.7"
    # A single hop is its own rightmost.
    assert _client_key(_scope(xff="203.0.113.7")) == "203.0.113.7"
    # Whitespace around hops is not part of the key.
    assert _client_key(_scope(xff="9.9.9.9 ,  203.0.113.7 ")) == "203.0.113.7"


def test_a_spoofed_forwarded_prefix_cannot_mint_a_fresh_bucket() -> None:
    """⭐ The XFF-spoofing bypass. Everything the client writes lands to the LEFT
    of the hop the edge appends, so rotating the prefix must not change the key —
    otherwise every per-IP bucket is one header away from being voided."""
    keys = {
        _client_key(_scope(xff=f"{fake}, 203.0.113.7"))
        for fake in ("1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4, 5.5.5.5")
    }
    assert keys == {"203.0.113.7"}, "the client's forged prefix leaked into the bucket key"


def test_edge_key_prefers_cf_connecting_ip() -> None:
    """⭐ The bucket-collapse. On the edge path the rightmost hop is the platform's own
    router — shared by every visitor on the platform — so keying on it makes all
    per-IP buckets one bucket, and five failed logins from a stranger lock out
    everyone. Cloudflare's header is the only entry that names the real client."""
    scope = _scope(xff="9.9.9.9, 172.71.0.5", cf="203.0.113.7")
    assert _client_key(scope, edge=True) == "203.0.113.7"
    # Two visitors behind one shared platform hop must not share a bucket.
    other = _scope(xff="9.9.9.9, 172.71.0.5", cf="198.51.100.4")
    assert _client_key(other, edge=True) != _client_key(scope, edge=True)


def test_off_edge_ignores_cf_connecting_ip() -> None:
    """The header is only trustworthy where Cloudflare overwrites it. Behind
    the reverse proxy a client can write it themselves, so believing it there would hand back
    the forgeable key the rightmost hop exists to avoid."""
    scope = _scope(xff="9.9.9.9, 203.0.113.7", cf="1.1.1.1")
    assert _client_key(scope) == "203.0.113.7"
    assert _client_key(scope, edge=False) == "203.0.113.7"


def test_edge_falls_back_to_the_rightmost_hop_without_cloudflare() -> None:
    """The edge flag says Cloudflare is in front, not that every request came
    through it: the platform healthcheck dials the service directly."""
    assert _client_key(_scope(xff="9.9.9.9, 203.0.113.7"), edge=True) == "203.0.113.7"
    assert _client_key(_scope(xff="203.0.113.7", cf="  "), edge=True) == "203.0.113.7"
    assert _client_key(_scope(xff=None, client=("192.0.2.50", 0)), edge=True) == "192.0.2.50"


def test_no_forwarded_header_falls_back_to_the_socket_peer() -> None:
    """A direct local connection or a test client has no proxy in front, so the
    socket peer IS the client."""
    assert _client_key(_scope(xff=None, client=("192.0.2.50", 0))) == "192.0.2.50"
    assert _client_key(_scope(xff=None, client=None)) == "unknown"


# ── the write backstop ───────────────────────────────────────────────


def test_write_has_a_global_backstop() -> None:
    """WRITE is the fallback bucket for every non-GET. Backstopping it means no
    write path is ever unbounded, whatever the per-IP key resolves to."""
    assert WRITE in GLOBAL_BACKSTOP
    assert GLOBAL_BACKSTOP[WRITE] in LIMITS


def test_every_unauthenticated_write_is_globally_backstopped() -> None:
    """The inventory guard. `/api/oauth/register` slipped into the weaker WRITE
    bucket because the design assumed request-access was the only unauthenticated
    write. This fails the moment a new exempt path routes a POST to a bucket that
    has no global ceiling."""
    for path in EXEMPT_PATHS:
        bucket = bucket_for(path, "POST")
        assert bucket in GLOBAL_BACKSTOP, (
            f"exempt path {path} routes a POST to {bucket!r}, which has no global backstop"
        )


def test_the_global_write_ceiling_bounds_an_aggregate_flood() -> None:
    """Many distinct addresses each pass their own fresh per-IP WRITE bucket, but
    the global "*" ceiling is what an aggregate flood eventually meets — the whole
    point of a backstop that does not care where a request came from."""
    limiter = RateLimiter()
    backstop = GLOBAL_BACKSTOP[WRITE]
    capacity = LIMITS[backstop].capacity
    refused = False
    for i in range(capacity + 5):
        per_ip = limiter.check(WRITE, f"10.0.{i // 256}.{i % 256}", now=0.0)
        assert per_ip.allowed, "each fresh address must pass its own per-IP bucket"
        if not limiter.check(backstop, "*", now=0.0).allowed:
            refused = True
            break
    assert refused, "the aggregate flood was never refused by the global ceiling"


def test_the_retry_after_is_long_enough_to_actually_help() -> None:
    """A `Retry-After` shorter than the refill interval invites a client to
    retry into another refusal, which is how a limiter becomes a busy loop."""
    limiter = RateLimiter()
    for _ in range(LIMITS[LOGIN].capacity):
        limiter.check(LOGIN, "1.2.3.4", now=0.0)
    verdict = limiter.check(LOGIN, "1.2.3.4", now=0.0)

    assert limiter.check(LOGIN, "1.2.3.4", now=float(verdict.retry_after)).allowed


def test_token_login_is_a_login_not_a_write() -> None:
    """It is EXEMPT from auth by necessity, so it is the one unauthenticated
    endpoint that turns a PRESENTED SECRET into a session. In the `write` bucket
    it would be brute-forceable at 60/min with no global ceiling."""
    assert bucket_for("/api/auth/token-login", "POST") == LOGIN


def test_the_oidc_round_trip_is_not_priced_as_guessing() -> None:
    """⭐ The lockout. A sign-in is TWO requests — the 302 out and the callback
    back — so charging both to a five-per-fifteen-minutes guessing budget buys
    the operator two and a half attempts, and a retry is the documented ordinary
    outcome of a slow first login. Neither half accepts a secret that can be
    wrong, which is what makes the guessing price the wrong one."""
    assert bucket_for("/api/auth/login", "GET") == SIGNIN
    assert bucket_for("/api/auth/callback", "GET") == SIGNIN

    attempts = LIMITS[SIGNIN].capacity / 2
    assert attempts >= 8, "a person who cannot get in retries, and every retry is two requests"


def test_the_signin_ceiling_is_never_the_operators_outage() -> None:
    """⭐ The other half of the lockout, and the one no per-IP fix reaches. The
    global bucket is keyed "*", so a stranger hammering `/api/auth/login` spends
    the SAME budget the operator needs — and the surface they would turn it off
    from is the one it refuses. A guessing ceiling is meant to bind; this one
    must sit far above any real person."""
    ceiling = LIMITS[GLOBAL_BACKSTOP[SIGNIN]]
    per_hour = ceiling.capacity / (ceiling.per_seconds / 3600)

    # An operator signing in every few minutes for an hour, at two requests each.
    assert per_hour > 20 * 2 * 5
    assert ceiling.refill_per_second > LIMITS[SIGNIN].refill_per_second


def test_ordinary_traffic_is_split_by_method() -> None:
    assert bucket_for("/api/briefs", "GET") == API
    assert bucket_for("/api/runs", "POST") == WRITE


def test_harmless_auth_reads_are_not_charged_to_the_login_budget() -> None:
    """Matching on the `/api/auth/` PREFIX was the obvious rule and it was
    wrong: `mode` is polled by the login page itself, `session` once before
    mount, `logout` on the way out. Charging those to a five-per-fifteen-minutes
    budget means the app locks the operator out of signing into it."""
    assert bucket_for("/api/auth/mode", "GET") == API
    assert bucket_for("/api/auth/session", "GET") == API
    assert bucket_for("/api/auth/logout", "POST") == WRITE


def test_the_global_login_ceiling_is_tighter_than_a_days_worth_of_per_ip() -> None:
    """Per-IP is weak for logins: behind a proxy every request can share one
    address, and an attacker with many addresses defeats per-IP anyway. The
    global bucket is the backstop that does not care where they came from."""
    assert GLOBAL_LOGIN.capacity < LIMITS[LOGIN].capacity * 10


def test_eviction_bounds_the_bucket_map() -> None:
    """Keyed by client address, so on a public host this grows without bound —
    a slow memory leak driven by strangers."""
    from venona.api.ratelimit import MAX_KEYS

    limiter = RateLimiter()
    for i in range(MAX_KEYS + 100):
        limiter.check(API, f"10.0.{i // 256}.{i % 256}", now=0.0)

    assert len(limiter._buckets) <= MAX_KEYS


# ── through the real middleware ──────────────────────────────────────


@pytest.fixture
def auth_env(monkeypatch, tmp_path):
    # Bucket state is process-wide and `app` is a module singleton, so without
    # this the order the suite happens to run in decides the outcome.
    ratelimit.reset()
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", "operator@example.com")
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", TOKEN)
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()
    yield
    security.get_auth_config.cache_clear()


async def test_repeated_bad_token_logins_are_locked_out(auth_env) -> None:
    """The brute-force path. `token-login` has to be reachable without a
    session — it is how you get in when Google is broken — so the limiter is
    the only thing standing in front of the API token."""
    from venona.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://t"
    ) as client:
        statuses = [
            (await client.post("/api/auth/token-login", json={"token": "wrong"})).status_code
            for _ in range(LIMITS[LOGIN].capacity + 3)
        ]

    assert 401 in statuses, "the wrong token should be refused on its own merits"
    assert 429 in statuses, "and repeated attempts should stop being answered at all"


async def test_a_rate_limited_response_says_when_to_come_back(auth_env) -> None:
    from venona.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://t"
    ) as client:
        last = None
        for _ in range(LIMITS[LOGIN].capacity + 3):
            last = await client.post("/api/auth/token-login", json={"token": "wrong"})

    assert last is not None and last.status_code == 429
    assert int(last.headers["retry-after"]) >= 1
    assert last.json()["detail"]["error"] == "rate_limited"


async def _exhaust_signin(client, *, accept: str):
    """Spend the sign-in budget and return the response that is refused."""
    last = None
    for _ in range(LIMITS[SIGNIN].capacity + 1):
        last = await client.get("/api/auth/login", headers={"accept": accept})
    return last


async def test_a_refused_sign_in_navigation_lands_on_the_login_page(auth_env) -> None:
    """⭐ A JSON body is the end of the road for a NAVIGATION. The browser is
    sent here by `location.replace`, so a refusal renders as raw text on a page
    with no way back — which is what a locked-out operator actually saw. The
    login page can say it in words and offer its button."""
    from venona.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://t"
    ) as client:
        last = await _exhaust_signin(client, accept="text/html,application/xhtml+xml")

    assert last is not None and last.status_code == 302
    assert last.headers["location"].startswith("/login?error=")
    # `?error=` is also what stops the page starting another round trip off the
    # back of this one: `LoginView` renders rather than bounces when it sees one.
    assert "sign-in" in last.headers["location"]
    assert int(last.headers["retry-after"]) >= 1


async def test_a_refused_sign_in_fetch_still_gets_the_error(auth_env) -> None:
    """The redirect is for browsers that asked for a page. Anything else — a
    fetch, a probe, an agent — needs the refusal itself, and a 302 into HTML
    would reach it as a parse error."""
    from venona.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://t"
    ) as client:
        last = await _exhaust_signin(client, accept="application/json")

    assert last is not None and last.status_code == 429
    assert last.json()["detail"]["error"] == "rate_limited"


async def test_every_refusal_is_logged(auth_env, caplog) -> None:
    """⭐ The one refusal in this middleware that leaves no account of itself.
    The person it locks out is locked out of the surface they would diagnose it
    from, so the log line is the whole story: which bucket, and whether the key
    was their own address or the `*` that means somebody else spent it."""
    import logging

    from venona.main import app

    with caplog.at_level(logging.WARNING, logger="venona.api.ratelimit"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://t"
        ) as client:
            for _ in range(LIMITS[LOGIN].capacity + 1):
                await client.post("/api/auth/token-login", json={"token": "wrong"})

    refusals = [r.getMessage() for r in caplog.records if "rate limited" in r.getMessage()]
    assert refusals, "a 429 was written with nothing in the log to say so"
    assert f"bucket={LOGIN}" in refusals[0]
    assert "path=/api/auth/token-login" in refusals[0]


# ── the per-user write bucket ────────────────────────────────────────
#
# The bucket that is NOT keyed on the address. Everything above this line is,
# and `_client_key` is the reason: it charges the edge-observed address, which is
# the one key that shifts when a signed-in caller walks to a different network.


@pytest.fixture
def two_users(monkeypatch, tmp_path, database_url: str):
    """Auth on, two admins with rows, the app pointed at this test's database.

    Two ADMINS rather than an admin and a reader: `role_for_request` defaults an
    unrecognised route to admin, and these tests POST to a path that does not
    exist precisely so that no handler runs. A reader would be refused by the
    role check one line above the bucket under test, and the test would pass
    without ever reaching it.
    """
    import importlib

    from venona.api import authz
    from venona.api.middleware import AuthMiddleware
    from venona.db import core as db
    from venona.services import user_service
    from venona.settings import get_settings

    ratelimit.reset()
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", SECRET)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", TOKEN)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", "breakglass@example.com")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VENONA_DATABASE_URL", database_url)
    security.get_auth_config.cache_clear()
    get_settings.cache_clear()

    conn = db.connect(database_url)
    try:
        for ident, email in (("user:one", "one@example.com"), ("user:two", "two@example.com")):
            user_service.create(
                conn,
                user_id=ident,
                email=email,
                role=authz.ADMIN,
                now="2026-08-17T00:00:00Z",
                allow_admin=True,
            )
    finally:
        conn.close()

    monkeypatch.setattr(AuthMiddleware, "_database_url", database_url, raising=False)
    import venona.main as main

    importlib.reload(main)
    main.app.state.settings = get_settings()
    yield main.app
    security.get_auth_config.cache_clear()
    get_settings.cache_clear()


def _cookie_for(email: str) -> dict[str, str]:
    cfg = security.get_auth_config()
    token, _ = security.mint_session(cfg, email=email, now=1_800_000_000.0)
    return {security.COOKIE_NAME: token}


async def _write(app, *, cookies=None, headers=None, address="1.1.1.1") -> int:
    """One charged write, from a named address.

    The path deliberately does not exist: the limiter, the auth check and the
    role check all run BEFORE routing, so a 404 means every gate under test was
    passed rather than that nothing happened. `_client_key` is patched per call
    because `httpx.ASGITransport` has one client address and the whole point of
    this bucket is what happens when that changes.
    """
    from venona.api import middleware

    original = middleware._client_key
    middleware._client_key = lambda scope, **_: address  # noqa: SLF001 - see docstring
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://t",
            cookies=cookies or {},
        ) as client:
            response = await client.post("/api/no-such-route", json={}, headers=headers or {})
            return response.status_code
    finally:
        middleware._client_key = original


async def test_a_write_budget_follows_the_person_across_addresses(two_users) -> None:
    """⭐ The defect this bucket exists for. Every other bucket is per-address,
    so a laptop moving between a hotspot and an office had a fresh budget at
    each -- and "the same person from somewhere else" is the cheapest thing in
    the world to arrange."""
    cookies = _cookie_for("one@example.com")
    capacity = LIMITS[ratelimit.USER_WRITE].capacity

    # A different address every time, so the per-IP bucket is always fresh and
    # anything that refuses can only be the per-user one.
    statuses = [
        await _write(two_users, cookies=cookies, address=f"10.0.0.{i % 250}")
        for i in range(capacity + 2)
    ]

    assert statuses[0] == 404, "the request must reach routing, not stop at a gate"
    assert statuses[-1] == 429, "the same person, from anywhere, shares one write budget"


async def test_one_persons_exhausted_budget_does_not_refuse_another(two_users) -> None:
    """The failure a per-user bucket introduces if it is keyed carelessly: one
    busy account locking out everyone else is worse than the problem it fixes."""
    capacity = LIMITS[ratelimit.USER_WRITE].capacity
    for i in range(capacity + 2):
        await _write(two_users, cookies=_cookie_for("one@example.com"), address=f"10.0.1.{i % 250}")

    assert (
        await _write(two_users, cookies=_cookie_for("one@example.com"), address="10.0.2.1") == 429
    )
    assert (
        await _write(two_users, cookies=_cookie_for("two@example.com"), address="10.0.2.2") == 404
    )


async def test_the_break_glass_admin_is_not_charged_to_a_shared_bucket(two_users) -> None:
    """`user_id` has a THIRD state, which `token_id` does not: the env-listed
    admin has no row and carries `""`.

    `if principal.user_id is not None` -- the obvious mirror of the token check
    one block up -- would charge every such principal to one bucket keyed on the
    empty string. That is a shared budget for the credential that exists for when
    everything else is broken.
    """
    await _write(two_users, cookies=_cookie_for("breakglass@example.com"))

    assert (ratelimit.USER_WRITE, "") not in ratelimit.limiter._buckets, (
        "the env break-glass admin was charged to a bucket keyed on the empty string"
    )


async def test_the_root_bearer_is_not_charged_a_per_user_bucket(two_users) -> None:
    """It has no user row either, and it is the credential the runbook reaches
    for when the database is the thing you are worried about."""
    await _write(two_users, headers={"authorization": f"Bearer {TOKEN}"})

    charged = [key for bucket, key in ratelimit.limiter._buckets if bucket == ratelimit.USER_WRITE]
    assert not charged, f"root bearer charged a per-user bucket: {charged}"


async def test_a_write_charges_the_global_write_backstop(two_users) -> None:
    """WRITE is in GLOBAL_BACKSTOP, so a write charges the aggregate "*" ceiling
    on the way through. The root bearer has no user row, which isolates the
    pre-auth WRITE bucket and its backstop from the per-user one."""
    await _write(two_users, headers={"authorization": f"Bearer {TOKEN}"})

    assert (GLOBAL_BACKSTOP[WRITE], "*") in ratelimit.limiter._buckets, (
        "a write did not charge the global-write backstop"
    )


async def test_a_read_is_not_charged_the_write_budget(two_users) -> None:
    """Scoped to WRITE, which is the whole reason this can be as tight as it is.
    `API` is 600/60s because one tab polling health during a light run is ~40 a
    minute on its own."""
    cookies = _cookie_for("one@example.com")
    from venona.api import middleware

    original = middleware._client_key
    middleware._client_key = lambda scope, **_: "10.0.3.1"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=two_users), base_url="https://t", cookies=cookies
        ) as client:
            for _ in range(LIMITS[ratelimit.USER_WRITE].capacity + 2):
                await client.get("/api/no-such-route")
    finally:
        middleware._client_key = original

    charged = [key for bucket, key in ratelimit.limiter._buckets if bucket == ratelimit.USER_WRITE]
    assert not charged, f"reads charged the write budget: {charged}"
