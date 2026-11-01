"""`HttpClient`, against a scripted transport.

NONE OF THIS COULD BE TESTED BEFORE. `build_http_client` returns a
`ReplayHttpClient` when offline, and that is a separate implementation reading a
fixture index -- so the offline suite never constructed `HttpClient` at all.
Pacing, retry, backoff, redirect handling and the conditional-GET interaction
were exercised only by a live forty-minute run, which is also the thing CLAUDE.md
forbids editing `.py` files during. That is why the `transport=` parameter
exists.
"""

from __future__ import annotations

import ipaddress

import httpx
import pytest
from venona.sources.http import MAX_REDIRECTS, MAX_RETRIES, HttpClient
from venona.sources.urlguard import BlockedUrl

# What the fake resolver answers for every hostname. A real public address:
# the TEST-NET documentation ranges are marked private by `ipaddress` and
# would be refused by the very guard under test.
PINNED = "93.184.216.34"


async def _fake_resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    return [ipaddress.ip_address(PINNED)]


class _Script:
    """A scripted server. Records what it was asked, replies from a queue."""

    def __init__(self, *replies: httpx.Response | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else httpx.Response(200, text="fallback")
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def count(self) -> int:
        return len(self.requests)


def _client(script: _Script) -> HttpClient:
    class _Zero:
        """No jitter, so a test does not sleep for real."""

        def random(self) -> float:
            return 0.0

    # The fake resolver is not optional: the default resolver does real DNS,
    # and `example.com` fixtures would turn this offline suite into one that
    # quietly hits the network.
    client = HttpClient(transport=httpx.MockTransport(script), rng=_Zero(), resolve=_fake_resolve)
    # The per-host delay is a politeness constant, not behaviour under test.
    client._last_hit.default_factory = lambda: 0.0  # type: ignore[attr-defined]
    return client


# ── the dead retry ───────────────────────────────────────────────────


async def test_a_conditional_request_still_retries(monkeypatch) -> None:
    """⭐ THE BUG.

    The guard read `if raw.status_code in RETRY_STATUSES and not headers`, and
    `not headers` is False for every feed that has ever succeeded -- because
    `_collect_one` passes the stored ETag on every subsequent fetch. So the
    retry only fired on a source's first-ever request. In normal operation it
    was dead code.

    The comment justified exempting a 304 ("a 304 is the answer, not a
    refusal"), which is correct, but 304 is not in RETRY_STATUSES -- so the
    guard protected nothing and cost everything.
    """
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(httpx.Response(503), httpx.Response(200, text="the feed"))

    response = await _client(script).get("https://example.com/feed", etag='W/"abc"')

    assert script.count == 2, "a conditional GET must still retry a 503"
    assert response.ok
    assert response.text == "the feed"


async def test_the_conditional_headers_are_actually_sent(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(httpx.Response(304))

    await _client(script).get(
        "https://example.com/feed", etag='W/"abc"', last_modified="Mon, 01 Jan 2026 00:00:00 GMT"
    )

    sent = script.requests[0].headers
    assert sent["if-none-match"] == 'W/"abc"'
    assert sent["if-modified-since"] == "Mon, 01 Jan 2026 00:00:00 GMT"


async def test_a_304_is_not_retried(monkeypatch) -> None:
    """It is the answer, not a refusal -- and the main reason a daily run over
    the same feeds stays cheap."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(httpx.Response(304))

    response = await _client(script).get("https://example.com/feed", etag='W/"abc"')

    assert script.count == 1
    assert response.not_modified


# ── retrying what used to become a strike ────────────────────────────


async def test_a_connect_error_is_retried(monkeypatch) -> None:
    """These went straight to `collect_rss`'s `except Exception` and earned a
    strike. At STRIKE_LIMIT=3 that auto-disables a healthy feed after three
    transient blips on three consecutive nights -- and it stays off until
    someone runs `venona sources --enable`."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(httpx.ConnectError("refused"), httpx.Response(200, text="ok"))

    response = await _client(script).get("https://example.com/feed")

    assert script.count == 2
    assert response.ok


async def test_a_read_timeout_is_retried(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(httpx.ReadTimeout("slow"), httpx.Response(200, text="ok"))

    assert (await _client(script).get("https://example.com/x")).ok
    assert script.count == 2


async def test_a_500_is_retried(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(httpx.Response(500), httpx.Response(200, text="ok"))

    assert (await _client(script).get("https://example.com/x")).ok


@pytest.mark.negative
async def test_a_404_is_not_retried(monkeypatch) -> None:
    """Retrying a 404 is just a slower 404."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(httpx.Response(404))

    response = await _client(script).get("https://example.com/gone")

    assert script.count == 1
    assert response.status == 404


async def test_retries_are_bounded(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(*[httpx.Response(503) for _ in range(10)])

    response = await _client(script).get("https://example.com/x")

    assert script.count == MAX_RETRIES
    assert response.status == 503


async def test_a_persistent_connect_error_still_raises(monkeypatch) -> None:
    """Retrying must not swallow a genuine outage -- `collect_rss` needs the
    exception to record a strike."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(*[httpx.ConnectError("refused") for _ in range(10)])

    with pytest.raises(httpx.TransportError):
        await _client(script).get("https://example.com/x")


# ── backoff ──────────────────────────────────────────────────────────


async def test_retry_after_is_honoured(monkeypatch) -> None:
    """A 429 saying 120 got a flat 1.5-second retry before this, which is not a
    retry -- it is a second refusal."""
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("asyncio.sleep", record)
    script = _Script(
        httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, text="ok")
    )

    await _client(script).get("https://example.com/x")

    assert 7.0 in slept


async def test_an_absurd_retry_after_is_capped(monkeypatch) -> None:
    """A CDN that says an hour is not going to be waited for inside a run."""
    from venona.sources.http import MAX_BACKOFF

    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("asyncio.sleep", record)
    script = _Script(
        httpx.Response(503, headers={"Retry-After": "3600"}), httpx.Response(200, text="ok")
    )

    await _client(script).get("https://example.com/x")

    assert max(slept) <= MAX_BACKOFF


async def test_backoff_grows(monkeypatch) -> None:
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("asyncio.sleep", record)
    script = _Script(*[httpx.Response(503) for _ in range(10)])

    await _client(script).get("https://example.com/x")

    assert len(slept) >= 2
    assert slept[1] > slept[0]


# ── redirects ────────────────────────────────────────────────────────


async def test_redirects_are_followed(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(
        httpx.Response(301, headers={"Location": "https://example.com/final"}),
        httpx.Response(200, text="arrived"),
    )

    response = await _client(script).get("https://example.com/start")

    assert response.ok
    assert response.text == "arrived"
    # Under the pin the wire URL carries the resolved IP; the hostname rides
    # in the Host header.
    assert str(script.requests[-1].url) == f"https://{PINNED}/final"
    assert script.requests[-1].headers["host"] == "example.com"


async def test_a_relative_redirect_resolves(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(
        httpx.Response(302, headers={"Location": "/elsewhere"}),
        httpx.Response(200, text="arrived"),
    )

    await _client(script).get("https://example.com/deep/path")

    assert str(script.requests[-1].url) == f"https://{PINNED}/elsewhere"
    assert script.requests[-1].headers["host"] == "example.com"


async def test_a_redirect_loop_is_refused(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(
        *[httpx.Response(302, headers={"Location": "https://example.com/loop"}) for _ in range(20)]
    )

    with pytest.raises(httpx.TooManyRedirects):
        await _client(script).get("https://example.com/loop")

    assert script.count <= MAX_REDIRECTS


async def test_the_reported_url_is_the_final_one(monkeypatch) -> None:
    """`normalize` and the recorder both key on this. Reporting the original
    would attribute the document to a URL that does not serve it."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(
        httpx.Response(308, headers={"Location": "https://example.com/canonical"}),
        httpx.Response(200, text="body"),
    )

    response = await _client(script).get("https://example.com/old")

    assert response.url == "https://example.com/canonical"


# ── deadlock ─────────────────────────────────────────────────────────


async def test_cross_host_redirects_do_not_deadlock(monkeypatch) -> None:
    """⭐ The hazard the manual redirect loop introduces.

    Per-hop pacing invites nesting one host lock inside another, and two
    concurrent fetches A->B and B->A then deadlock on the inversion. That is
    trivially reachable: `try_candidates` walks a list of model-supplied URLs.
    `asyncio.Lock` has no timeout, so the run would hang forever holding its
    lane slot.
    """
    import asyncio

    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        # The Host header, not `request.url.host` -- the wire URL is the
        # pinned IP for every hostname.
        host = request.headers["host"]
        if request.url.path == "/hop":
            other = "b.example.com" if host == "a.example.com" else "a.example.com"
            return httpx.Response(302, headers={"Location": f"https://{other}/done"})
        return httpx.Response(200, text=f"done at {host}")

    client = _client(_Script())
    client._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler), follow_redirects=False
    )

    results = await asyncio.wait_for(
        asyncio.gather(
            client.get("https://a.example.com/hop"),
            client.get("https://b.example.com/hop"),
        ),
        timeout=5.0,
    )

    assert all(r.ok for r in results)


# ── resolve-and-pin ──────────────────────────────────────────────────


async def test_the_dial_is_pinned_to_the_vetted_address(monkeypatch) -> None:
    """⭐ The rebinding defence.

    A guard that resolves, approves, and then lets the transport resolve AGAIN
    hands the record's owner a window: answer public to the check, private to
    the connection. The pin closes it -- the request must dial exactly the
    address the resolver returned, with the hostname carried in Host and SNI.
    """
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(httpx.Response(200, text="ok"))

    await _client(script).get("https://example.com/feed")

    sent = script.requests[0]
    assert sent.url.host == PINNED
    assert sent.headers["host"] == "example.com"
    assert sent.extensions["sni_hostname"] == "example.com"


async def test_a_rebinding_resolver_cannot_win(monkeypatch) -> None:
    """Even a resolver that answers public-then-private is never consulted
    between the check and the connect -- the connect uses the checked answer."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    answers = [[ipaddress.ip_address(PINNED)], [ipaddress.ip_address("10.0.0.5")]]

    async def rebinding(host: str) -> list:
        return answers.pop(0) if answers else [ipaddress.ip_address("10.0.0.5")]

    script = _Script(httpx.Response(200, text="ok"))
    client = HttpClient(transport=httpx.MockTransport(script), resolve=rebinding)
    client._last_hit.default_factory = lambda: 0.0  # type: ignore[attr-defined]

    response = await client.get("https://example.com/feed")

    assert response.ok
    assert script.requests[0].url.host == PINNED, "must dial the vetted answer, not re-resolve"


async def test_an_ip_literal_is_dialled_as_itself(monkeypatch) -> None:
    """No pin, no Host override, no SNI extension: `check_static` already
    vetted the literal, and the certificate is verified against its IP SAN."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(httpx.Response(200, text="ok"))

    async def exploding_resolve(host: str) -> list:
        raise AssertionError("an IP literal must not be resolved")

    client = HttpClient(transport=httpx.MockTransport(script), resolve=exploding_resolve)
    client._last_hit.default_factory = lambda: 0.0  # type: ignore[attr-defined]

    await client.get(f"http://{PINNED}/feed")

    sent = script.requests[0]
    assert sent.url.host == PINNED
    assert "sni_hostname" not in sent.extensions


async def test_a_resolution_failure_is_retried_not_blocked(monkeypatch) -> None:
    """A transient NXDOMAIN must earn the retry-then-strike treatment of any
    other transport failure. Raising `BlockedUrl` instead would stop it
    counting toward the source's strikes -- forever, silently."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    calls: list[str] = []

    async def flaky(host: str) -> list:
        calls.append(host)
        if len(calls) == 1:
            raise httpx.ConnectError(f"could not resolve {host}")
        return [ipaddress.ip_address(PINNED)]

    script = _Script(httpx.Response(200, text="ok"))
    client = HttpClient(transport=httpx.MockTransport(script), resolve=flaky)
    client._last_hit.default_factory = lambda: 0.0  # type: ignore[attr-defined]

    response = await client.get("https://example.com/feed")

    assert response.ok
    assert len(calls) == 2, "the failed resolution must be retried"


async def test_retries_rotate_through_the_vetted_answers(monkeypatch) -> None:
    """One dead address in a multi-A record must not fail a host that worked
    under happy-eyeballs: attempt two dials answer two."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    second_ip = "93.184.216.35"

    async def two_answers(host: str) -> list:
        return [ipaddress.ip_address(PINNED), ipaddress.ip_address(second_ip)]

    script = _Script(httpx.ConnectError("dead address"), httpx.Response(200, text="ok"))
    client = HttpClient(transport=httpx.MockTransport(script), resolve=two_answers)
    client._last_hit.default_factory = lambda: 0.0  # type: ignore[attr-defined]

    response = await client.get("https://example.com/feed")

    assert response.ok
    assert script.requests[0].url.host == PINNED
    assert script.requests[1].url.host == second_ip


async def test_a_retry_never_crosses_into_an_unroutable_family(monkeypatch) -> None:
    """The rotation stays in the family the first attempt used.

    Measured on production. `media.defense.gov` answers with one A and two
    AAAA, so `answers[attempt % len(answers)]` pinned IPv6 on both retries; on
    a host with no v6 route each was dead before it was made, and the caller
    was told `ConnectError: All connection attempts failed` about an origin
    that had answered 403. This test is the reason that shape is checked at
    all: the rotation test above uses two IPv4 addresses and passes either way.
    """
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    second_v4 = "93.184.216.35"

    async def one_v4_two_v6(host: str) -> list:
        return [
            ipaddress.ip_address(PINNED),
            ipaddress.ip_address(second_v4),
            ipaddress.ip_address("2600:1409:9800:1a8d::3f78"),
            ipaddress.ip_address("2600:1409:9800:1a85::3f78"),
        ]

    script = _Script(
        httpx.Response(503, text="busy"),
        httpx.Response(503, text="busy"),
        httpx.Response(200, text="ok"),
    )
    client = HttpClient(transport=httpx.MockTransport(script), resolve=one_v4_two_v6)
    client._last_hit.default_factory = lambda: 0.0  # type: ignore[attr-defined]

    response = await client.get("https://example.com/feed")

    assert response.ok
    dialled = [r.url.host for r in script.requests]
    assert len(dialled) == MAX_RETRIES
    for host in dialled:
        assert ipaddress.ip_address(host).version == 4, f"a retry pinned {host}"


async def test_resolution_asks_only_for_families_this_host_can_route() -> None:
    """`AI_ADDRCONFIG`, and it is load-bearing rather than tidy.

    The pin dials exactly one address, so there is no happy-eyeballs underneath
    to rescue a family the host cannot route. Without the flag a container with
    no v6 address is still handed AAAA answers, and a self-hosted deployment disables
    IPv6 at the sysctl level for self-hosted deployments -- which is why this
    only ever showed in production, where that file is not used.
    """
    import socket as socket_mod

    seen: dict = {}

    class _Loop:
        async def getaddrinfo(self, host, port, **kwargs):
            seen.update(kwargs)
            return [(socket_mod.AF_INET, socket_mod.SOCK_STREAM, 6, "", (PINNED, 0))]

    import venona.sources.http as http_mod

    original = http_mod.asyncio.get_running_loop
    http_mod.asyncio.get_running_loop = lambda: _Loop()  # type: ignore[assignment]
    try:
        answers = await http_mod.resolve_host("example.com")
    finally:
        http_mod.asyncio.get_running_loop = original  # type: ignore[assignment]

    assert answers == [ipaddress.ip_address(PINNED)]
    assert seen.get("flags", 0) & socket_mod.AI_ADDRCONFIG, "AAAA on a v4-only host is a dead pin"


async def test_a_v6_only_host_is_still_dialled(monkeypatch) -> None:
    """Preferring v4 is not refusing v6. A host with only AAAA answers has one
    routable family and the rotation uses it."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    async def v6_only(host: str) -> list:
        return [ipaddress.ip_address("2620:0:863:ed1a::1")]

    script = _Script(httpx.Response(200, text="ok"))
    client = HttpClient(transport=httpx.MockTransport(script), resolve=v6_only)
    client._last_hit.default_factory = lambda: 0.0  # type: ignore[attr-defined]

    assert (await client.get("https://example.com/feed")).ok
    assert ipaddress.ip_address(script.requests[0].url.host).version == 6


async def test_a_refusal_is_not_retried(monkeypatch) -> None:
    """A bot manager's 403 is a verdict, not a hiccup.

    No host that answered 403 across 533 measured candidate fetches ever passed
    on a retry, and the same hosts answer 403 to a domestic line as to
    production. Retrying costs three requests and three per-host delays to be
    told the same thing.
    """
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(httpx.Response(403, text="denied"), httpx.Response(200, text="ok"))
    client = HttpClient(transport=httpx.MockTransport(script), resolve=_fake_resolve)
    client._last_hit.default_factory = lambda: 0.0  # type: ignore[attr-defined]

    response = await client.get("https://example.com/feed")

    assert response.status == 403
    assert len(script.requests) == 1, "a 403 must cost one request, not three"


async def test_a_status_we_received_outranks_a_later_transport_failure(monkeypatch) -> None:
    """What the origin decided beats what happened to us afterwards.

    A retryable status followed by a transport failure reported the transport
    failure, so a 503 or a 403 the server had plainly returned reached the trace
    as `ConnectError`. That is what recorded nine days of `.gov`/`.mil`
    telemetry as unreachable hosts.
    """
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(
        httpx.Response(503, text="busy"),
        httpx.ConnectError("All connection attempts failed"),
        httpx.ConnectError("All connection attempts failed"),
    )
    client = HttpClient(transport=httpx.MockTransport(script), resolve=_fake_resolve)
    client._last_hit.default_factory = lambda: 0.0  # type: ignore[attr-defined]

    response = await client.get("https://example.com/feed")

    assert response.status == 503, "the answer we got outranks the error we met after it"


async def test_a_transport_failure_with_no_answer_still_raises(monkeypatch) -> None:
    """Keeping the last answer must not swallow a fetch that never got one."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(*[httpx.ConnectError("dead") for _ in range(MAX_RETRIES)])
    client = HttpClient(transport=httpx.MockTransport(script), resolve=_fake_resolve)
    client._last_hit.default_factory = lambda: 0.0  # type: ignore[attr-defined]

    with pytest.raises(httpx.ConnectError):
        await client.get("https://example.com/feed")


async def test_the_reported_url_never_leaks_the_pin(monkeypatch) -> None:
    """`Response.url` keys the recorder's index and `normalize.url_key_for`;
    an IP there would fork every document's identity on the next resolution."""
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    script = _Script(
        httpx.Response(308, headers={"Location": "https://example.com/canonical"}),
        httpx.Response(200, text="body"),
    )

    response = await _client(script).get("https://example.com/old")

    assert response.url == "https://example.com/canonical"
    assert PINNED not in response.url


async def _no_sleep(_seconds: float) -> None:
    return None


# ── the body ceiling ─────────────────────────────────────────────────


async def test_an_oversized_body_is_refused(monkeypatch) -> None:
    """⭐ The URL is often the MODEL's choice.

    Tier-3 grounding fetches pages the model nominated, so the size of what
    comes back is decided by whoever controls that page — and this runs inside
    the API process, where a body big enough to exhaust memory takes the whole
    server down rather than just the run.
    """
    from venona.sources import http as http_module

    monkeypatch.setattr(http_module, "MAX_RESPONSE_BYTES", 1024)
    script = _Script(httpx.Response(200, content=b"x" * 4096))
    client = _client(script)

    with pytest.raises(http_module.BodyTooLarge):
        await client.get("https://example.com/huge")

    await client.aclose()


async def test_a_decompression_bomb_is_refused(monkeypatch) -> None:
    """The ceiling is on DECODED bytes, which is the half that matters. A
    `Content-Length` check would be no defence at all — it is supplied by the
    same page — and a compressed-size check refuses at the few hundred kilobytes
    the attacker paid to send rather than at the gigabyte it expands to."""
    import gzip

    from venona.sources import http as http_module

    monkeypatch.setattr(http_module, "MAX_RESPONSE_BYTES", 1024)
    payload = gzip.compress(b"x" * 500_000)
    assert len(payload) < 1024, "the point is that the WIRE body is under the cap"

    script = _Script(httpx.Response(200, content=payload, headers={"content-encoding": "gzip"}))
    client = _client(script)

    with pytest.raises(http_module.BodyTooLarge):
        await client.get("https://example.com/bomb")

    await client.aclose()


async def test_an_ordinary_page_is_unaffected() -> None:
    """A ceiling that refuses real articles is worse than none: the whole
    corpus arrives through this method."""
    script = _Script(httpx.Response(200, text="an ordinary article body"))
    client = _client(script)

    response = await client.get("https://example.com/article")

    assert response.ok
    assert response.text == "an ordinary article body"
    await client.aclose()


async def test_a_compressed_page_reads_back_as_its_text() -> None:
    """⭐ THE REGRESSION THE BODY CEILING SHIPPED.

    `aiter_bytes` yields bytes httpx has ALREADY decoded. Re-presenting them
    under the original headers carried `Content-Encoding: gzip` forward, so the
    next read decoded them a second time and every origin serving gzip or br --
    which is nearly all of them -- failed with `DecodingError: incorrect header
    check`. Six of six sources struck on the first run after it deployed.

    The cap's own tests missed it because neither exercised the ordinary path:
    the bomb test raises BEFORE the response is rebuilt, and the plain-page test
    sends no `Content-Encoding` at all. What was never covered is the common
    case -- a compressed page, comfortably under the cap, read as text.
    """
    import gzip

    body = "An ordinary article about a programme, served compressed like everything else."
    script = _Script(
        httpx.Response(
            200,
            content=gzip.compress(body.encode()),
            headers={"content-encoding": "gzip", "content-type": "text/html"},
        )
    )
    client = _client(script)

    response = await client.get("https://example.com/gzipped")

    assert response.ok
    assert response.text == body
    await client.aclose()


async def test_a_rebuilt_response_describes_the_body_it_carries() -> None:
    """The headers must describe the DECODED bytes, because those are what is
    being handed over. `Content-Encoding` is dropped (the body is no longer
    encoded) and `Content-Length` is recomputed by httpx from the new content --
    carrying the original forward would have described the compressed size."""
    import gzip

    body = "x" * 5000
    script = _Script(
        httpx.Response(
            200,
            content=gzip.compress(body.encode()),
            headers={"content-encoding": "gzip"},
        )
    )
    client = _client(script)

    response = await client.get("https://example.com/lengths")

    assert response.text == body
    assert "content-encoding" not in response.headers
    assert response.headers.get("content-length") == str(len(body))
    await client.aclose()


# ── post_once: the outbound half ─────────────────────────────────────
#
# Every constraint here exists because a destination URL is typed by a reader
# rather than set in the environment. `docs/security/REVIEW-2026-08.md` cleared
# the old `notify.py` from SSRF on exactly that distinction, so the clearance
# does not transfer and this is what replaces it.


async def test_post_once_dials_the_address_it_vetted() -> None:
    """Resolve and pin, the same control `get` uses.

    Without the pin the destination's DNS owner has a rebinding window: vet a
    public answer, then serve a private one to the transport's own second
    lookup. A one-second TTL is three lines of config.
    """
    script = _Script(httpx.Response(200, text="ok"))
    client = _client(script)

    response = await client.post_once(
        "https://example.com/hook", content=b"body", headers={"content-type": "text/plain"}
    )

    assert response.status == 200
    request = script.requests[0]
    assert request.method == "POST"
    assert request.content == b"body"
    # The wire carries the vetted IP; the name survives in `Host` so SNI and
    # certificate verification still run against it.
    assert request.url.host == "93.184.216.34"
    assert request.headers["Host"] == "example.com"
    # And the URL reported back is the NAME, never the address that was dialled.
    assert response.url == "https://example.com/hook"
    await client.aclose()


async def test_post_once_refuses_a_private_answer() -> None:
    """Vetting is over every answer and fails closed, exactly as it does for a
    feed. A destination resolving to something internal is refused before the
    connection rather than after it."""

    async def private(_host: str):
        import ipaddress

        return [ipaddress.ip_address("127.0.0.1")]

    script = _Script(httpx.Response(200))
    client = HttpClient(transport=httpx.MockTransport(script), resolve=private)
    with pytest.raises(BlockedUrl):
        await client.post_once("https://example.com/hook", content=b"x")
    assert script.count == 0
    await client.aclose()


async def test_post_once_does_not_follow_a_redirect() -> None:
    """A feed genuinely moves; a webhook does not. An endpoint answering 302 is
    either misconfigured or steering us, and neither earns a second request.
    The status is reported as it stands and the outbox counts it a failure."""
    script = _Script(httpx.Response(302, headers={"location": "https://elsewhere.test/x"}))
    client = _client(script)

    response = await client.post_once("https://example.com/hook", content=b"x")

    assert response.status == 302
    assert script.count == 1
    await client.aclose()


async def test_post_once_does_not_retry() -> None:
    """Retrying inside the request would send the same message twice to a
    receiver that answered slowly. The outbox retries instead, and it knows not
    to duplicate the row."""
    script = _Script(httpx.Response(503), httpx.Response(200))
    client = _client(script)

    response = await client.post_once("https://example.com/hook", content=b"x")

    assert response.status == 503
    assert script.count == 1
    await client.aclose()
