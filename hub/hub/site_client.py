"""Reading a separate front site's version.

SEPARATE FROM `mgmt_client` deliberately, on both of that module's own terms:
it says it is the only place an unsealed `vmgmt_` token lives and that nothing
but the instance leash is expressible there, and this call carries no credential
at all. Its `MGMT_TRANSPORT` seam is the sharper reason -- every instance-poll
mock in the offline suite binds it, and sharing it would make each of them
silently answer this fetch too.
"""

from __future__ import annotations

from typing import Any

import httpx

from hub.mgmt_client import TIMEOUT

#: Test seam, like `MGMT_TRANSPORT`, and its own so the two never intercept
#: each other's calls.
SITE_TRANSPORT: httpx.AsyncBaseTransport | None = None


class SiteError(RuntimeError):
    """The site did not answer with a version. Message is operator-safe."""


async def fetch_version(site_url: str) -> dict[str, Any]:
    """GET /version.json, written by the site's own build.

    A static site has no process to ask, so the build stamps the answer into
    the published tree. Returned verbatim, like `fetch_status`: what the site
    says about itself is the site's to define.
    """
    async with httpx.AsyncClient(
        timeout=TIMEOUT, trust_env=False, transport=SITE_TRANSPORT
    ) as client:
        response = await client.get(f"{site_url}/version.json")
    if response.status_code != 200:
        raise SiteError(f"{site_url} answered {response.status_code} to a version read")
    try:
        body = response.json()
    except ValueError as exc:
        raise SiteError(f"{site_url} answered a version read with unreadable content") from exc
    if not isinstance(body, dict):
        raise SiteError(f"{site_url} answered a version read with a non-object body")
    return body
