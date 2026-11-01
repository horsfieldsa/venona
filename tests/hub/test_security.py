"""Sessions, state cookies, and the token helpers."""

from __future__ import annotations

import pytest
from hub.config import Settings
from hub.middleware import SecurityHeadersMiddleware
from hubmaterial import TEST_SEAL_KEY, TEST_SIGNING_KEY
from starlette.requests import Request

from hub import ratelimit, security


def _settings(**overrides) -> Settings:
    values = dict(
        _env_file=None,
        public_url="https://hub.test",
        session_secret="s" * 48,
        seal_key=TEST_SEAL_KEY,
        admin_token="a" * 40,
        oidc_signing_key=TEST_SIGNING_KEY,
        google_client_id="cid",
        google_client_secret="cs",
    )
    values.update(overrides)
    return Settings(**values)


def test_session_round_trip_and_expiry() -> None:
    settings = _settings()
    token, expires = security.mint_session(settings, email="a@example.com", now=1_000_000.0)
    assert expires == 1_000_000.0 + settings.session_ttl_seconds
    assert security.verify_session(settings, token, now=1_000_000.0 + 5) == "a@example.com"
    assert security.verify_session(settings, token, now=expires + 1) is None


def test_session_tamper_and_wrong_secret_refused() -> None:
    settings = _settings()
    token, _ = security.mint_session(settings, email="a@example.com", now=0.0)
    body, sig = token.split(".")
    assert security.verify_session(settings, f"{body}x.{sig}", now=1.0) is None
    assert security.verify_session(settings, f"{body}.{sig}x", now=1.0) is None
    other = _settings(session_secret="t" * 48)
    assert security.verify_session(other, token, now=1.0) is None
    assert security.verify_session(settings, None, now=1.0) is None
    assert security.verify_session(settings, "garbage", now=1.0) is None


def test_state_cookie_round_trip() -> None:
    settings = _settings()
    cookie, state, nonce = security.mint_state(settings, now=100.0, next_path="/chooser?x=1")
    claims = security.verify_state(settings, cookie, state=state, now=200.0)
    assert claims is not None
    assert claims["nonce"] == nonce
    assert claims["next"] == "/chooser?x=1"
    # Expired, wrong state, missing cookie.
    assert security.verify_state(settings, cookie, state=state, now=100.0 + 301) is None
    assert security.verify_state(settings, cookie, state="other", now=200.0) is None
    assert security.verify_state(settings, None, state=state, now=200.0) is None


def test_safe_next_refuses_offsite_targets() -> None:
    assert security.safe_next("/instances?x=1") == "/instances?x=1"
    assert security.safe_next("https://evil.example") == "/"
    assert security.safe_next("//evil.example") == "/"
    assert security.safe_next(None) == "/"
    # An absolute URL smuggled into the state cookie is neutralised at mint time.
    settings = _settings()
    cookie, state, _ = security.mint_state(settings, now=0.0, next_path="https://evil.example")
    claims = security.verify_state(settings, cookie, state=state, now=1.0)
    assert claims is not None and claims["next"] == "/"


@pytest.mark.negative
def test_safe_next_refuses_a_backslash_or_a_control_character() -> None:
    """⭐ The hub is the origin every instance trusts, so an open redirect here
    launders a phishing page through the one hostname a reader has been taught
    to look for.

    `/\\evil.example` starts with a single slash and does not start with two, so
    the obvious check admits it — and a browser normalises the backslash to a
    slash and lands on another origin. Control characters are the same attack:
    tab, newline and carriage return are stripped before the browser parses.
    """
    for hostile in (
        "/\\evil.example",
        "/\\\\evil.example",
        "/\t/evil.example",
        "/\n//evil.example",
    ):
        assert security.safe_next(hostile) == "/", repr(hostile)
    # And the property that has to survive: a same-origin path keeps its query.
    assert security.safe_next("/chooser?next=%2Finstances") == "/chooser?next=%2Finstances"


def test_admin_token_compare() -> None:
    settings = _settings()
    assert security.admin_token_ok(settings, "a" * 40)
    assert not security.admin_token_ok(settings, "a" * 39 + "b")
    assert not security.admin_token_ok(_settings(admin_token=""), "")


def test_mgmt_token_shape() -> None:
    assert security.looks_like_mgmt_token("vmgmt_" + "0" * 64)
    assert not security.looks_like_mgmt_token("vmgmt_" + "0" * 63)
    assert not security.looks_like_mgmt_token("vmcp_" + "0" * 65)


def _request(*, xff: str | None = None, cf: str | None = None, client=("10.9.9.9", 0)) -> Request:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    if cf is not None:
        headers.append((b"cf-connecting-ip", cf.encode()))
    return Request({"type": "http", "headers": headers, "client": client})


def test_client_key_charges_the_visitor_not_the_platform() -> None:
    """⭐ The request-access form allows five an hour per key, so the key decides
    whether that is per visitor or for the whole internet at once. The socket
    peer is the platform's router behind Cloudflare and the reverse proxy behind the reverse proxy —
    never the visitor — so a forwarded chain must always outrank it."""
    edge = _request(xff="9.9.9.9, 172.71.0.5", cf="203.0.113.7")
    assert ratelimit.client_key(edge, edge=True) == "203.0.113.7"
    assert ratelimit.client_key(edge, edge=True) != "10.9.9.9"
    # Two visitors arriving through one shared platform hop stay separate.
    other = _request(xff="9.9.9.9, 172.71.0.5", cf="198.51.100.4")
    assert ratelimit.client_key(other, edge=True) != ratelimit.client_key(edge, edge=True)


def test_client_key_ignores_cloudflare_off_the_edge_path() -> None:
    """Off the edge path nothing overwrites the header, so a submitter could
    write one per request and mint a fresh bucket each time."""
    forged = _request(xff="9.9.9.9, 203.0.113.7", cf="1.1.1.1")
    assert ratelimit.client_key(forged, edge=False) == "203.0.113.7"
    assert ratelimit.client_key(_request(client=("192.0.2.50", 0)), edge=False) == "192.0.2.50"


# ── the content security policy ──────────────────────────────────────


def _directives() -> dict[str, str]:
    csp = SecurityHeadersMiddleware.EDGE_HEADERS[b"content-security-policy"].decode()
    return dict(
        (part.split(" ", 1) + [""])[:2] for part in (p.strip() for p in csp.split(";")) if part
    )


def test_the_csp_allows_scripts_only_from_this_origin() -> None:
    """⭐ The hub is the OIDC issuer, and its session cookie is the SSO master
    key for every instance, so what may execute here is the one posture worth
    pinning in a test rather than trusting to review.

    Two things run: the graph field's canvas on the public pages (ADR 0202) and
    the operator bundle behind the session gate (ADR 0223). Both are generated
    into `static/` and served from this origin. Each name below is one that
    would have to be added before an injected script could run, and none of them
    is worth either feature.
    """
    directives = _directives()
    assert directives["script-src"] == "'self'"
    assert "'unsafe-inline'" not in directives["script-src"]
    assert "'unsafe-eval'" not in directives["script-src"]
    assert directives["default-src"] == "'none'"
    # NO ORIGIN OUTSIDE THIS ONE ON ANY DIRECTIVE THAT LOADS OR EXECUTES.
    #
    # `form-action` is the sole exception and it is named rather than skipped,
    # because it is the one directive that governs where the reader GOES rather
    # than what this page may run. Nothing on it can execute here, and the
    # paywall cannot work without it: a form submission's redirect chain is
    # checked against this list, so `'self'` alone meant the trial button
    # created a Checkout Session and then went nowhere (2026-09-11).
    allowed_hosts = {"https://checkout.stripe.com", "https://billing.stripe.com"}
    assert set(directives["form-action"].split()) == {"'self'"} | allowed_hosts
    for name, value in directives.items():
        if name == "form-action":
            continue
        assert "http://" not in value and "https://" not in value, (
            f"{name} names an origin outside this one. Only `form-action` may, and only "
            f"because nothing it allows can execute here."
        )


def test_the_csp_admits_the_operator_bundle_and_nothing_further() -> None:
    """⭐ The three directives the operator surface needs, each pinned to
    `'self'` so widening one is a deliberate diff.

    `connect-src` is the one whose absence does not look like a security
    setting: with no such directive it falls to `default-src 'none'` and every
    fetch the bundle makes is refused, so the page renders and then does
    nothing. `style-src` keeps `'unsafe-inline'` for the field's per-page block,
    which is why it is the one directive here with two sources.
    """
    directives = _directives()
    assert directives["connect-src"] == "'self'"
    assert directives["font-src"] == "'self'"
    assert directives["style-src"] == "'self' 'unsafe-inline'"


async def test_the_served_policy_carries_the_operator_directives(hub_settings) -> None:
    """⭐ The directives above, on a real response rather than on the constant.

    `main.py` installs the headers with `edge=settings.edge_headers` and
    `make hub-dev` runs off-edge, so a missing directive is invisible in local
    verification and appears only on example.com. Every assertion about this
    policy has to go through an app built with the flag on, which is what this
    one does and why it is not simply reading `EDGE_HEADERS` again.
    """
    import httpx
    from hub.main import build_app

    app = build_app(hub_settings.model_copy(update={"edge_headers": True}))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as client:
        response = await client.get("/healthz")
    served = response.headers["content-security-policy"]
    for directive in ("connect-src 'self'", "font-src 'self'", "style-src 'self' 'unsafe-inline'"):
        assert directive in served, directive
