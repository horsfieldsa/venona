"""The one HTTP client, plus its replay and recording transports.

Everything network-facing goes through `HttpClient` so that `VENONA_OFFLINE=1`
can swap the transport and a run becomes fully reproducible with zero network
(decision 7). `make lint-determinism` fails the build if anything in the guarded
packages constructs an httpx client itself.

Politeness is deliberately modest: every publisher robots.txt read during design
is permissive for `User-agent: *` and the system fetches roughly a dozen feeds a
day, so an honest User-Agent with a contact URL, conditional GETs and a small
per-host delay is the whole of it.

The default headers below go further, and that is not a disguise -- `user_agent()`
still names venona and carries a contact URL, and it is still what every server
sees. The rest are the ordinary request envelope a browser sends, because
several .gov and .mil WAFs match on its ABSENCE rather than on the agent string
and returned 403 to a request that named itself honestly. That refused half the
candidate URLs in the first live acronym lookups, starving a grounding gate that
was working correctly.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import random
import socket
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from venona.sources.urlguard import check_resolved, check_static

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


async def resolve_host(host: str) -> list[IPAddress]:
    """Every A and AAAA answer for `host`, deduplicated, v4 first.

    v4 first because the pin dials exactly one address, with no happy-eyeballs
    underneath to rescue a pinned AAAA on a host with no v6 route (ADR 0179).

    A failed lookup is an `httpx.ConnectError`, NEVER a `BlockedUrl`: a
    transient NXDOMAIN earns the same retry-then-strike treatment as a refused
    connection, while `BlockedUrl` means "resolved fine, to an address venona
    refuses to talk to".
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            host,
            None,
            type=socket.SOCK_STREAM,
            # Only the families this host can actually route: on a managed host, a
            # container with no v6 address otherwise gets AAAA answers that the
            # pin then dials (ADR 0179).
            flags=socket.AI_ADDRCONFIG,
        )
    except socket.gaierror as exc:
        raise httpx.ConnectError(f"could not resolve {host}: {exc}") from exc

    addresses: list[IPAddress] = []
    for info in infos:
        # A v6 sockaddr may carry a scope id (`fe80::1%eth0`) that `ip_address`
        # cannot parse, and the scope changes nothing about being blocked.
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip not in addresses:
            addresses.append(ip)
    if not addresses:
        raise httpx.ConnectError(f"no addresses for {host}")
    return sorted(addresses, key=lambda ip: ip.version)


def routable_family(answers: list[IPAddress]) -> list[IPAddress]:
    """The answers a retry may choose between: one family, the preferred one.

    `resolve_host` sorts v4 first, which decides the FIRST attempt and nothing
    else. A retry that indexes further into the mixed list crosses into a family
    the first attempt did not use, and on a host with no v6 route every such
    attempt is dead before it is made: `media.defense.gov` and
    `diplomacy.state.gov` answer with one A and two AAAA, so both retries after
    a refusal pinned IPv6 and reported a connection failure for a request the
    origin had already answered with 403 (ADR 0179, 2026-08-30).
    """
    preferred = answers[0].version
    return [ip for ip in answers if ip.version == preferred]


def user_agent() -> str:
    from venona.domain import get_domain

    return f"venona/0.1 ({get_domain().user_agent_note}; +https://github.com/venona) httpx"


DEFAULT_TIMEOUT = 25.0
PER_HOST_DELAY = 1.0

#: The ceiling on a single response body, in DECODED bytes -- `_read_capped` has
#: why there is one at all. Sized 2026-08-25 against the work: the largest thing
#: worth fetching is an article, `max_doc_chars` truncates to 12k characters
#: downstream, and 10MB is three orders of magnitude of headroom over any real one.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class BodyTooLarge(Exception):
    """A response body passed `MAX_RESPONSE_BYTES` and was abandoned mid-read.

    Its own class rather than a `BlockedUrl`, which means the URL was refused
    BEFORE it was dialled, on what it pointed at. This one means the host
    answered and then sent too much, which a caller may reasonably treat as one
    dead page rather than as a policy violation.
    """


# The envelope, not a disguise -- see the module docstring. `User-Agent` is ours
# and is set alongside these.
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # NEVER ADVERTISE AN ENCODING WE CANNOT DECODE. httpx does not raise on a
    # `Content-Encoding` it has no decoder for -- it falls back to
    # `IdentityDecoder` and hands back the raw compressed bytes with a 200. So
    # asking for `br`, which `httpx[http2]` cannot decode, took DefenseScoop,
    # Defense One and War on the Rocks dark for three strikes each, reported as
    # "moved or reformatted?" (ADR 0090). Derived rather than written so it
    # cannot drift: adding the `brotli` extra makes `br` appear here on its own.
    "Accept-Encoding": ", ".join(e for e in httpx._decoders.SUPPORTED_DECODERS if e != "identity"),
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


# Statuses worth asking again about, and never one that means the page is simply
# not there -- retrying a 404 is just a slower 404.
#
# 403 is deliberately NOT here. A bot manager's 403 is a verdict, not a hiccup:
# measured 2026-08-30 across 533 candidate fetches, no host answering 403 ever
# passed on a retry, and hammering a WAF is how a refusal becomes a dropped
# connection (ADR 0179).
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_DELAY = 1.5

# Three attempts total: a feed that fails three times in ~5 seconds is down.
MAX_RETRIES = 3

# Ceiling on one backoff, including one the server asked for. A CDN that says
# `Retry-After: 3600` is not going to be waited for inside a run.
MAX_BACKOFF = 30.0

# Followed by hand -- see `_fetch_following_redirects`.
MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


@dataclass
class Response:
    """A transport-independent response, so replay and live look identical."""

    url: str
    status: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def not_modified(self) -> bool:
        """304 means our stored ETag is still current -- nothing new to read."""
        return self.status == 304


def _looks_recordable(response: Response) -> bool:
    """Is this body something a human would recognise as the document we asked for?

    A fixture is test data, and test data that is silently wrong is worse than no
    fixture at all. The one cheap, general check: a body we successfully decoded
    is text, and text that survived a round trip has no replacement characters.
    """
    text = response.text
    if not text:
        return False
    return "\ufffd" not in text[:512]


def fixture_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return False
    return True


class HttpClient:
    """Live HTTP. One client, reused, with per-host pacing."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        record_dir: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        rng: Any | None = None,
        resolve: Any | None = None,
    ) -> None:
        """`transport` is THE reason this class is testable at all.

        `build_http_client` returns a `ReplayHttpClient` when offline, and that is
        a SEPARATE implementation reading a fixture index -- so nothing in the
        offline suite constructs this class, and pacing, retry, backoff and the
        conditional-GET interaction can only be exercised by a live forty-minute
        run. An `httpx.MockTransport` lets a test script a server instead.

        `rng` makes the retry jitter injectable for those tests, and is NOT a
        determinism requirement: `sources/` is outside `lint-determinism`'s
        guarded packages and this class never runs offline. `resolve` is the same
        idea for DNS, keeping the offline suite off the network AND making the
        resolve-and-pin behaviour (see `_get_once`) scriptable -- rebinding
        answers included.
        """
        self._client = httpx.AsyncClient(
            # Per-phase, not one number for all four: a dead host should be given
            # up on in seconds, while a large document legitimately takes longer
            # to read, and 25s is the wrong answer to both.
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=10.0, pool=5.0),
            # `get` follows redirects by hand so per-hop pacing applies and the
            # URL guard and the resolver see every hop -- httpx would otherwise
            # redirect inside the transport, where nothing can inspect it.
            follow_redirects=False,
            # ZERO KEEPALIVE, deliberately. Under the pin, httpcore's pool is
            # keyed on the dialled origin -- the IP -- so two vetted hostnames
            # sharing a CDN address would reuse a connection whose certificate
            # was verified for only one of them.
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=0),
            headers={"User-Agent": user_agent(), **DEFAULT_HEADERS},
            transport=transport,
            # An ambient HTTPS_PROXY would route around the pin entirely.
            trust_env=False,
        )
        self._last_hit: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._rng = rng or random.Random()
        self._resolve = resolve or resolve_host
        self._record_dir = record_dir
        if record_dir:
            record_dir.mkdir(parents=True, exist_ok=True)

    async def _get_once(
        self,
        url: str,
        headers: dict[str, str],
        pinned_ip: str | None = None,
        *,
        method: str = "GET",
        content: bytes | None = None,
    ) -> httpx.Response:
        """Exactly one request, holding exactly one host lock.

        `method`/`content` exist for `post_once`. They are here rather than in a
        second copy of the pin because the pin is the only thing that makes the
        resolving check worth anything, and two copies would drift apart quietly.

        ONE LOCK. The naive way to add per-hop pacing to a redirect loop is to
        nest `async with self._locks[next_host]` inside the lock already held for
        the current host -- and two concurrent fetches, A->B and B->A, deadlock
        on the inversion. That is trivially arranged: `try_candidates` in
        `pipeline/web_grounding.py` walks a list of model-supplied URLs, and
        `asyncio.Lock` has no timeout, so the run would hang forever holding its
        lane slot.

        THE PIN. With `pinned_ip`, the request dials the address the caller just
        vetted instead of letting the transport resolve the name again -- the
        re-resolution is the rebinding window (`urlguard.py`). The hostname
        survives in an explicit `Host` header (httpx only auto-derives Host when
        the header is absent) and, for https, in the `sni_hostname` extension,
        which httpcore hands to ssl as `server_hostname` -- so SNI and
        certificate verification both run against the NAME. Lock, pacing, and
        everything the caller sees stay keyed on the hostname.
        """
        host = urlparse(url).netloc
        async with self._locks[host]:
            wait = PER_HOST_DELAY - (time.monotonic() - self._last_hit[host])
            if wait > 0:
                await asyncio.sleep(wait)
            body = {} if content is None else {"content": content}
            try:
                if pinned_ip is None:
                    return await self._read_capped(method, url, headers=headers, **body)
                target = httpx.URL(url).copy_with(host=pinned_ip)
                extensions = (
                    {"sni_hostname": urlparse(url).hostname} if target.scheme == "https" else None
                )
                return await self._read_capped(
                    method,
                    target,
                    headers={**headers, "Host": host},
                    extensions=extensions,
                    **body,
                )
            finally:
                self._last_hit[host] = time.monotonic()

    async def _read_capped(self, method: str, url: Any, **kwargs: Any) -> httpx.Response:
        """A response whose body is read under a ceiling.

        THE URL IS OFTEN THE MODEL'S CHOICE. Tier-3 grounding fetches pages the
        model nominated, so the size of what comes back is decided by whoever
        controls that page -- and this runs in the API process, so a body large
        enough to exhaust memory takes the whole server down, not just the run.

        The ceiling is on the DECODED bytes: `aiter_bytes` yields
        post-decompression, so a few hundred kilobytes of gzip claiming to be a
        megabyte is refused at the megabyte. A `Content-Length` check alone would
        be no defence at all -- it is supplied by the same page. `max_doc_chars`
        is not this either: that is a code-point cap in `llm/runner.py`, long
        after the whole body is in memory.
        """
        request = self._client.build_request(method, url, **kwargs)
        response = await self._client.send(request, stream=True)
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise BodyTooLarge(
                        f"{url}: response exceeded {MAX_RESPONSE_BYTES} bytes and was abandoned"
                    )
                chunks.append(chunk)
        finally:
            await response.aclose()
        # Re-presented as an ordinary response so every caller keeps reading
        # `.text`, `.status_code` and `.headers`.
        #
        # THE CONTENT HEADERS MUST NOT COME WITH IT. `aiter_bytes` yields bytes
        # that httpx has ALREADY decoded, so carrying `Content-Encoding` forward
        # describes the body as still compressed and the next read decodes it a
        # second time -- `DecodingError: incorrect header check`, on every origin
        # that serves gzip, which is nearly all of them. `Content-Length` goes
        # for the same reason: it measured the compressed body.
        headers = httpx.Headers(
            [
                (name, value)
                for name, value in response.headers.multi_items()
                if name.lower() not in ("content-encoding", "content-length")
            ]
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=headers,
            content=b"".join(chunks),
            request=request,
            extensions=response.extensions,
        )

    def _backoff(self, attempt: int, response: httpx.Response | None) -> float:
        """`Retry-After` when the server said so, else jittered exponential.

        Honouring it is the whole difference between backing off and pretending
        to: answering a 429 that asked for 120 with a flat 1.5 seconds is not a
        retry, it is a second refusal.
        """
        if response is not None:
            raw = response.headers.get("retry-after", "")
            if raw.strip().isdigit():
                return min(MAX_BACKOFF, float(raw.strip()))
        base = min(MAX_BACKOFF, RETRY_DELAY * (2**attempt))
        return base * (0.5 + self._rng.random() * 0.5)

    async def _fetch_following_redirects(
        self, url: str, headers: dict[str, str]
    ) -> tuple[httpx.Response, str]:
        """Retry the transient failures, then follow redirects by hand.

        Returns the response AND the final hostname-form URL: under the pin the
        response's own `.url` carries the dialled IP, and the recorder's index,
        `normalize.url_key_for` and the reported final URL must stay keyed on the
        name.

        DO NOT GATE THE RETRY ON `not headers`. `_collect_one` passes the stored
        ETag on every fetch after the first, so that condition is False for every
        feed that has ever succeeded -- the retry would fire only on a source's
        first-ever request or right after `clear_strikes`. Exempting a 304 is
        right, but a 304 is not in RETRY_STATUSES anyway; exempting the whole
        conditional request is not the same thing.

        Retries cover CONNECT and READ failures too. Those otherwise go straight
        to `except Exception` in `collect_rss` and earn a strike -- and at
        STRIKE_LIMIT=3 a healthy feed auto-disables after three transient blips
        on three consecutive nights.

        Resolution happens INSIDE the attempt loop, on purpose: a transient DNS
        failure gets retry-and-backoff rather than an instant strike, and
        successive attempts rotate through the vetted answers within one family
        (ADR 0179). A `BlockedUrl` from `check_resolved` is a ValueError -- it
        sails past the retry catch and keeps its no-strike semantics in
        `pipeline/fetch.py`.
        """
        current = url
        for _ in range(MAX_REDIRECTS):
            # Every hop, not just the first, which is the whole reason redirects
            # are followed by hand rather than inside the transport.
            hostname = check_static(current).hostname or ""
            literal = _is_ip_literal(hostname)
            raw: httpx.Response | None = None
            # The last attempt that got an ANSWER, kept across attempts that do
            # not: a refusal says what the origin decided, where a transport
            # error only says we stopped being able to ask.
            answered: httpx.Response | None = None
            last_error: Exception | None = None

            for attempt in range(MAX_RETRIES):
                try:
                    if literal:
                        # Already vetted by `check_static`, and dialling it as-is
                        # verifies the certificate against its IP SAN.
                        pin = None
                    else:
                        answers = await self._resolve(hostname)
                        # EVERY answer, fail-closed: a host resolving to one
                        # public and one private address is refused whichever
                        # one a retry would have dialled.
                        check_resolved(current, answers)
                        rotation = routable_family(answers)
                        pin = str(rotation[attempt % len(rotation)])
                    raw = await self._get_once(current, headers, pinned_ip=pin)
                    answered = raw
                    last_error = None
                except (httpx.TransportError, httpx.ProtocolError) as exc:
                    raw, last_error = None, exc

                retryable = last_error is not None or (
                    raw is not None and raw.status_code in RETRY_STATUSES
                )
                if not retryable or attempt == MAX_RETRIES - 1:
                    break
                await asyncio.sleep(self._backoff(attempt, raw))

            if raw is None:
                if answered is None:
                    raise last_error if last_error else httpx.TransportError("no response")
                # Report what the origin said. Raising the transport error over a
                # status we hold is what told nine days of traces that
                # `.gov`/`.mil` hosts were unreachable from production, when
                # every one had answered 403 (ADR 0179).
                raw = answered

            if raw.status_code not in REDIRECT_STATUSES:
                return raw, current
            location = raw.headers.get("location")
            if not location:
                return raw, current
            current = urljoin(current, location)

        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects from {url}")

    async def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> Response:
        """Conditional GET. A 304 costs almost nothing and is the main reason a
        daily run over the same feeds stays cheap."""
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        raw, final_url = await self._fetch_following_redirects(url, headers)

        # The hostname-form URL, never `raw.url`, which carries the dialled IP.
        response = Response(
            url=str(httpx.URL(final_url)),
            status=raw.status_code,
            text="" if raw.status_code == 304 else raw.text,
            headers={k.lower(): v for k, v in raw.headers.items()},
        )

        # `response.text` on an undecoded body is mojibake and still truthy, so
        # recording it turns a live-only bug into a permanently poisoned offline
        # suite: the Brotli regression would have baked three broken feeds into
        # `fixtures/http/` (ADR 0090).
        if self._record_dir is not None and response.ok and _looks_recordable(response):
            self._write_fixture(url, response)

        return response

    async def post_once(
        self, url: str, *, content: bytes, headers: dict[str, str] | None = None
    ) -> Response:
        """One POST to a destination somebody typed. No redirects, no retry.

        WHY THIS IS HERE RATHER THAN IN `notify/`. `REVIEW-2026-08` cleared that
        client of SSRF because its URL was an environment variable, "not
        runtime-editable through any API and not derived from attacker input".
        Destinations are rows a reader types into a form, so every clause of that
        sentence stops being true (ADR 0193). What replaces it is the control
        this class implements: vet every resolved answer, then dial the address
        that was vetted, so the destination's DNS owner has no rebinding window.

        NO REDIRECTS. `get` follows them by hand and re-vets each hop because a
        feed genuinely moves. A webhook does not: an endpoint answering 302 is
        either misconfigured or steering us, so the status is reported as it
        stands and the caller counts it as a failure.

        NO RETRY EITHER, and that is the outbox's job rather than an omission.
        Retrying here would send the same message twice to a receiver that
        answered slowly, and the queue knows how to try again without
        duplicating a row.

        The per-host lock and delay still apply, so a burst of deliveries to one
        Slack workspace paces itself exactly as feed collection does.
        """
        hostname = check_static(url).hostname or ""
        if _is_ip_literal(hostname):
            # `check_static` refuses private literals outright, so anything here
            # is public: dial as-is and verify the certificate against its IP SAN.
            pin = None
        else:
            answers = await self._resolve(hostname)
            check_resolved(url, answers)
            pin = str(routable_family(answers)[0])
        raw = await self._get_once(
            url, dict(headers or {}), pinned_ip=pin, method="POST", content=content
        )
        return Response(
            url=url,
            status=raw.status_code,
            text=raw.text,
            headers={k.lower(): v for k, v in raw.headers.items()},
        )

    def _write_fixture(self, url: str, response: Response) -> None:
        """Recording shares the replay layout, so a live development run doubles
        as free test data."""
        key = fixture_key(url)
        (self._record_dir / f"{key}.body").write_text(response.text)

        index_path = self._record_dir / "index.json"
        index: dict[str, Any] = {}
        if index_path.exists():
            index = json.loads(index_path.read_text())
        index[url] = {
            "key": key,
            "final_url": response.url,
            "status": response.status,
            "headers": {
                k: v
                for k, v in response.headers.items()
                if k in {"etag", "last-modified", "content-type"}
            },
        }
        index_path.write_text(json.dumps(index, indent=2, sort_keys=True))

    async def aclose(self) -> None:
        await self._client.aclose()


class ReplayHttpClient:
    """Offline transport. Serves recorded bodies from `fixtures/http/`.

    A missing recording is a HARD FAILURE, never a fall-through to the network:
    that is how an "offline" suite quietly starts hitting the internet.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._index: dict[str, Any] = {}
        index_path = root / "index.json"
        if index_path.exists():
            self._index = json.loads(index_path.read_text())

    async def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> Response:
        entry = self._index.get(url)
        if entry is None:
            raise FileNotFoundError(
                f"no recorded HTTP fixture for {url}\n"
                f"  expected an entry in {self._root / 'index.json'}\n"
                f"  record it with: make record"
            )

        body_path = self._root / f"{entry['key']}.body"
        if not body_path.exists():
            raise FileNotFoundError(
                f"index references a missing body file: {body_path}\n  re-record with: make record"
            )

        return Response(
            url=entry.get("final_url", url),
            status=entry.get("status", 200),
            text=body_path.read_text(),
            headers=entry.get("headers", {}),
            from_cache=True,
        )

    async def post_once(
        self, url: str, *, content: bytes, headers: dict[str, str] | None = None
    ) -> Response:
        """Never reached, and it raises rather than returning a fake success.

        `notify.raise_notice` returns before it queues anything offline, so this
        exists to make that invariant fail loudly if the guard is ever moved.
        There is nothing sensible to record either: a POST has no cacheable body,
        and pretending it succeeded would mark a delivery as sent that never
        happened.
        """
        raise RuntimeError(
            f"offline: refusing to POST to {url}. Delivery is a no-op when "
            f"VENONA_OFFLINE is set, so reaching this means the guard in "
            f"`notify.raise_notice` was bypassed."
        )

    async def aclose(self) -> None:
        return None


def build_http_client(settings, *, record: bool = False):
    """One place decides live vs replay, mirroring `build_executor`."""
    if settings.offline:
        return ReplayHttpClient(settings.fixtures_dir / "http")
    return HttpClient(record_dir=(settings.fixtures_dir / "http") if record else None)
