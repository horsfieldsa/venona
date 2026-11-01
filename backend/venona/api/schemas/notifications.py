"""Where notices go, on the wire.

ONE FIELD IS DELIBERATELY ABSENT FROM EVERY MODEL HERE: `target`. A webhook URL
is a credential -- anyone holding a Slack incoming URL can post to that channel
-- and `api/security.py` opens by explaining that auth material must be neither
attacker-writable nor operator-visible over HTTP. Venona accepts this one rather
than minting it, which is the opposite direction, but the corollary binds the
same way: once stored it is never rendered back. `hint` is what stands in its
place, and `tests/test_notifications_api.py` greps every response for the stored
value.

That is also why there is no `DestinationUpdate` carrying a target. Replacing an
address is its own route with its own body, so no screen can offer an edit
control pre-filled with the credential.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NoticeTypeOut(BaseModel):
    """One switch, in the words the registry declares.

    `label` and `detail` ship from the server for the reason
    `RunType.outcomes` does: a second copy in TypeScript drifts inside a
    release, and four surfaces once invented run-outcome words independently
    with one of them wrong.
    """

    key: str
    label: str
    detail: str
    #: Whether this destination is currently subscribed.
    on: bool


class DestinationOut(BaseModel):
    id: str
    kind: Literal["webhook", "email"]
    label: str
    #: Enough to tell two destinations apart, never enough to use. An email
    #: address is shown whole: it is not a secret, and a reader checking where
    #: their brief goes has to be able to read it.
    hint: str
    #: `active` | `disabled` | `struck_out`. The last one is venona's own
    #: decision after repeated failures, and it is the one a screen must
    #: explain rather than merely colour.
    status: str
    failures: int = 0
    last_error: str | None = None
    #: Who added it. Attribution, not ownership: any admin may edit any
    #: instance destination.
    added_by: str | None = None
    created_at: str
    notices: list[NoticeTypeOut] = Field(default_factory=list)


class DestinationCreate(BaseModel):
    kind: Literal["webhook", "email"]
    label: str
    #: The URL or the address. Write-only: it is validated, stored, and never
    #: returned by anything.
    target: str


class TargetReplace(BaseModel):
    target: str


class SubscriptionUpdate(BaseModel):
    notice_type: str
    on: bool


class TestResult(BaseModel):
    """What a test send did, said in full.

    `detail` carries the endpoint's own refusal or the guard's sentence rather
    than a status code, because that is the half a person can act on, and it is
    rendered inline rather than in a toast for the same reason.
    """

    delivered: bool
    detail: str | None = None
    #: Exactly what was sent, so somebody can see the shape their receiver got.
    preview: str | None = None


class NotificationsOut(BaseModel):
    """The admin screen, in one response."""

    destinations: list[DestinationOut] = Field(default_factory=list)
    #: Whether an email subscription can be delivered at all right now. False
    #: today, and the screen says so plainly rather than offering controls that
    #: would flip and do nothing.
    email_available: bool = False
    #: Why not, in a sentence. Null when email works.
    email_unavailable_reason: str | None = None


class DeliveryOut(BaseModel):
    id: str
    notice_type: str
    #: The registry's own label for that type, resolved server-side so no
    #: client has to hold a map of keys to English.
    notice_label: str
    #: The destination's name, so a row says where it went without carrying
    #: anything about how to get there.
    destination: str
    kind: str
    status: str
    attempts: int = 0
    last_error: str | None = None
    created_at: str
    delivered_at: str | None = None


class DeliveryList(BaseModel):
    items: list[DeliveryOut] = Field(default_factory=list)
    #: What the list was capped at. Announced, because a truncated list that
    #: does not say so is read as a complete one.
    limit: int
    #: How many deliveries there are in all, so "capped" can be read as "there
    #: are more" rather than guessed at from a full-looking page.
    total: int = 0


class NotificationStatus(BaseModel):
    """The small, frequently-polled answer behind the rail's mark.

    Separate from `NotificationsOut` because it rides the app's existing poll
    and must stay cheap: two integers and a name, not a listing.
    """

    struck_out: int = 0
    #: The first struck-out destination's name, so the banner can say which
    #: one without a second request.
    first_struck_out: str | None = None
    queued: int = 0
    blocked: int = 0


class ReaderNotificationsOut(BaseModel):
    """The reader's own screen.

    `email_available` is what decides whether the email switches render at all.
    A switch that cannot work is worse than an honest sentence, which is the
    decision `AccountView.vue` already made for this exact feature.
    """

    destinations: list[DestinationOut] = Field(default_factory=list)
    #: The address venona would use, from the account. The reader never types
    #: it, and it is shown so they can see where their brief would go.
    account_email: str | None = None
    email_available: bool = False
    email_unavailable_reason: str | None = None
