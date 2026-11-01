"""The shapes contract C2 is written in, independent of any one harness.

These live apart from `executor.py` because a harness driver both produces them
and raises them: `llm/harness/claude_code.py` builds an `LlmResult` and raises
`PermissionDenied`, while `executor.py` imports the driver table. With the types
in either of those two modules the import graph closes on itself.

Nothing here knows which binary ran. `Usage.from_envelope` is the one exception
and says so: it reads Claude Code's `modelUsage` shape, and a harness whose
envelope carries plain token counts constructs `Usage(...)` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class LlmError(RuntimeError):
    """Raised for conditions the pipeline must never silently absorb."""


class PermissionDenied(LlmError):
    """A tool the call depended on was denied. Never degrade this to 'no result'."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    web_search_requests: int = 0
    web_fetch_requests: int = 0

    @classmethod
    def from_envelope(
        cls, usage: dict[str, Any], model_usage: dict[str, Any] | None = None
    ) -> Usage:
        """Server-tool counts come from `modelUsage`, NOT from `server_tool_use`.

        Measured against 2.1.220 with a call that demonstrably searched (two
        `tool_use` blocks in the transcript, a URL returned):

            usage.server_tool_use.web_search_requests          0
            modelUsage["claude-haiku-4-5"].webSearchRequests    1

        The top-level block is present and always zero. The work is done by the
        haiku sub-model, so the count is attributed to it -- exactly the shape
        contract C2 already records for COST ("`modelUsage` carries a phantom
        haiku entry; cost is the sum over the map"). The same lesson applies to
        tool counts and was missed: `llm_calls.web_search_requests` read 0 on
        every row ever written, including the discovery sweeps that are ~40% of
        a run's spend and consist almost entirely of searching.

        A column that says 0 is worse than a column that says NULL -- it reads
        as "no searches happened" rather than "nobody measured".

        `server_tool_use` is kept as a fallback in case a future CLI populates
        it; `max` rather than a sum, so a version that fills in both does not
        double-count.
        """
        server_tools = usage.get("server_tool_use") or {}

        def across_models(key: str) -> int:
            return sum(int((m or {}).get(key, 0) or 0) for m in (model_usage or {}).values())

        return cls(
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
            web_search_requests=max(
                server_tools.get("web_search_requests", 0) or 0,
                across_models("webSearchRequests"),
            ),
            web_fetch_requests=max(
                server_tools.get("web_fetch_requests", 0) or 0,
                across_models("webFetchRequests"),
            ),
        )


@dataclass(frozen=True)
class QuotaWindow:
    """One subscription window, as the harness reports it.

    `utilization` stays the 0-to-1 FRACTION rather than being scaled to a
    percentage here. Storing what the source said keeps the ledger honest about
    its own provenance, and one conversion at the render boundary cannot
    disagree with another. A harness reporting 0-to-100 divides in its driver,
    where the fact that it does is visible next to the parse that reads it.
    """

    name: str
    utilization: float
    resets_at: str


@dataclass(frozen=True)
class RateLimits:
    """What binds on a subscription. See ADR 0057 for why this, not dollars.

    `representative` answers "which limit is about to stop me". A harness whose
    protocol names that window uses it, because the fullest is not always the
    one the vendor's own warning names. One whose protocol does NOT reports the
    fullest and is no worse than its input: codex's `rateLimitReachedType` says
    a limit was hit, not which of its windows, so `codex.parse_quota` takes the
    maximum deliberately.

    A harness with no window reading leaves `LlmResult.rate_limits` None, which
    the quota surface already treats as "nothing measured" rather than as zero.
    """

    status: str
    windows: tuple[QuotaWindow, ...] = ()
    representative: str | None = None
    using_overage: bool = False
    surpassed_threshold: float | None = None


@dataclass(frozen=True)
class LlmResult:
    data: dict[str, Any] | None
    usage: Usage
    models: dict[str, float] = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_ms: int = 0
    refused: bool = False
    error: str | None = None
    cache_key: str = ""
    #: Present when the harness reported its usage windows on this call. Absent
    #: under replay, absent for a credential without the scope to see it, and
    #: absent for a harness that does not report them at all.
    rate_limits: RateLimits | None = None
    #: How `cost_usd` was arrived at. One column holds four kinds of number and
    #: summing across them is meaningless. `list_price` is a
    #: subscription's shadow price (ADR 0180), `measured` is what a metered
    #: provider billed, `computed` is tokens multiplied by a price table this
    #: repo maintains, and `none` is a harness that reports neither.
    cost_basis: str = "list_price"

    @property
    def ok(self) -> bool:
        return self.data is not None and self.error is None and not self.refused


@dataclass(frozen=True)
class HarnessHealth:
    """What a probe of the harness's own CLI could establish.

    NOTE WHAT THIS CANNOT TELL YOU. A status probe reporting a logged-in session
    means a credential is PRESENT, not that calls work: under a pasted token the
    `claude` CLI reports `loggedIn: true` for any value, expired or garbage. The
    circuit breaker is what knows whether calls are actually landing, and
    `/api/health` serves both for that reason.
    """

    ok: bool
    version: str | None = None
    logged_in: bool = False
    #: The account the harness says it is signed in as. ADMIN SURFACES ONLY --
    #: `GET /api/health` is in `authz.READER_ROUTES`, so this must not reach it.
    account: str | None = None
    subscription: str | None = None
    version_matches_pin: bool = True
    error: str | None = None
    #: Whether a model process from this service is locked to a filesystem
    #: before it runs. Reported rather than assumed: the restriction is
    #: opportunistic, so believing a call was confined when the kernel refused
    #: is the one failure mode of confining at all, and it is silent. `None`
    #: means this service asks for no restriction -- a different fact from
    #: asking and not getting one.
    confined: bool | None = None
    confinement_detail: str | None = None


@dataclass(frozen=True)
class PlanHelp:
    """What a service wants in exchange for answering, in a reader's words.

    ONE VENDOR'S FACT, so it belongs beside that vendor's driver rather than in
    the page. Each service means something different by "connect": one mints a
    long-lived token you can paste, another writes a file in its own home when
    you run its sign-in command, and their metered keys come from three
    different consoles. A page that says "Paste the credential" is asking a
    question only the driver can answer, of somebody who has to guess.

    `pasteable` is `env_for(mode) is not None` said plainly. A mode Venona
    cannot hold has no box, because the box is the lie: it accepts a value,
    verifies nothing, and the run fails later somewhere unrelated.
    """

    #: What the thing IS. "a long-lived token", "an API key".
    credential: str
    #: Where it comes from, as an instruction that can be followed.
    how: str
    #: Whether Venona can hold this kind at all, or the service keeps its own.
    pasteable: bool
    #: Whether this mode is charged per token. Said out loud because the two
    #: plans bill in completely different ways and the page is where somebody
    #: chooses between them.
    metered: bool
    #: The command to run, exactly, where Venona is deployed -- or empty when
    #: this plan needs none. Separate from `how` so a page can offer it to be
    #: copied rather than transcribed: an administrator reading this has already
    #: had to open a shell on another machine, and a command retyped from prose
    #: is the step that goes wrong.
    command: str = ""
