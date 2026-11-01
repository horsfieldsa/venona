"""A token bucket for the one unauthenticated write, `POST /request-access`.

In-process and deliberately tiny: the hub is single-replica by design, so a
dict is the truth. Everything else on the hub is either read-only, behind a
session, or behind a bearer.
"""

from __future__ import annotations

import time

from starlette.requests import Request

CAPACITY = 5.0
REFILL_PER_SECOND = 5 / 60

_buckets: dict[str, tuple[float, float]] = {}


def client_key(request: Request, *, edge: bool) -> str:
    """The address to charge, chosen the way the instance chooses it.

    The socket peer is the wrong answer on both deployments. Behind
    Cloudflare -> Render it is the platform's router -- one address for every
    visitor, so the bucket below covers the whole internet at five submissions
    an hour and the form closes for everyone. Behind a reverse proxy it is the proxy. On the
    edge path `CF-Connecting-IP` carries the real client and Cloudflare
    overwrites it on every request it proxies; off it, the rightmost
    `X-Forwarded-For` hop is the one the client cannot write. The full argument
    for both, including why the leftmost hop is never the answer, is on
    `venona.api.middleware._client_key`.
    """
    if edge:
        cf = request.headers.get("cf-connecting-ip", "").strip()
        if cf:
            return cf
    for hop in reversed(request.headers.get("x-forwarded-for", "").split(",")):
        hop = hop.strip()
        if hop:
            return hop
    return request.client.host if request.client else "unknown"


def allow(key: str, *, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    tokens, updated = _buckets.get(key, (CAPACITY, now))
    tokens = min(CAPACITY, tokens + (now - updated) * REFILL_PER_SECOND)
    if tokens < 1.0:
        _buckets[key] = (tokens, now)
        return False
    _buckets[key] = (tokens - 1.0, now)
    return True


def reset() -> None:
    _buckets.clear()
