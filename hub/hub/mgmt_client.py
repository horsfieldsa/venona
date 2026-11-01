"""Outbound calls to an instance's management surface.

The ONLY module that holds an unsealed `vmgmt_` token, and only for the
duration of a request. The reach is the instance's leash (its CONTRACTS C12
addendum): status reads and user provisioning, nothing else -- so nothing
else is expressible here.
"""

from __future__ import annotations

from typing import Any

import httpx

TIMEOUT = 15.0

#: Test seam, like `google.EXCHANGE_TRANSPORT`: the offline suite injects an
#: `httpx.MockTransport`; None means the real network.
MGMT_TRANSPORT: httpx.AsyncBaseTransport | None = None


class MgmtError(RuntimeError):
    """A poll or provision call failed. Message is operator-safe."""


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, trust_env=False, transport=MGMT_TRANSPORT)


async def fetch_status(public_url: str, mgmt_token: str) -> dict[str, Any]:
    """GET /api/mgmt/status. Returns the body VERBATIM -- the shipped
    MgmtStatus envelope is the instance's to define and the hub's to store."""
    async with _client() as client:
        response = await client.get(
            f"{public_url}/api/mgmt/status",
            headers={"Authorization": f"Bearer {mgmt_token}"},
        )
    if response.status_code != 200:
        raise MgmtError(f"{public_url} answered {response.status_code} to a status poll")
    body = response.json()
    if not isinstance(body, dict):
        raise MgmtError(f"{public_url} answered a status poll with a non-object body")
    return body


async def push_settings(
    public_url: str, mgmt_token: str, *, billing_enforced: bool
) -> tuple[int, dict[str, Any]]:
    """POST /api/mgmt/settings. The switches an operator throws, delivered.

    Returns the status and body rather than raising, like `provision_user`: the
    caller decides what a refusal means, and here it means "leave it pending and
    try again next poll" rather than "an error happened". A domain that is down
    while somebody flips a switch is the ordinary case, not a fault.

    The body echoes what is now TRUE on that instance, which can differ from
    what was sent -- `VENONA_BILLING_ENFORCED` overrides the hub in both
    directions -- and that difference is worth storing rather than assuming
    away.
    """
    async with _client() as client:
        response = await client.post(
            f"{public_url}/api/mgmt/settings",
            headers={"Authorization": f"Bearer {mgmt_token}"},
            json={"billing_enforced": billing_enforced},
        )
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text[:300]}
    return response.status_code, body


async def provision_user(
    public_url: str,
    mgmt_token: str,
    *,
    email: str,
    role: str,
    action: str,
    entitlement: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """POST /api/mgmt/provision-user, passed through: the instance's status
    code and body come back as-is, because its 404 ("no such user") is an
    answer for the admin, not a hub failure.

    Entitlement rides as EXTRA FIELDS on the existing actions, and never as an
    action of its own. `venona.api.schemas.mgmt.ProvisionUser.action` is a
    pydantic *pattern*, so an unrecognised action is a 422 from validation
    before any handler runs -- a hub that deployed first would then fail every
    push until the instance caught up. Extra fields are ignored by pydantic's
    default `extra='ignore'`, so an instance without billing simply drops them
    and no deployment order breaks (ADR 0209).
    """
    payload: dict[str, Any] = {"email": email, "role": role, "action": action}
    if entitlement is not None:
        payload.update(entitlement)
    async with _client() as client:
        response = await client.post(
            f"{public_url}/api/mgmt/provision-user",
            headers={"Authorization": f"Bearer {mgmt_token}"},
            json=payload,
        )
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text[:300]}
    return response.status_code, body


async def logout_user(public_url: str, mgmt_token: str, *, email: str) -> None:
    """End this user's sessions on one instance: the `logout` provisioning
    action, which bumps the same per-user session epoch the user's own
    sign-out does. `role` is required by the wire shape and ignored by the
    action."""
    status, body = await provision_user(
        public_url, mgmt_token, email=email, role="reader", action="logout"
    )
    # 404 is "no user row here", which for a sign-out IS signed out.
    if status not in (200, 404):
        detail = body.get("detail", body) if isinstance(body, dict) else body
        raise MgmtError(f"{public_url} answered {status} to a logout: {detail}")
