"""What the operator surface sends and receives.

A model at every boundary, the same rule the instance holds: without one a
renamed field ships silently and the client finds out. `response_model` also
FILTERS, which is load-bearing on two of these -- a webhook address and a
session secret are both one careless field away from a page that leaks them,
and a model that does not name them cannot serialize them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Session(BaseModel):
    """Who is signed in, and the two counts the rail annotates.

    THE COUNTS LIVE HERE rather than on each page, because the rail shows them
    everywhere and a count that is only correct on the page describing it is
    worse than no count.
    """

    email: str
    pending: int
    stricken_out: int


class Domain(BaseModel):
    slug: str
    display_name: str
    status: str
    last_seen_at: str | None
    poll_misses: int
    #: The release that domain reported at the last poll. Null for one that has
    #: never answered, which the table renders as a dash rather than a blank --
    #: a domain whose version is unknown is not a domain running nothing.
    version: str | None = None


class Overview(BaseModel):
    """The figures, the domains, and what the hub's own two properties are running.

    THE HUB IS A SURFACE TOO, and so is a separate front site. All three deploy
    from one branch on separate build filters, so the hub being a release ahead
    of the domains it polls is an ordinary state and one nothing else could
    show. Its own three fields are read live at the request; the site's are the
    poller's last successful read, five minutes stale like everything else here.
    """

    pending: int
    waiting: int
    accounts: int
    domains: list[Domain]
    #: This hub, read from its installed package at the moment of the request.
    version: str
    #: The exact code. Null off a build host, which the page says as `local`.
    commit: str | None = None
    #: When this process came up, which is how long this build has been serving.
    started_at: str | None = None
    #: A separate front site, as its own build stamped it. Every one of these is
    #: null until the first successful read, and they STAY at the last good
    #: reading afterwards -- a site that missed one read is not a site with no
    #: version, so `site_seen_at` going stale is what says the read is failing.
    site_version: str | None = None
    site_commit: str | None = None
    site_built_at: str | None = None
    site_seen_at: str | None = None


class DomainSetup(BaseModel):
    """The wizard's state, as that domain reported it."""

    configured: bool | None = None
    has_dossier: bool | None = None
    #: Config documents saved but not yet applied, by key.
    drafts: list[str] = []
    #: A research run in flight at the moment of the poll. Five minutes stale by
    #: construction, like everything else here.
    active_run: str | None = None


class DomainHealth(BaseModel):
    """Everything the hub knows about one domain, in one object.

    TWO SOURCES, AND THE SPLIT IS THE POINT. The first block is the hub's own
    columns -- what it decided and what it has observed. The rest is
    `instances.last_status`, the domain's `GET /api/mgmt/status` body stored
    verbatim at the last successful poll. A field that disagrees between the two
    is not a bug to reconcile here; it is the thing an operator needs to see.

    EVERY FIELD IS OPTIONAL below the hub's own. An instance that has not
    redeployed reports a shorter body, and the schema that produces it defaults
    the newer fields for exactly that reason -- so a domain a version behind
    renders as a quieter card rather than a broken one.

    NO SECRET REACHES THIS. `instances` also holds `mgmt_token_sealed` and
    `oidc_client_secret_hash`, and `list_all` is `SELECT *`. A `response_model`
    FILTERS, so what is not named here cannot be serialized -- which is the
    protection, and the reason this object is written out rather than passed
    through as a dict.
    """

    # ── what the hub decided and has seen ────────────────────────────
    id: str
    slug: str
    display_name: str
    tagline: str | None = None
    public_url: str
    #: `live`, `offline`, `provisioning` or `retired`. The poller owns the first
    #: two; an operator may only assert the other two.
    status: str
    last_seen_at: str | None = None
    #: Consecutive failed polls. The notifier acts on this; it is here so the
    #: number behind that message is visible.
    poll_misses: int = 0
    registered_at: str | None = None
    #: A switch the hub has saved and the domain has not confirmed shows as
    #: these two disagreeing.
    settings_version: int = 0
    settings_pushed_version: int = 0

    # ── what the domain said at the last poll ────────────────────────
    #: The version it actually runs, read from its installed package rather than
    #: typed anywhere. Two domains reporting different versions is the drift the
    #: hub exists to make visible.
    version: str | None = None
    #: The exact code inside that release. Between bumps every domain reports
    #: the same `version`, so this is the field that actually tells two of them
    #: apart, and `local` means an instance nobody deployed.
    commit: str | None = None
    #: When that process came up. An immutable-image deploy restarts it, so this
    #: is how long the reported build has been serving there.
    started_at: str | None = None
    #: The slug and name the domain calls ITSELF. Either differing from the
    #: hub's column above means the two disagree about what this domain is.
    reported_slug: str | None = None
    reported_name: str | None = None
    setup: DomainSetup | None = None
    documents: int | None = None
    entities: int | None = None
    #: The graph's size. `assertions` is what `venona graph rebuild`
    #: reconstructs every edge from, so this needs no graph connection and
    #: cannot disagree with the projection.
    relationships: int | None = None
    admins: int | None = None
    readers: int | None = None
    #: What the paywall is ACTUALLY doing there, which is not what the hub last
    #: decided: the environment overrides the switch in both directions.
    billing_enforced: bool | None = None
    #: When reading last landed. A count with no recency flatters a corpus that
    #: stopped a month ago.
    latest_brief_at: str | None = None
    login_blurb: str | None = None
    #: Set when the last poll body was not the shape this schema expects, so a
    #: domain reporting something unreadable says so rather than looking new.
    unreadable: bool = False


class Domains(BaseModel):
    domains: list[DomainHealth]
    #: When any domain last ANSWERED, which is the newest `last_seen_at` across
    #: them -- not when a sweep last ran. The two differ and the difference is
    #: the useful half: with every domain down this goes stale, which is the
    #: reader's signal that nothing here has been confirmed recently rather
    #: than that nothing has changed.
    polled_at: str | None = None


class Request_(BaseModel):
    id: str
    email: str
    note: str
    wanted_slug: str | None
    created_at: str


class Requests(BaseModel):
    rows: list[Request_]
    page: int
    pages: int
    total: int
    #: Every registered slug, for the grant control. The client must not have to
    #: fetch the domains list separately to render one `<select>`.
    domains: list[str]


class Signup(BaseModel):
    id: str
    email: str
    created_at: str


class Signups(BaseModel):
    rows: list[Signup]
    page: int
    pages: int
    total: int


class Membership(BaseModel):
    slug: str
    role: str


class Account(BaseModel):
    email: str
    display_name: str | None
    status: str
    memberships: list[Membership]
    last_login_at: str | None


class Accounts(BaseModel):
    rows: list[Account]
    page: int
    pages: int
    total: int


class SettingsDomain(BaseModel):
    id: str
    slug: str
    display_name: str
    #: What we decided.
    billing_enforced: bool
    #: What the domain says back, or `None` where it has not answered yet. The
    #: two disagreeing is the whole reason this field exists: it tells a switch
    #: that has not arrived from a domain that is ignoring it.
    reported: bool | None
    last_seen_at: str | None
    #: Everything standing between a signed-in stranger and a subscription to
    #: this domain, from `billing_state.blockers` -- the same call the front
    #: door makes to decide which button to draw. Empty means it sells.
    #: `web/src/lib/offer.ts` turns these into sentences.
    blockers: list[str]
    #: What this domain charges a month, in Stripe's minor units, or `None`
    #: where nobody has priced it. Beside the blockers because "no price" is
    #: the one an operator fixes by knowing there is no figure to see.
    price_monthly_amount: int | None
    price_currency: str | None


class SettingsState(BaseModel):
    domains: list[SettingsDomain]
    self_serve: bool
    #: Whether the hub holds Stripe credentials at all. Hub-wide, so it is
    #: stated once rather than repeated on every row, and it is the answer to
    #: the question a per-domain table cannot raise when there are no domains.
    takes_payment: bool


class Destination(BaseModel):
    id: str
    label: str
    #: NEVER the address. A webhook URL is a credential -- anyone holding a
    #: Slack incoming address can post to that channel -- so no route returns
    #: one, and this renders in its place: enough to tell two apart, not enough
    #: to use.
    hint: str
    status: str
    last_error: str | None
    subscribed: list[str]


class NoticeType(BaseModel):
    key: str
    label: str
    detail: str


class Delivery(BaseModel):
    created_at: str
    notice_type: str
    label: str
    status: str
    delivered_at: str | None
    attempts: int
    last_error: str | None


class Notifications(BaseModel):
    destinations: list[Destination]
    types: list[NoticeType]
    history: list[Delivery]


class Ok(BaseModel):
    """The answer to a write that has nothing to report but success.

    A body rather than a 204, so every route on this surface has a
    `response_model` and the contract test needs no exemption for a write.
    """

    ok: bool = True


class GrantIn(BaseModel):
    slug: str = Field(max_length=64)
    role: str = Field(pattern="^(admin|reader)$")


class BillingIn(BaseModel):
    instance_id: str = Field(max_length=64)
    enforced: bool


class SelfServeIn(BaseModel):
    self_serve: bool


class DestinationIn(BaseModel):
    label: str = Field(max_length=200)
    url: str = Field(max_length=2000)


class DestinationRef(BaseModel):
    destination_id: str = Field(max_length=64)


class SwitchIn(BaseModel):
    destination_id: str = Field(max_length=64)
    notice_type: str = Field(max_length=64)
    on: bool
