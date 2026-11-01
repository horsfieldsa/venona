"""What venona refuses to fetch.

⚠️ READ `sources/urlguard.py`'s docstring before extending this. The control
has two halves: `check_static` at parse time (scheme, userinfo, port, IP
literals -- no DNS, so config saves stay off the network) and
`check_resolved` at connect time, where `HttpClient` vets every DNS answer
and then dials the vetted address itself (resolve-and-pin). The pin matters
as much as the check: these URLs come from a model's search results, so they
point at domains someone OWNS, and a check whose answer the transport
re-resolves is a rebinding window, not a control.
"""

from __future__ import annotations

import ipaddress

import pytest
from venona.sources.urlguard import BlockedUrl, check_resolved, check_static

PUBLIC_IP = ipaddress.ip_address("93.184.216.34")


async def _public_resolve(host: str) -> list:
    return [PUBLIC_IP]


def test_an_ordinary_url_passes() -> None:
    parsed = check_static("https://breakingdefense.com/feed/")
    assert parsed.hostname == "breakingdefense.com"


def test_a_url_with_an_explicit_allowed_port_passes() -> None:
    check_static("https://example.com:443/feed")
    check_static("http://example.com:80/feed")


# ── schemes ──────────────────────────────────────────────────────────


@pytest.mark.negative
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/x",
        "data:text/html,<script>",
        "jar:https://example.com/!/",
    ],
)
def test_non_http_schemes_are_refused(url: str) -> None:
    with pytest.raises(BlockedUrl, match="scheme"):
        check_static(url)


# ── the local network ────────────────────────────────────────────────


@pytest.mark.negative
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:80/api/health",
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://100.64.0.1/",  # CGNAT -- used internally by many VPS providers
        "http://169.254.169.254/",  # cloud metadata: credentials
        "http://[::ffff:127.0.0.1]/",  # loopback wearing a v6 hat
    ],
)
def test_ip_literals_pointing_inward_are_refused(url: str) -> None:
    with pytest.raises(BlockedUrl, match="routable"):
        check_static(url)


def test_the_metadata_address_is_the_one_that_matters_on_a_vps() -> None:
    """`169.254.169.254` returns instance credentials on every major cloud, and
    the fetched body is surfaced back through the API -- so this would be a
    read-capable SSRF, not a blind one."""
    with pytest.raises(BlockedUrl):
        check_static("http://169.254.169.254/latest/meta-data/iam/security-credentials/")


def test_venonas_own_api_is_refused() -> None:
    """The app has ~30 write endpoints and, from inside the container, no auth
    header is needed to reach them if something can make the server call
    itself."""
    with pytest.raises(BlockedUrl):
        check_static("http://127.0.0.1:8000/api/curation/rebuild")


def test_a_public_ip_literal_still_passes() -> None:
    """The guard rejects a RANGE, not the practice of using an address."""
    check_static("http://93.184.216.34/")


# ── parser confusion ─────────────────────────────────────────────────


@pytest.mark.negative
def test_credentials_in_the_authority_are_refused() -> None:
    """`https://trusted.example.com@evil.example.com/` -- a reader sees the
    first host, the client connects to the second. venona has no use for HTTP
    userinfo at all, so refusing it costs nothing."""
    with pytest.raises(BlockedUrl, match="credentials"):
        check_static("https://trusted.example.com@evil.example.com/")


@pytest.mark.negative
def test_an_unusual_port_is_refused() -> None:
    with pytest.raises(BlockedUrl, match="port"):
        check_static("http://example.com:7687/")  # neo4j bolt


@pytest.mark.negative
def test_a_url_with_no_host_is_refused() -> None:
    with pytest.raises(BlockedUrl, match="no host"):
        check_static("http:///just/a/path")


# ── the fetch path ───────────────────────────────────────────────────


async def test_the_client_refuses_a_blocked_url_before_connecting() -> None:
    import httpx
    from venona.sources.http import HttpClient

    attempted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request)
        return httpx.Response(200, text="should never happen")

    client = HttpClient(transport=httpx.MockTransport(handler), resolve=_public_resolve)

    with pytest.raises(BlockedUrl):
        await client.get("http://169.254.169.254/latest/meta-data/")

    assert not attempted, "the request must not be issued at all"


async def test_a_redirect_into_the_local_network_is_refused(monkeypatch) -> None:
    """⭐ Why redirects are followed by hand.

    httpx would follow this INSIDE the transport, where no guard can see the
    destination -- so a public URL that 302s to the metadata service would
    succeed, and the guard on the original URL would have been theatre.
    """
    import httpx
    from venona.sources.http import HttpClient

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(request.headers["host"] + request.url.path)
        if request.headers["host"] == "example.com":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/"})
        return httpx.Response(200, text="credentials")

    client = HttpClient(transport=httpx.MockTransport(handler), resolve=_public_resolve)

    with pytest.raises(BlockedUrl):
        await client.get("https://example.com/innocent")

    assert reached == ["example.com/innocent"], "the second hop must not be made"


async def test_a_hostname_resolving_inward_is_refused(monkeypatch) -> None:
    """The hosted posture: no NET_ADMIN, no egress firewall, and the private
    network resolves sibling services by name -- so a hostname whose DNS
    answer is private must be refused HERE, before any connection."""
    import httpx
    from venona.sources.http import HttpClient

    attempted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request)
        return httpx.Response(200, text="should never happen")

    async def inward(host: str) -> list:
        return [ipaddress.ip_address("10.116.0.3")]

    client = HttpClient(transport=httpx.MockTransport(handler), resolve=inward)

    with pytest.raises(BlockedUrl, match="resolves to"):
        await client.get("https://internal-service.example/")

    assert not attempted, "the request must not be issued at all"


async def test_a_split_dns_answer_is_refused(monkeypatch) -> None:
    """One public answer does not launder one private one: ANY blocked address
    in the answer set refuses the whole fetch, fail-closed."""
    import httpx
    from venona.sources.http import HttpClient

    attempted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request)
        return httpx.Response(200, text="should never happen")

    async def split(host: str) -> list:
        return [PUBLIC_IP, ipaddress.ip_address("192.168.1.10")]

    client = HttpClient(transport=httpx.MockTransport(handler), resolve=split)

    with pytest.raises(BlockedUrl, match="resolves to"):
        await client.get("https://split-horizon.example/")

    assert not attempted


async def test_a_redirect_to_a_name_resolving_inward_is_refused(monkeypatch) -> None:
    """Every hop is resolved and vetted, not just the first: a public page
    that 302s to an internal NAME (not literal) must die at the second hop."""
    import httpx
    from venona.sources.http import HttpClient

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)

    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(request.headers["host"])
        return httpx.Response(302, headers={"Location": "https://neo4j.internal/creds"})

    async def horizon(host: str) -> list:
        if host == "example.com":
            return [PUBLIC_IP]
        return [ipaddress.ip_address("10.116.0.3")]

    client = HttpClient(transport=httpx.MockTransport(handler), resolve=horizon)

    with pytest.raises(BlockedUrl, match="resolves to"):
        await client.get("https://example.com/innocent")

    assert reached == ["example.com"], "the internal name must never be dialled"


# ── check_resolved itself ────────────────────────────────────────────


@pytest.mark.negative
@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.5",
        "127.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "::1",
        "::ffff:127.0.0.1",  # loopback wearing a v6 hat
        "0.0.0.0",
    ],
)
def test_resolved_inward_addresses_are_refused(address: str) -> None:
    with pytest.raises(BlockedUrl, match="resolves to"):
        check_resolved("https://example.com/", [ipaddress.ip_address(address)])


def test_resolved_public_addresses_pass() -> None:
    check_resolved(
        "https://example.com/",
        [PUBLIC_IP, ipaddress.ip_address("2606:2800:21f:cb07:6820:80da:af6b:8b2c")],
    )


# ── sources.yaml ─────────────────────────────────────────────────────


@pytest.mark.negative
def test_a_source_url_must_be_https() -> None:
    """A feed fetched over plain http can be rewritten in flight by anything on
    the path, which for a document set whose whole value is provenance is not a
    trade worth making. All fifteen live entries already are."""
    from pydantic import ValidationError
    from venona.sources.base import SourceSpec

    with pytest.raises(ValidationError):
        SourceSpec(id="x", kind="rss", url="http://example.com/feed")


@pytest.mark.negative
def test_a_source_url_cannot_point_inward() -> None:
    from pydantic import ValidationError
    from venona.sources.base import SourceSpec

    with pytest.raises(ValidationError):
        SourceSpec(id="x", kind="rss", url="https://127.0.0.1/feed")


def test_every_shipped_source_still_validates() -> None:
    """The rule breaks nothing today. If this fails, the rule changed and a real
    feed is now unfetchable -- which is a decision, not a detail."""
    from venona.settings import get_settings
    from venona.sources.registry import load_sources

    specs = load_sources(get_settings().config_dir)
    assert specs, "no sources loaded -- did config_dir move?"
