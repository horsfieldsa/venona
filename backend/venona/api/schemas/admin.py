"""Administration wire types. snake_case, ISO-8601 UTC with `Z` (Conventions).

`response_model` FILTERS, and on this surface that matters more than usual: a
field the model omits vanishes silently, and the fields here are the ones a
person reads before deciding whether somebody may reach the corpus. A `role` or
`status` dropped on the way out renders a permissions screen that lies.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: What an admin may set a role TO over HTTP. `reader` and nothing else.
#:
#: NOT THE ENFORCEMENT. `user_service`'s `allow_admin` is, on `create` as well as
#: `change_authority`, because the hub reaches both without passing through here
#: (ADR 0168). This stays because it puts the refusal in the OpenAPI document, so
#: the frontend cannot offer a control the server would refuse. C12 carries the
#: rule and `routers/admin.py` what granting admin would hand out.
DemotableRole = Literal["reader"]


class AdminUser(BaseModel):
    """One person, as the users table shows them."""

    id: str
    email: str
    role: str
    status: str
    display_name: str | None = None
    created_at: str | None = None
    last_seen_at: str | None = None
    #: No `last_seen_at`, which is stamped at sign-in: an account that exists and
    #: has never been used. THE INVITE FOLLOW-UP SIGNAL -- venona sends no email,
    #: so this is how an operator sees who was approved and never told.
    never_signed_in: bool = True
    #: The hub's projection. `none` on an instance that takes no money, where
    #: `/admin/people` drops the column rather than show a row of "not started".
    entitlement_status: str = "none"
    entitled_through: str | None = None


class AdminUserList(BaseModel):
    items: list[AdminUser] = Field(default_factory=list)
    #: How many could still administer the instance. The screen needs it to
    #: explain why the last admin's controls are disabled, rather than showing an
    #: enabled control that fails.
    active_admins: int = 0
    #: Every user, whole set — what makes the route's bound honest.
    total: int = 0


class RoleChange(BaseModel):
    role: DemotableRole


class SuspensionChange(BaseModel):
    """A note is optional and is for the operator six weeks later, the same way
    `access_requests.note` is."""

    note: str | None = Field(default=None, max_length=500)


class AuthorityEvent(BaseModel):
    """One line of the audit trail.

    ADDRESSES RATHER THAN IDS, for the reason `curation.py`'s verdict schemas
    give: the id is internal and means nothing on screen. `actor_email` is None
    for every change the CLI made, and that is honest rather than missing -- it
    means shell access decided, which is not a `users` row.
    """

    id: str
    user_id: str
    user_email: str | None = None
    actor_email: str | None = None
    action: str
    detail: str | None = None
    created_at: str


class AuthorityEventList(BaseModel):
    items: list[AuthorityEvent] = Field(default_factory=list)
    total: int = 0


class SpendDay(BaseModel):
    day: str
    cost_usd: float = 0.0
    calls: int = 0
    #: WHICH KIND OF NUMBER `cost_usd` IS, and one column holds four incompatible
    #: kinds -- `repositories/metrics.py::cost_by_day` names them, and `unknown`
    #: is a row that recorded none. A day carries a row per kind because an
    #: instance that switched mid-month has two in it, and adding them produces a
    #: total that is not any quantity at all.
    cost_basis: str = "unknown"


class AdminOverview(BaseModel):
    """The dashboard, and only what has no surface anywhere else.

    DELIBERATELY NOT HEALTH. `/api/health` already carries neo4j, the `claude`
    circuit, run state, source strikes and freshness stamps, and the surface
    each outage breaks renders it -- the graph explorer for neo4j, the Runs
    page for the model. Restating it here would be chrome, which is the
    failure this endpoint was scoped against.

    WHICH BUILD THIS IS does belong here, by that same test: it has no surface
    anywhere else an admin can reach. `/api/mgmt/status` has carried it all
    along, but only the hub may call that.
    """

    #: The release, and the exact code inside it. `commit` is null off a build
    #: host, which the screen says as `local` -- a blank would read as missing.
    version: str = "unknown"
    commit: str | None = None
    #: When this process came up, which is how long this build has been serving.
    started_at: str | None = None
    pending_requests: int = 0
    users_total: int = 0
    users_suspended: int = 0
    users_never_signed_in: int = 0
    active_admins: int = 0
    tokens_live: int = 0
    #: Newest first, one row per day that spent anything. MEASURED, NEVER
    #: MANAGED: `llm/budget.py` and `ratelimit.py` both argue that conflating the
    #: two put a spend cap on a flat-rate plan once already, so nothing here
    #: caps, alerts or refuses. It is a read.
    spend: list[SpendDay] = Field(default_factory=list)
    spend_days: int = 30


class AdminToken(BaseModel):
    """One agent credential, as the operator of the instance sees it.

    THE ADDRESS, NOT THE ID, for the reason `AuthorityEvent` gives: `user_id` is
    internal and means nothing on screen, and the question this row answers is
    "whose is this". `user_email` is None for a token whose `user_id` is NULL --
    minted before identity existed -- and the screen names that rather than
    leaving a blank cell, because an unowned credential is the one most worth
    looking at.

    Note what is ABSENT, exactly as `McpToken` notes it: the token. venona stores
    a SHA-256, so this endpoint could not serve the secret if someone added the
    field. `prefix` is enough to match a row against what is pasted into a client
    config and useless on its own.
    """

    id: str
    label: str
    prefix: str
    scope: str
    user_id: str | None = None
    user_email: str | None = None
    #: ATTACKER-SUPPLIED TEXT -- registration is unauthenticated -- so every
    #: render of it escapes. `McpToken` carries the rest.
    client_name: str | None = None
    created_at: str
    last_used_at: str | None = None
    expires_at: str | None = None


class AdminTokenList(BaseModel):
    """Every live credential on the instance.

    LIVE ONLY, and deliberately no `include_revoked`: a revoked token is not
    something that could be used against this instance, which is the only
    question this list exists to answer. History is `venona mcp token ls --all`.
    """

    items: list[AdminToken] = Field(default_factory=list)
    #: Every live token, whole set — same honesty as `AdminUserList.total`.
    total: int = 0


# ── the model service ────────────────────────────────────────────────


class ModelRole(BaseModel):
    """One job, and what it will ask for.

    `label` is written by the server for the reason a run's words are: a render
    site that invents English for a wire value is a second place the vocabulary
    can drift, and only one of the two is checked by anything.

    `configured` is what the document says and `resolved` is what the service
    will actually be asked for. They differ whenever a tier name is used, and
    showing both is what makes a tier legible rather than magic.
    """

    role: str
    label: str
    configured: str
    resolved: str
    effort: str | None = None


class ModelPlan(BaseModel):
    """One way to pay a service, and what connecting on it actually takes.

    On the wire per SERVICE rather than per page, because the answer differs by
    both: one service's subscription is a token you can paste and another's is a
    file it writes itself. A page that cannot see that offers the same password
    box for all six combinations, two of which have nothing to put in it.
    """

    mode: Literal["subscription", "api"]
    label: str
    #: These five are `llm/types.py::PlanHelp` on the wire, field for field, and
    #: it carries why each exists -- including why a plan Venona cannot hold a
    #: credential for gets the instruction and no box.
    credential: str
    how: str
    pasteable: bool
    metered: bool
    command: str = ""
    #: Whether this service is connected on THIS plan right now.
    connected: bool = False


class ModelService(BaseModel):
    """A service this instance could use, whether or not it is the current one.

    Listed rather than hidden behind the change control, so an administrator
    can see that an alternative exists, whether it is installed, and whether it
    has anything to authenticate with -- before pressing a button whose verb
    implies they have already decided.
    """

    key: str
    label: str
    installed: bool
    version: str | None = None
    connected: bool
    billing_modes: list[str] = Field(default_factory=list)
    #: `billing_modes` with its human half attached: the label, what connecting
    #: asks for, and whether the plan is metered.
    plans: list[ModelPlan] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class ModelConnection(BaseModel):
    """What the instance is authenticated with, and where that came from.

    `source` is load-bearing rather than decorative. Both vendors read an
    environment variable ahead of their own credential store, and every
    deployment sets one -- so an operator who connects an account needs to be
    told which of the two is actually in force, because the sign-in probe
    cannot tell them.

    NO SECRET, EVER. `hint` is derived at read time from the stored value; a
    stored hint would be a second copy that can drift from what it describes.
    """

    #: `inherited` is the service's own configuration directory, which
    #: authenticates it perfectly well and which Venona did not put there.
    #: Reporting that as `none` would banner a working instance as broken.
    source: Literal["none", "inherited", "environment", "connected", "stored"]
    account: str | None = None
    hint: str | None = None
    verified_at: str | None = None
    #: Whether calls are actually LANDING: the breaker's state, not the sign-in
    #: probe's, which reports a live session for a credential that has expired.
    working: bool | None = None


class ModelSettings(BaseModel):
    """Everything `/admin/model` renders, in one round trip.

    `base_version` rides along because changing the service writes the settings
    document, and a write without one is last-writer-wins over a file somebody
    may have open in the editor next door.
    """

    service: str
    service_label: str
    billing_mode: str
    version: str | None = None
    pinned: str | None = None
    version_matches_pin: bool = True
    connection: ModelConnection
    services: list[ModelService] = Field(default_factory=list)
    roles: list[ModelRole] = Field(default_factory=list)
    #: Why a change would be refused right now, or None when it would be taken.
    #: Reported with the page rather than discovered on submit, so the control
    #: can say what is in the way before it is pressed.
    change_blocked: str | None = None
    #: Whether a model process is locked to a filesystem before it runs. `null`
    #: means this service asks for no restriction. Reported because confining is
    #: opportunistic and fails silently: the call runs either way, so an
    #: administrator who is not told cannot find out.
    confined: bool | None = None
    confinement_detail: str | None = None
    base_version: str | None = None


class ModelSelection(BaseModel):
    """A change to which service does the thinking, or how it is paid for.

    Both optional, so a caller may move one without restating the other. Not a
    response model as well as a request model: a class used for both is
    excluded from the OpenAPI honesty pass, and every field on it would be
    typed optional on the client whether or not the server always sends it.
    """

    service: str | None = None
    billing_mode: Literal["subscription", "api"] | None = None
    base_version: str | None = None


class CredentialInput(BaseModel):
    """A credential pasted by an administrator.

    Write-only by construction: there is no response model carrying `secret`,
    and no route that returns one. Replacing a credential is this same route
    with a fresh value, so no screen can offer an edit control pre-filled with
    a secret.
    """

    secret: str
    billing_mode: Literal["subscription", "api"] = "subscription"
    #: Which service this belongs to, defaulting to the one in use. Naming
    #: another is the ordinary case: you connect a service in order to switch.
    service: str | None = None


class ConnectionEnded(BaseModel):
    """A cancelled attempt or a removed credential.

    A body rather than a 204, because this router is in the route-contract
    sweep and a 204 there is a route with no model. It also lets the page say
    whether anything was actually removed.
    """

    ended: bool
