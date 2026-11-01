"""The edge security headers defer to a policy a route set for itself."""

from __future__ import annotations

import httpx
import pytest
from venona.api.middleware import SecurityHeadersMiddleware as AppHeaders
from venona.api.middleware import content_security_policy


def _edge_value(headers: type, name: str) -> str:
    return dict(headers.EDGE_HEADERS)[name.encode()].decode()


# ── the one route that owns its own policy ───────────────────────────


@pytest.mark.anyio
async def test_the_edge_defers_to_a_policy_a_route_set_for_itself() -> None:
    """⭐ Appending the edge CSP over a route's own leaves TWO on the response,
    and a browser enforces their INTERSECTION -- so the widening the consent
    screen depends on would be undone by the header that follows it, with both
    policies looking correct in isolation.
    """
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    own = content_security_policy("'self' https://callback.test")

    async def plain(_request):
        return PlainTextResponse("x")

    async def opinionated(_request):
        return PlainTextResponse("x", headers={"content-security-policy": own})

    app = Starlette(routes=[Route("/plain", plain), Route("/own", opinionated)])
    app.add_middleware(AppHeaders, edge=True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://venona.test") as client:
        plain_response = await client.get("/plain")
        assert plain_response.headers.get_list("content-security-policy") == [
            _edge_value(AppHeaders, "content-security-policy")
        ]

        owned = await client.get("/own")
        assert owned.headers.get_list("content-security-policy") == [own]
        # The rest of the edge headers still land -- only the CSP defers.
        assert owned.headers["strict-transport-security"] == _edge_value(
            AppHeaders, "strict-transport-security"
        )
