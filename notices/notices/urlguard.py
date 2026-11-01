"""What venona is willing to fetch.

TWO CHECKS, AT TWO TIMES, BECAUSE NEITHER SUFFICES ALONE.

`check_static` is the parse-time half: scheme, userinfo, port, IP literals.
It runs where the network must not be touched -- inside
`PUT /api/config/sources` and inside `make test` -- so a misconfigured
`sources.yaml` entry fails at save time with a sentence attached, not at 6am
as a connection timeout. Deliberately no DNS in it: a lookup there would make
config saves depend on the network and the offline suite non-hermetic.

`check_resolved` is the connect-time half: `HttpClient` resolves each hop's
hostname, vets EVERY answer here, and then CONNECTS TO THE ADDRESS IT VETTED
(resolve-and-pin -- see `_get_once` in `sources/http.py`). The pin is what
makes a resolving check worth anything: these URLs come from a model's
web-search results, so they point at domains someone OWNS, and an owner
controls the DNS record -- a one-second TTL is three lines of config. A guard
that resolves once and then lets the transport resolve again hands the owner
a rebinding window; pinning removes the second resolution entirely.

Where the host grants NET_ADMIN, the image's entrypoint additionally
drops egress to RFC1918, 100.64/10 and 169.254/16 -- defense in depth that
also covers any HTTP client added to this codebase later. Managed hosts
(a managed host) cannot grant NET_ADMIN at all, which is why the control that has to
hold lives in the client, not the network.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from urllib.parse import ParseResult, urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})

# 80 and 443 only. Not a strong control on its own -- an attacker picks a port --
# but it turns "someone pasted an internal service URL" into an error with a
# sentence attached.
ALLOWED_PORTS = frozenset({80, 443})


class BlockedUrl(ValueError):
    """Refused before any request was made.

    A distinct type so `pipeline/fetch.py` can tell it from a network failure:
    a blocked URL is a configuration or model mistake, and it must NOT count
    toward a source's strike total. Three of those and a healthy feed
    auto-disables until someone runs `venona sources --enable`.
    """


def _address_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # `ipv4_mapped` first: `::ffff:127.0.0.1` is loopback wearing a v6 hat, and
    # every one of the properties below answers False for it.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped

    if any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    ):
        return True

    # Named explicitly even though 3.12's `is_private` covers both. The
    # special-registry table is a Python implementation detail, and a security
    # control should not rest on it silently.
    if ip.version == 4:
        if ip in ipaddress.ip_network("100.64.0.0/10"):  # CGNAT
            return True
        if ip in ipaddress.ip_network("169.254.169.254/32"):  # cloud metadata
            return True
    return False


def check_static(url: str) -> ParseResult:
    """Parse-time checks only. No DNS, no network. Raises `BlockedUrl`.

    Safe to call from config validation, from a pydantic validator, and from the
    request path.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise BlockedUrl(f"unparseable url: {exc}") from exc

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise BlockedUrl(f"scheme {parsed.scheme!r} is not fetchable (http and https only): {url}")

    # `https://expected.example.com@evil.example.com/` is a classic: a reader
    # sees the first host, the client connects to the second. venona has no use
    # for HTTP userinfo at all, so refusing it costs nothing.
    if parsed.username or parsed.password:
        raise BlockedUrl(f"url carries credentials in its authority: {url}")

    hostname = parsed.hostname
    if not hostname:
        raise BlockedUrl(f"url has no host: {url}")

    try:
        port = parsed.port
    except ValueError as exc:
        raise BlockedUrl(f"invalid port: {url}") from exc
    if port is not None and port not in ALLOWED_PORTS:
        raise BlockedUrl(f"port {port} is not fetchable (80 and 443 only): {url}")

    # An IP LITERAL needs no DNS to judge, so judging it here is free and exact.
    # `http://127.0.0.1:8000/api/...` -- venona's own API -- is the case that
    # matters most, and it is caught without a lookup.
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return parsed  # a name; the network rules decide where it goes

    if _address_is_blocked(literal):
        raise BlockedUrl(f"{hostname} is not a routable public address: {url}")
    return parsed


def check_resolved(
    url: str,
    addresses: Iterable[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> None:
    """Vet every DNS answer for a URL's hostname. Raises `BlockedUrl`.

    ALL answers, not just the one being dialled: a split answer -- one public
    address and one private -- is an attack shape, not a CDN quirk worth
    accommodating, and failing closed costs a legitimate publisher nothing.
    """
    for ip in addresses:
        if _address_is_blocked(ip):
            hostname = urlparse(url).hostname
            raise BlockedUrl(
                f"{hostname} resolves to {ip}, which is not a routable public address: {url}"
            )


def is_https(url: str) -> bool:
    return urlparse(url).scheme.lower() == "https"
