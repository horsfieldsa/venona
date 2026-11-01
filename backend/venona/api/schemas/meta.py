"""Wire models for `routers/meta.py` — health, the ontology, and the two small
operator actions that live beside them.

`/health` is the first thing anyone checks when the app looks wrong, and one
poll at the app frame answers it for the whole SPA. That is why it is not a bare
dict: `web/src/types/health.ts` aliases these models through the generated
`web/src/api/schema.d.ts`, so a field renamed here fails a typecheck rather than
turning a status pill undefined.

**Every field below that can be absent is optional, and that is not defensive
padding.** `response_model` filters, so a required field the handler omits is a
500 on the health endpoint — which is the one endpoint that must answer when
things are broken. `ClaudeHealth` is the live case: under the replay backend the
handler returns `{ok, detail}` and nothing else, so modelling the `claude_cli`
fields as required would make `/health` fail exactly when `VENONA_OFFLINE=1`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Neo4jHealth(BaseModel):
    """`error` is set and the rest null when the driver cannot reach the graph.

    A degraded graph is not a degraded app — the brief is served from Postgres
    (invariant 1) — so this reports rather than raising.
    """

    ok: bool
    version: str | None = None
    edition: str | None = None
    error: str | None = None


class BreakerState(BaseModel):
    """The circuit in front of the `claude` binary.

    This is what a green `ClaudeHealth.ok` cannot tell you. Under
    `CLAUDE_CODE_OAUTH_TOKEN`, `claude auth status` reports `loggedIn: true` for
    any token value including an expired one, so `ok` means "a token is present"
    and this means "calls are actually working".
    """

    open: bool = False
    consecutive_failures: int = 0
    last_error: str | None = None
    # Only meaningful while open: True when the failure holding the circuit is
    # one the research lane is waiting out (a provider incident), False when it
    # is terminal (a dead token) and nothing will improve on its own.
    transient: bool | None = None


class ModelHealth(BaseModel):
    """Two shapes, one model, discriminated by which fields are present.

    The replay backend returns only `ok` and `detail`; a live one returns the
    probe. See this module's docstring for why that makes every field but `ok`
    optional.

    ONE FIELD IS DELIBERATELY ABSENT: the account the instance is signed in as.
    This route is reader-reachable, and an address is not a reader's to see.
    The admin route serves it.
    """

    ok: bool
    #: Replay backend only — why there is no probe to report.
    detail: str | None = None
    #: Which service does the thinking, and how it is spelled for a reader.
    service: str | None = None
    service_label: str | None = None
    billing_mode: str | None = None
    #: `down`, `waiting`, or `not_connected`. The last is a deliberate state an
    #: administrator put the instance in, and a surface that renders it as an
    #: outage promises a recovery that will not arrive on its own.
    reason: str | None = None
    version: str | None = None
    subscription: str | None = None
    #: `llm.pinned_version`, so a drifted CLI is visible without a second call.
    pinned: str | None = None
    version_matches_pin: bool | None = None
    error: str | None = None
    circuit: BreakerState | None = None
    #: Whether a model process is locked to a filesystem before it runs. `null`
    #: means this service asks for no restriction, which is a different fact
    #: from asking for one and not getting it -- and the difference is
    #: reportable only here, because the call runs either way.
    confined: bool | None = None


#: Kept for one release alongside `model`; see `HealthResponse.claude`.
ClaudeHealth = ModelHealth


class QuotaWindow(BaseModel):
    """One subscription window's latest reading.

    `used` is a 0-to-1 fraction, matching what the CLI reports. The one place it
    becomes a percentage is the component that draws it.
    """

    name: str
    used: float
    resets_at: str
    #: The window the CLI itself considers closest to binding, which is not
    #: always the fullest one.
    representative: bool = False
    observed_at: str


class QuotaHealth(BaseModel):
    """What this instance has left of its subscription.

    `available` separates "nothing observed yet" from "this credential cannot
    see it" -- a token without the profile scope reports no windows forever, and
    a fresh instance reports none until its first call. Rendering those two the
    same way sends someone looking for a bug in the first case and hides one in
    the second.
    """

    available: bool = False
    windows: list[QuotaWindow] = Field(default_factory=list)
    #: Whether the configured service reports windows AT ALL. False means
    #: no run will ever fill this in, which the strip has to say instead of
    #: telling a reader to wait.
    reported: bool = True
    #: allowed | allowed_warning | rejected, across the set.
    status: str | None = None


class RunBrief(BaseModel):
    """Enough to label a run in flight without a second call.

    `subject` because three simultaneous rows all reading "Definition"
    say less than none — the question a reader has is *which* term.
    """

    run_id: str
    kind: str
    lane: str
    label: str
    subject: str | None = None
    #: Both lookup flavours share `kind="lookup"`; this is what tells an acronym
    #: surface from an entity define when a client re-attaches after a reload.
    target_kind: str | None = None
    #: Asked to stop, still running. A cancel is not instant -- the run is given
    #: a few seconds to stop on its own before it is stopped for it -- and a row
    #: that keeps reading "running" through that leaves the person who pressed
    #: cancel with no way to tell whether it worked.
    cancelling: bool = False


class RunsHealth(BaseModel):
    """Contract C11. Both lanes, so the UI can tell "queued behind a full lane"
    from "running"."""

    active: list[RunBrief] = Field(default_factory=list)
    queued: list[RunBrief] = Field(default_factory=list)


class ScheduleHealth(BaseModel):
    """The daily schedule as configured, and when it will actually fire.

    `next_run_at` is null when the cron string cannot be parsed or matches
    nothing within the search horizon -- reported as absence rather than an
    error, for the reason this module's docstring gives: `/health` must answer
    when things are broken. `enabled: false` with a non-null `next_run_at`
    is a real state: the in-process job is off, and the timestamp is what the
    yaml cron *would* fire -- the operator's stated cadence when an external
    cron drives the trigger endpoint instead.
    """

    enabled: bool = False
    cron: str = ""
    timezone: str = ""
    next_run_at: str | None = None
    #: `started_at` of the last daily that succeeded (fully or partially).
    last_daily_at: str | None = None


class DisabledSource(BaseModel):
    id: str
    strikes: int = 0
    last_success_at: str | None = None
    last_error: str | None = None


class StrikingSource(BaseModel):
    id: str
    strikes: int = 0


class SourcesHealth(BaseModel):
    """An auto-disabled source is silent by design — it just stops appearing in
    results. Without this, "the brief got thin" has no visible cause anywhere in
    the product."""

    total: int = 0
    auto_disabled: list[DisabledSource] = Field(default_factory=list)
    striking: list[StrikingSource] = Field(default_factory=list)


class ConfigHealth(BaseModel):
    """Which settings file this process actually read, and how current it is.

    Reported because its absence is what let the reader and the writer point at
    two different files for months: every screen agreed with itself, and no
    screen said which file it was agreeing about.
    """

    #: The directory this instance's editable documents live in.
    dir: str
    #: Which configuration updates this instance's settings file has received.
    version: int


class HealthResponse(BaseModel):
    api: str
    offline: bool
    #: True until the domain descriptor is real (the setup wizard's gate).
    setup_required: bool = False
    neo4j: Neo4jHealth
    #: Both names for one release. The SPA reads `claude` without
    #: optional-chaining the block, so a tab open across the deploy would throw
    #: inside a computed on its next poll if `claude` vanished at once.
    claude: ModelHealth
    model: ModelHealth | None = None
    config: ConfigHealth | None = None
    #: What the subscription has left, beside the CLI that reports it. Always
    #: emitted: `response_model` filters, so a branch that skipped this key
    #: would silently drop the block from the wire.
    quota: QuotaHealth | None = None
    #: The RESEARCH-lane run specifically. `useRunStream` follows this, and the
    #: pipeline is the only run worth watching stage by stage — pointing it at a
    #: 20-second lookup would yank the Runs page off the run someone was reading.
    active_run: str | None = None
    #: True when that run has been ASKED to stop and has not stopped yet. The
    #: grace before it is stopped for it is a window in which `active_run` alone
    #: leaves the person who pressed cancel unable to tell "it ignored me" from
    #: "it is on its way out". ADR 0231.
    active_run_cancelling: bool = False
    runs: RunsHealth
    sources: SourcesHealth
    schedule: ScheduleHealth | None = None
    #: One stamp per domain in `services/freshness.py`, plus `config`. A store
    #: refetches when its own stamp moves, which is how a change made outside
    #: this browser (a nightly run, the CLI, a second tab) reaches a view that
    #: loaded once. A HINT: a stamp moving causes a refetch, a stamp not moving
    #: never suppresses one.
    stamps: dict[str, str] = Field(default_factory=dict)


class LivenessResponse(BaseModel):
    """The only unauthenticated endpoint that touches the app, and deliberately
    the thinnest thing that can be true."""

    status: str


class EntityTypeSpec(BaseModel):
    name: str
    prefix: str
    #: The CSS custom property the frontend colours this type with, so adding a
    #: type in `config/ontology.yaml` reaches the legend without a frontend change.
    token: str
    #: 1-based index into the `--entity-slot-*` palette, and that slot's token.
    #: `lib/entityTokens.ts` injects `token: var(slot_token)` at boot, which is
    #: how a generated domain's types colour without a build.
    color_slot: int = 0
    slot_token: str = ""
    description: str
    org_types: list[str] = Field(default_factory=list)
    platform_types: list[str] = Field(default_factory=list)
    #: The types this one is routinely READ AS, so a surface shared across one of
    #: these pairs is one thing filed twice rather than two things. On the wire
    #: because the curation queue explains the distinction to a reader, and a
    #: `response_model` that omits a field drops it silently.
    confusable_with: list[str] = Field(default_factory=list)


class PredicateSpec(BaseModel):
    name: str
    domain: list[str] = Field(default_factory=list)
    range: list[str] = Field(default_factory=list)
    symmetric: bool = False
    description: str
    example: str | None = None


class RewriteSpec(BaseModel):
    """One row of the ontology's `rewrites:` table — an ill-typed shape
    `analyze` rewrites to the legal edge instead of rejecting. Served so the
    rule the pipeline enforces is visible in Admin → Ontology, not only in the
    YAML."""

    predicates: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    to_predicate: str
    swap: bool = False
    note: str | None = None


class OntologyResponse(BaseModel):
    """The research↔graph contract (C10), served to the UI. Filter chips, the
    graph legend and the entity colour mapping are all built from this."""

    #: An INTEGER: `Ontology.version` is `int`, and typing it as a string here
    #: served a 500 from the endpoint the graph legend and every filter chip are
    #: built from.
    version: int
    entity_types: list[EntityTypeSpec] = Field(default_factory=list)
    predicates: list[PredicateSpec] = Field(default_factory=list)
    rewrites: list[RewriteSpec] = Field(default_factory=list)


class EnableSourceResponse(BaseModel):
    """Clearing strikes is not a claim that the source is fixed — the next run
    tries again, and earns three fresh strikes if the cause is still there."""

    id: str
    auto_disabled: bool
    strikes: int


class AnnotatableSite(BaseModel):
    """One site the browser extension may be granted permission to annotate.

    Served rather than carried by hand in `manifest.json`'s
    `content_scripts.matches`: a hand-kept list has nothing generating it and
    nothing checking it, so it drifts — five hosts named there against fourteen
    the corpus had actually produced documents from, with every domain the
    discovery sweeps found invisible by construction, since a `kind: discovery`
    source has no `url` for anyone to copy across.

    **`match` is built here, not in the client.** It is the one string that goes
    straight into `chrome.permissions.request`, so it is minted and validated in
    the same place, through `sources/urlguard.py::check_static` — the guard the
    fetcher already uses, rather than a second idea of a safe host free to drift
    from the first.

    The pattern is `https://*.{domain}/*`. Chrome matches `*.example.com`
    against `example.com` itself as well as its subdomains, which is what
    recovers the `www.` that `canonical_url` strips on the way into
    `documents.site_domain`.

    **This list is OFFERED, never APPLIED.** Nothing here grants anything: every
    host still needs an explicit permission the user approves and can revoke.
    That is the property `extension/README.md` bought when it removed
    `https://*.gov/*` and `https://*.mil/*` from the manifest, and it survives
    this endpoint intact.
    """

    #: Bare host, `www.` stripped — the same form `documents.site_domain` holds.
    domain: str
    #: Chrome match pattern. See the class docstring; do not rebuild it client-side.
    match: str
    publisher: str | None = None
    #: Canonical documents only. `duplicate_of` rows are excluded, so this is
    #: articles rather than stored rows — it is what the user sorts by.
    documents: int = 0
    #: True when this is the host of an enabled `kind: rss` source. Such a site
    #: appears even at `documents: 0`, so a fresh checkout with no runs yet
    #: still offers the configured publishers instead of an empty page.
    configured: bool = False
    #: ISO-8601 UTC with `Z`. Null for a configured feed that has produced nothing.
    last_seen_at: str | None = None


class AnnotatableSites(BaseModel):
    """The extension's site catalog. Sorted by `documents` descending, then domain."""

    sites: list[AnnotatableSite] = Field(default_factory=list)


class RescoreResponse(BaseModel):
    """Reweighting every stored cluster from its per-axis scores.

    `model_calls` is always 0 and is reported rather than assumed: it is the
    whole reason the model is never asked for an overall score, and the number a
    reader checks before pressing this on a corpus of several hundred clusters.
    """

    clusters: int
    changed: int
    tier_changes: int
    missing_axes: int
    weights: dict[str, float] = Field(default_factory=dict)
    model_calls: int = 0


class GuideRun(BaseModel):
    """One formatting run inside a guide block, contract C3's shape.

    The same vocabulary the brief uses, minus the entity fields: a guide is not
    annotated, so `r`/`k`/`c` never appear. Flags are absent rather than false
    for the reason C3 gives — a `"b": false` on every run of a 200-block
    document spends the payload budget on nothing.
    """

    t: str
    b: bool | None = None
    i: bool | None = None
    s: bool | None = None
    code: bool | None = None
    href: str | None = None


class GuideBlock(BaseModel):
    """A block of the user guide.

    `heading` carries `level` and `slug`; `table` carries `head` and `rows` and
    no runs of its own; everything else carries `runs`. One flat model rather
    than a union because the client renders by `type` and a discriminated union
    on the wire would buy nothing it does not already have.
    """

    type: str
    depth: int = 0
    text: str = ""
    runs: list[GuideRun] = Field(default_factory=list)
    #: Headings only.
    level: int | None = None
    slug: str | None = None
    #: `list_item` only — whether its list was numbered, and its position in
    #: that list. The guide's sections are cross-referenced BY number ("see
    #: section 7"), so rendering them as bullets loses a reference.
    ordered: bool | None = None
    number: int | None = None
    #: `blockquote` only — the callout's tone (`info`, `warn`, `stop`), from a
    #: GitHub `> [!NOTE]` marker, and whether this block opens or closes the
    #: box. A two-paragraph callout is two blocks inside ONE frame, and a flat
    #: list cannot work that out at render time.
    tone: str | None = None
    first: bool | None = None
    last: bool | None = None
    #: Tables only. Each cell is a list of runs, so a cell can hold emphasis and
    #: links like any other text.
    head: list[list[list[GuideRun]]] = Field(default_factory=list)
    rows: list[list[list[GuideRun]]] = Field(default_factory=list)


class Guide(BaseModel):
    """`docs/USER-GUIDE.md`, parsed.

    `blocks` is empty when the file is absent, which is not an error: a
    deployment that trimmed `docs/` should lose the help page rather than the
    app, and the client says so.
    """

    blocks: list[GuideBlock] = Field(default_factory=list)


class McpTool(BaseModel):
    """One tool, as the settings page lists it.

    Served rather than hard-coded in the Vue component for the reason
    `AccountView.vue`'s docblock gives about rendering only what has a backend:
    a hand-maintained copy of this list in the frontend is a second source of
    truth, and it goes stale silently the first time a tool is added.
    """

    name: str
    title: str
    summary: str


class McpToken(BaseModel):
    """A minted agent credential. Note what is ABSENT: the token.

    venona stores a SHA-256, so this endpoint could not serve the secret even if
    someone added the field -- which is the property that lets the page list
    credentials at all. `prefix` is the first eleven characters, enough to match
    a row against what is pasted in a client config and useless on its own.

    `scope`, `expires_at` and `client_name` are here so the page can tell one
    row from another, which only started mattering when each grew a Disconnect
    button beside it. `scope` separates an assistant from the browser extension
    -- both are the reader's, both land in this list, and unplugging the wrong
    one is a silent mistake. `client_name` is the name a client registered
    under, or None for a credential typed in at a terminal. It is
    ATTACKER-SUPPLIED TEXT, because registration is unauthenticated; every
    render of it escapes.
    """

    id: str
    label: str
    prefix: str
    scope: str
    created_at: str
    last_used_at: str | None = None
    expires_at: str | None = None
    client_name: str | None = None


class Disconnected(BaseModel):
    """What ending a connection reports.

    `ended` is whether THIS call was the one that ended it. False means it was
    already gone -- revoked from another tab, expired out, or double-clicked --
    which is worth telling the reader apart from success, and is not an error in
    either direction. Contract C7's convention: a route's answer is metadata, and
    the list is re-read rather than patched in place.
    """

    ended: bool


class McpInfo(BaseModel):
    """What `Settings -> Agents` renders.

    `path` rather than a URL: the API sits behind the reverse proxy and does not know its own
    scheme or host -- the same fact the reverse proxy config cites for owning the HSTS
    header. The browser joins this to `window.location.origin`, and says so on
    screen, rather than printing an address an agent on another machine may not
    be able to reach.
    """

    path: str
    protocol_versions: list[str] = Field(default_factory=list)
    auth_required: bool
    tools: list[McpTool] = Field(default_factory=list)
    tokens: list[McpToken] = Field(default_factory=list)
