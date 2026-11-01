"""Wire models for the hub's management surface (docs/adr/0142).

Everything the hub may read or do is HERE and in `authz.MGMT_REACH`, and the
two lists are the same four routes. What the hub can never do is recorded in
CONTRACTS.md rather than implied by omission.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MgmtSetupState(BaseModel):
    """The wizard's state, in the envelope the hub renders verbatim."""

    configured: bool
    slug: str
    display_name: str
    has_dossier: bool
    drafts: list[str] = Field(default_factory=list)
    active_run: str | None = None


class MgmtUsers(BaseModel):
    admins: int
    readers: int


class MgmtStatus(BaseModel):
    version: str
    #: The exact code, where `version` is only the release it belongs to. Between
    #: bumps every domain reports the same number, so this is what tells two of
    #: them apart -- and null is not a gap, it means "not built by a deploy".
    #:
    #: Defaulted like the fields below, for the same reason: an instance that has
    #: not redeployed must render as a quieter card, not a broken one.
    commit: str | None = None
    #: When this process came up. An immutable-image deploy restarts it, so this
    #: is how long the reported build has actually been serving -- which no build
    #: timestamp is available to answer, the host exposing none to a Docker build.
    started_at: str | None = None
    domain_slug: str
    display_name: str
    setup: MgmtSetupState
    #: Corpus size, coarse: enough for a directory row, nothing exportable.
    documents: int
    entities: int
    #: THE GRAPH'S SIZE, which is what the hub's gallery shows somebody deciding
    #: whether this domain is worth reading. A document count measures how much
    #: was fetched; it says nothing about how much was understood, and a domain
    #: that read four hundred articles and drew nothing out of them would look
    #: identical to one that built a graph from them.
    #:
    #: Defaulted like the fields below, so an instance that has not redeployed
    #: reports a quieter card rather than breaking one.
    relationships: int = 0
    users: MgmtUsers
    #: What the paywall is ACTUALLY doing here, which is not the same question
    #: as what the hub last decided. `VENONA_BILLING_ENFORCED` overrides the
    #: hub's switch in both directions, so without this the operator page could
    #: only ever show its own intent back to itself -- and a domain quietly
    #: overriding it would look identical to one obeying.
    billing_enforced: bool = False
    #: This domain in its own words, the sentence it already shows on its own
    #: login page. The hub's gallery describes domains to somebody choosing
    #: between them, and a description each domain writes for itself cannot
    #: drift from what that domain actually is -- which a second field, edited
    #: on the hub, would do within a month.
    login_blurb: str = ""
    #: When reading last landed here. NOT decoration: the gallery shows how many
    #: documents a domain holds, and a count with no recency flatters a corpus
    #: that stopped a month ago. Null on a domain that has never run.
    #:
    #: DEFAULTED, like the two above, so an instance that has not redeployed is
    #: quieter rather than broken -- the hub stores this envelope verbatim and
    #: reads fields out of it.
    latest_brief_at: str | None = None


class ProvisionUser(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: str = Field(default="reader", pattern="^(admin|reader)$")
    action: str = Field(pattern="^(add|update|suspend|reinstate|logout)$")
    # The hub's entitlement projection (ADR 0209). OPTIONAL FIELDS ON THE
    # EXISTING ACTIONS, deliberately not an action of their own: `action` is a
    # pattern constraint, so an unrecognised value is a 422 from validation
    # before the handler runs, and a hub that deployed first would fail every
    # push until this instance caught up. An older instance ignores unknown
    # fields (pydantic's default `extra='ignore'`) and a newer one honours them,
    # so no deployment order breaks.
    #
    # None means "the hub said nothing", which is NOT the same as 'none' meaning
    # "never subscribed" -- the first leaves what is stored alone.
    entitlement_status: str | None = Field(
        default=None, pattern="^(none|trialing|active|past_due|lapsed|comped)$"
    )
    entitled_through: str | None = None
    billing_notice: str | None = Field(default=None, max_length=40)
    billing_notice_at: str | None = None


class ProvisionResult(BaseModel):
    email: str
    action: str
    role: str | None = None
    status: str
    #: Why the instance declined, when `status` is `refused`. The one refusal
    #: that reaches here is "never the last active admin": the hub believes this
    #: person should be demoted or suspended and the instance cannot do it
    #: without leaving itself unadministered. Carried rather than swallowed
    #: because that disagreement is what an operator has to see.
    message: str | None = None


class MgmtDomainIn(BaseModel):
    """The one config push the hub may make: the domain descriptor, validated
    by the same loader every save goes through."""

    content: str = Field(min_length=1, max_length=262_144)


class MgmtDomainSaved(BaseModel):
    slug: str


class MgmtSettingsIn(BaseModel):
    """Switches the hub throws on this instance's behalf.

    A `bool`, not a string flag: the hub is the only caller and it is ours, so
    there is nothing to be lenient about, and a `"false"` that parses as truthy
    would turn a paywall on for a readership nobody meant to charge.
    """

    billing_enforced: bool


class MgmtSettingsSaved(BaseModel):
    """Echoed back so the hub can record what actually landed rather than what
    it sent -- the same reason `MgmtDomainSaved` returns the slug."""

    billing_enforced: bool


class MgmtSetupStarted(BaseModel):
    run_id: str
