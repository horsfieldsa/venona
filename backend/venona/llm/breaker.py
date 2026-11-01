"""A circuit breaker in front of the `claude` binary.

THE FAILURE THIS EXISTS FOR is an expired or revoked OAuth token, and it is
worse than it sounds because health cannot see it. Under
`CLAUDE_CODE_OAUTH_TOKEN` -- the headless deployment path -- `claude auth status`
returns `loggedIn: true` for ANY token value, including a garbage one, and omits
`subscriptionType` entirely (verified against 2.1.220). So `/api/health` shows a
green `claude` line while every single call fails with a 401.

Without a breaker, one run then spends `max_calls_per_run` × the retry count
in subprocess spawns, each with backoff between them, to discover the same
thing every time -- a thousand of them at the settings this ships with. The
nightly run does that every night. Nothing says so, because
a run that produced nothing is a `succeeded_partial` with warnings.

THE SECOND FAILURE is the provider itself being down -- a 5xx/529 incident of
the kind Anthropic's status page records every few weeks, typically for twenty
to forty minutes. The breaker classifies each failure as TRANSIENT (the
provider said "not right now": 429, 5xx, overloaded, a timeout) or TERMINAL
(401, a missing binary, a malformed call), and exposes that so the executor can
WAIT through a transient episode instead of refusing the rest of a run in
seconds. A 06:00 run that meets a 26-minute incident should produce the day's
brief 26 minutes late, not skip it.

WHAT IT DOES NOT DO: it never turns a failure into a success, and it never
degrades a run to `failed`. `BreakerOpen` is raised where an `LlmError` would
have been, so every existing handler -- the budget's degrade-to-partial, the
stage-level warnings -- behaves exactly as before, only sooner and without the
subprocess. The point is to stop paying, not to change the verdict.

WHY NOT AT THE `runner.py` LEVEL: retries live there, and a breaker above the
retry loop would count one logical call as one failure rather than three. Below
it, at the executor, it counts what actually happened -- which is what "the
provider is down" is measured in.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from venona.llm.budget import is_retryable

log = logging.getLogger("venona.llm")

# Enough that a couple of genuinely bad prompts, or one flaky minute, do not
# trip it -- and few enough that a dead credential is caught inside the first
# stage rather than the first run.
FAILURE_THRESHOLD = 6

# How long an open circuit stays open before letting ONE call through. Long
# enough that a re-auth is plausible, short enough that a transient provider
# outage does not cost the rest of a run.
COOLDOWN_SECONDS = 120.0


class BreakerOpen(RuntimeError):
    """The circuit is open; the call was not attempted.

    A subclass of RuntimeError rather than of LlmError on purpose: an LlmError
    means "the model was asked and something went wrong", and that is not what
    happened here. Nothing was asked.
    """


@dataclass
class CircuitBreaker:
    """Closed -> open on consecutive failures -> half-open after a cooldown.

    Deliberately counts CONSECUTIVE failures. A run that fails one call in ten
    is working; a run that has failed six in a row is not going to succeed on
    the seventh, and the difference between those two is what a plain failure
    RATE would blur.
    """

    threshold: int = FAILURE_THRESHOLD
    cooldown: float = COOLDOWN_SECONDS
    #: How a failure is graded. Supplied by the executor from the active
    #: harness, because "is this transient" is a question about one vendor's
    #: error vocabulary: a symbolic code like `usage_limit_reached` matches
    #: none of the numeric tokens `is_retryable` looks for, so a shared default
    #: would grade a rate limit as terminal and abort a run at the first
    #: refusal. The default is Claude Code's, which is what a directly
    #: constructed breaker has always meant.
    classify: Callable[[str | None], str] = field(
        default=lambda error: "transient" if is_retryable(error) else "terminal", repr=False
    )

    consecutive_failures: int = 0
    opened_at: float | None = None
    last_error: str = ""
    # Whether the failure that is holding the circuit open looked like the
    # provider saying "not now" (retryable) rather than "never" (a dead token,
    # a missing binary). Decides whether waiting is worth anything.
    transient: bool = False
    # When the current open EPISODE began. `opened_at` restarts on every failed
    # half-open probe; this does not, so a wait budget is measured against the
    # outage as a whole rather than against its most recent probe.
    episode_started_at: float | None = None
    # Closed->open transitions, ever. A run that compares this before and
    # after knows whether the circuit opened on its watch.
    opens: int = 0
    _probing: bool = field(default=False, repr=False)

    def check(self, *, now: float) -> None:
        """Raise if the call should not be attempted."""
        if self.opened_at is None:
            return
        if now - self.opened_at < self.cooldown:
            waited = int(now - self.opened_at)
            # A usage limit is the service saying "not now", and sending the
            # reader to re-check a sign-in that is fine costs them the evening.
            advice = (
                "The service is refusing calls for now, which a usage limit and an "
                "outage both look like."
                if self.transient
                else "Check the connection under Model in Admin: a sign-in check can "
                "report a session as live even when its credential has expired."
            )
            raise BreakerOpen(
                f"the model service has failed {self.consecutive_failures} times in "
                f"a row ({self.last_error}); not retrying for another "
                f"{int(self.cooldown) - waited}s. {advice}"
            )
        # Half-open: let exactly one through and see.
        self._probing = True

    def retry_in(self, *, now: float) -> float:
        """Seconds until the next half-open probe is allowed. 0 when closed."""
        if self.opened_at is None:
            return 0.0
        return max(0.0, self.cooldown - (now - self.opened_at))

    def episode_age(self, *, now: float) -> float:
        """How long the current open episode has lasted. 0 when closed."""
        if self.episode_started_at is None:
            return 0.0
        return max(0.0, now - self.episode_started_at)

    def record_success(self) -> None:
        if self.opened_at is not None:
            log.warning("claude circuit closed after a successful call")
        self.consecutive_failures = 0
        self.opened_at = None
        self.episode_started_at = None
        self.last_error = ""
        self.transient = False
        self._probing = False

    def record_failure(self, error: str, *, now: float) -> bool:
        """Returns True if this failure OPENED the circuit (so a caller can
        notify exactly once, rather than on every subsequent refusal)."""
        # A REFUSAL IS NOT DAMAGE. The model declining, or being handed more
        # than its context can hold, fails identically on retry and says
        # nothing about whether the service is reachable -- so counting it
        # opens the circuit on a run whose calls were all working, aborts the
        # rest of it, and reports the service down.
        verdict = self.classify(error)
        if verdict == "refusal":
            return False

        self.consecutive_failures += 1
        self.last_error = error[:200]
        self.transient = verdict == "transient"

        if self._probing:
            # The half-open probe failed. Restart the cooldown rather than
            # letting every subsequent call through.
            self._probing = False
            self.opened_at = now
            return False

        if self.opened_at is None and self.consecutive_failures >= self.threshold:
            self.opened_at = now
            self.episode_started_at = now
            self.opens += 1
            log.error(
                "claude circuit OPEN after %d consecutive failures: %s",
                self.consecutive_failures,
                self.last_error,
            )
            return True
        return False

    @property
    def is_open(self) -> bool:
        return self.opened_at is not None

    def state(self) -> dict[str, object]:
        """For `/api/health`, so a green CLI line cannot hide a dead token."""
        return {
            "open": self.is_open,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error or None,
            "transient": self.transient if self.is_open else None,
        }
