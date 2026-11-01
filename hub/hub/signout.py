"""One sign-out means all sign-outs.

The hub is the entry point both ways through the door: signing in at the hub
signs you into instances without a prompt, so signing out at the hub must not
leave those instance sessions alive. Both exits -- the portal's own Sign out
and the RP-initiated `/oauth/logout` an instance sends the browser to -- call
this before clearing the hub cookie.

Two layers, one shape. The hub's OWN sessions die by the same per-user
epoch watermark the instances use (`users.session_epoch`, checked in
`deps.session_email`); each instance then gets the `logout` provisioning
action, the identical bump on its side. Server-side both times, so every
browser and device is covered -- no cookie choreography, no
third-party-cookie rules.

THE INSTANCE FAN-OUT IS BEST-EFFORT. An instance that cannot be reached
keeps its sessions until it can be; the alternative is a sign-out that hangs
or fails on one offline box, which punishes the person standing at the door.
Failures are logged, never raised. The hub epoch bump is NOT best-effort --
it is one local UPDATE, and with the hub sessions dead an unreached
instance's surviving session cannot be silently re-minted, only played out
to its expiry.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import Request

from hub import mgmt_client, seal
from hub.db.core import Conn
from hub.repositories import users as users_repo

log = logging.getLogger("hub.signout")

#: Tighter than `mgmt_client.TIMEOUT`: a sign-out is a person waiting on a
#: redirect, and an offline instance must cost seconds, not the full 15.
PER_INSTANCE_TIMEOUT = 5.0


async def end_all_sessions(request: Request, conn: Conn, email: str | None) -> None:
    if not email:
        return
    user = users_repo.by_email(conn, email)
    if user is None:
        # A drive-by Google sign-in: a session but no row, so no memberships
        # and nothing to end anywhere.
        return
    # The HUB's own sessions first: the hub cookie is the SSO master key, so a
    # surviving copy on another device would silently re-mint every instance
    # session ended below. The watermark is checked in `deps.session_email`.
    users_repo.bump_session_epoch(conn, user_id=user["id"], now=time.time())
    rows = users_repo.instances_to_sign_out(conn, user["id"])
    if not rows:
        return
    seal_key = request.app.state.settings.seal_key

    async def one(row) -> None:
        token = seal.unseal(seal_key, row["mgmt_token_sealed"])
        await asyncio.wait_for(
            mgmt_client.logout_user(row["public_url"], token, email=email),
            PER_INSTANCE_TIMEOUT,
        )

    results = await asyncio.gather(*(one(row) for row in rows), return_exceptions=True)
    for row, result in zip(rows, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("sign-out could not reach %s: %s", row["slug"], result)
