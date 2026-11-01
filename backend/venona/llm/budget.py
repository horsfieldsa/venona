"""Run pacing and rate-limit handling.

On a subscription the binding constraint is RATE LIMITS, not dollars. There is
no spend to manage: the work is already paid for, and a dollar cap on a
subscription call buys nothing except a way to abort work that costs nothing
extra to finish. `--max-budget-usd` is not passed, and there is no
`max_budget_usd_per_call` setting.

Cost is still MEASURED -- `llm_calls` ledgers every call and `cost_usd` is a run
counter -- because "why did this run take so long / hit the rate limit" is
answered by looking at where the tokens went. Measurement is not management, and
conflating the two is what put a spend cap on a flat-rate plan.

The bound that remains is `max_calls_per_run`, which is the honest one: it
limits how much WORK a run may attempt, degrades to `succeeded_partial`, and has
fired in anger (run 06a738f3, 34 clusters deferred).

The important behaviour: when that cap is hit the run does NOT fail. Remaining
work is deferred, persist and brief still run on what was gathered, and the run
ends `succeeded_partial`. A partially useful run reported as a failure trains
you to ignore the status.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

from venona.settings import LlmSettings


class BudgetExceeded(Exception):
    """Raised when a run has spent its allowance. Callers must degrade to
    `succeeded_partial`, never to `failed`."""


@dataclass
class Budget:
    """Call count is the cap. There is deliberately no wall-clock cap.

    An earlier version carried `max_run_seconds` and a `tick()` to feed it.
    Nothing ever called `tick()`, so `_elapsed` was permanently 0 and the check
    could not fire -- a config knob (`llm.max_run_seconds: 2400`) that read as a
    safety limit and enforced nothing. Removed rather than wired up: the honest
    bound on a run is how many model calls it may make, and `max_calls_per_run`
    already enforces that, has fired in anger (run 06a738f3, 34 clusters
    deferred), and degrades to `succeeded_partial` correctly.

    A time cap would also be the wrong shape here. Stages are resumable by
    state, so a run cut off mid-analyze is re-entered by the next `venona run`;
    a cap measured in seconds would mostly interrupt the slowest -- which is to
    say the most expensive -- call at random.
    """

    settings: LlmSettings
    calls: int = 0
    cost_usd: float = 0.0
    deferred: int = 0

    def check(self) -> None:
        if self.calls >= self.settings.max_calls_per_run:
            raise BudgetExceeded(
                f"run hit its call cap ({self.settings.max_calls_per_run}); remaining work deferred"
            )
        # THE DOLLAR CEILING, AND ONLY WHEN DOLLARS ARE REAL. ADR 0057 removed
        # a spend cap because on a flat-rate plan the work is already paid for
        # and a cap only aborts calls that cost nothing extra to finish. That
        # argument does not survive metered billing, where "Look up all" can
        # spend tens of dollars from one press with nothing between it and the
        # card. A subscription still ignores this entirely (ADR 0218).
        if (
            self.settings.billing_mode == "api"
            and self.cost_usd >= self.settings.max_spend_usd_per_run
        ):
            raise BudgetExceeded(
                f"run hit its spend cap (${self.settings.max_spend_usd_per_run:.2f}); "
                f"remaining work deferred"
            )

    def charge(self, cost_usd: float) -> None:
        self.calls += 1
        self.cost_usd += cost_usd

    def defer(self, n: int = 1) -> None:
        self.deferred += n

    def deferral(self, n: int, unit: str) -> str:
        """Record `n` deferred units of work and return the sentence to report.

        Four stages hit the cap and every one of them wrote its own sentence
        about it: two wrapped `check()`'s message in a second parenthetical, one
        rebuilt it from the setting by hand, and one prefixed its own stage name
        and produced a parenthetical inside a parenthetical. The facts were the
        same in all four, so a reader comparing two runs was reading four
        vocabularies for one event.

        The unit stays the caller's, because only the caller knows what it left
        on the floor. Everything else is the same sentence.
        """
        self.defer(n)
        return (
            f"This run reached its limit of {self.settings.max_calls_per_run} model calls, "
            f"so {n} {unit} were left for next time."
        )

    @property
    def remaining(self) -> int:
        """Calls still allowed. Never negative.

        For a stage that dispatches CONCURRENTLY: `check()` answers "may I make
        one more", which a batch of N would overshoot by up to N-1. Sizing the
        batch against this keeps a parallel stage inside the same cap a serial
        one respected. It is still an approximation -- a call that retries
        charges more than once -- but the error is bounded by the retry count
        rather than by the concurrency.
        """
        return max(0, self.settings.max_calls_per_run - self.calls)

    @property
    def summary(self) -> str:
        return f"{self.calls} calls, ${self.cost_usd:.3f}" + (
            f", {self.deferred} deferred" if self.deferred else ""
        )


# Transient conditions worth retrying. Matched loosely because the CLI surfaces
# them as strings rather than typed errors.
_RETRYABLE = (
    "429",
    "rate_limit",
    "rate limit",
    "overloaded",
    "529",
    "500",
    "502",
    "503",
    "504",
    "timed out",
    "timeout",
)


def is_retryable(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    return any(token in lowered for token in _RETRYABLE)


async def backoff(attempt: int, *, rng: random.Random | None = None, first: float = 1.0) -> float:
    """Exponential with jitter. Jitter matters because a run fires several calls
    concurrently, and without it they all retry in lockstep.

    `first` is how long the FIRST wait is, and it is what decides whether this
    is patience or a formality. Doubling from one second gives waits of about a
    second and about two, which is the right amount for a lock contending and
    nothing like enough for a provider having a bad minute -- the condition the
    retry exists for. `llm.retry_base_seconds` is what the pipeline passes.

    Callers inside the pipeline pass `ctx.rng`, which is seeded in offline mode.
    The unseeded fallback exists only for direct/ad-hoc use; it is unreachable
    under VENONA_OFFLINE=1 because replay serves fixtures and never produces a
    retryable error. Do not remove the parameter -- that assumption is only safe
    while callers can inject.
    """
    delay = next_delay(attempt, rng=rng, first=first)
    await asyncio.sleep(delay)
    return delay


def next_delay(attempt: int, *, rng: random.Random | None = None, first: float = 1.0) -> float:
    """How long the next wait will be, without taking it.

    Split out so a caller can SAY the number before sleeping it. Announcing the
    un-jittered base instead promises a wait up to twice the one happening, on
    the one figure a strip reading "retrying in Ns" exists to carry -- and
    recomputing the curve at the announcement site puts it in two modules that
    have to be edited together.
    """
    rng = rng or random.Random()  # determinism: ok - see backoff; ctx.rng injected in-pipeline
    base = min(60.0, first * 2.0**attempt)
    return base * (0.5 + rng.random() * 0.5)
