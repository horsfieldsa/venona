"""The circuit breaker in front of the `claude` binary.

The failure it exists for is an expired OAuth token, and it is nastier than it
sounds because HEALTH CANNOT SEE IT: under `CLAUDE_CODE_OAUTH_TOKEN`,
`claude auth status` returns `loggedIn: true` for any token value including a
garbage one, and omits `subscriptionType`. Verified against 2.1.220. So
`/api/health` shows a green `claude` line while every call 401s.

Without a breaker, one run spends `max_calls_per_run` × the retry count in
subprocess spawns with backoff between them to learn that every time, every
night, reporting `succeeded_partial` each time.
"""

from __future__ import annotations

import pytest
from venona.llm.breaker import FAILURE_THRESHOLD, BreakerOpen, CircuitBreaker


def test_a_closed_circuit_lets_calls_through() -> None:
    breaker = CircuitBreaker()
    breaker.check(now=0.0)  # must not raise


def test_it_opens_only_after_consecutive_failures() -> None:
    """CONSECUTIVE, not a rate. A run failing one call in ten is working; a run
    that has failed six in a row will not succeed on the seventh, and a failure
    rate would blur exactly that difference."""
    breaker = CircuitBreaker()

    for _ in range(FAILURE_THRESHOLD - 1):
        breaker.record_failure("401 unauthorized", now=0.0)
    breaker.check(now=0.0)  # still closed

    breaker.record_failure("401 unauthorized", now=0.0)
    with pytest.raises(BreakerOpen):
        breaker.check(now=0.0)


def test_one_success_resets_the_count() -> None:
    breaker = CircuitBreaker()
    for _ in range(FAILURE_THRESHOLD - 1):
        breaker.record_failure("transient", now=0.0)

    breaker.record_success()
    for _ in range(FAILURE_THRESHOLD - 1):
        breaker.record_failure("transient", now=0.0)

    breaker.check(now=0.0)  # never reached the threshold consecutively


def test_the_refusal_says_what_to_check_and_warns_about_the_lie() -> None:
    """A breaker that says "circuit open" sends someone to read source. This
    one names the SURFACE that can fix it AND the fact that the surface will
    look fine -- a reader of a run message is not at a shell, and the sign-in
    status it would send them to reports a session as live for a dead token."""
    breaker = CircuitBreaker()
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("401 OAuth access token is invalid", now=0.0)

    with pytest.raises(BreakerOpen) as caught:
        breaker.check(now=1.0)

    message = str(caught.value)
    assert "Model in Admin" in message
    assert "expired" in message


def test_it_half_opens_after_the_cooldown() -> None:
    breaker = CircuitBreaker(cooldown=100.0)
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("down", now=0.0)

    with pytest.raises(BreakerOpen):
        breaker.check(now=50.0)

    breaker.check(now=101.0)  # one probe allowed through


def test_a_failed_probe_restarts_the_cooldown(caplog) -> None:
    """Otherwise a still-dead provider gets every subsequent call again, and the
    breaker has bought nothing after the first cooldown elapses."""
    breaker = CircuitBreaker(cooldown=100.0)
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("down", now=0.0)

    breaker.check(now=101.0)  # half-open probe
    breaker.record_failure("still down", now=101.0)

    with pytest.raises(BreakerOpen):
        breaker.check(now=150.0)


def test_a_successful_probe_closes_it() -> None:
    breaker = CircuitBreaker(cooldown=100.0)
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("down", now=0.0)

    breaker.check(now=101.0)
    breaker.record_success()

    assert not breaker.is_open
    breaker.check(now=102.0)


def test_opening_is_reported_exactly_once() -> None:
    """`record_failure` returns True only on the transition, so a notification
    is sent once rather than on every subsequent refusal -- a hundred messages
    about one problem is the same as none."""
    breaker = CircuitBreaker()
    transitions = [breaker.record_failure("down", now=0.0) for _ in range(FAILURE_THRESHOLD + 5)]

    assert transitions.count(True) == 1
    assert transitions.index(True) == FAILURE_THRESHOLD - 1


def test_the_state_is_reportable_for_health() -> None:
    """So a green `claude` line in /api/health cannot hide a dead token."""
    breaker = CircuitBreaker()
    assert breaker.state()["open"] is False

    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("401 OAuth access token is invalid", now=0.0)

    state = breaker.state()
    assert state["open"] is True
    assert state["consecutive_failures"] == FAILURE_THRESHOLD
    assert "401" in str(state["last_error"])


@pytest.mark.negative
def test_breaker_open_is_not_an_llm_error() -> None:
    """`LlmError` means "the model was asked and something went wrong". Nothing
    was asked here, and conflating them would let a retry loop treat a refusal
    to call as a call that failed."""
    from venona.llm.executor import LlmError

    assert not issubclass(BreakerOpen, LlmError)


# ── waiting out a provider incident ──────────────────────────────────


def test_a_provider_error_is_classified_transient_and_a_dead_token_is_not() -> None:
    """The executor waits only through failures that can end on their own. A
    529 will; a 401 will not, however long anyone waits."""
    breaker = CircuitBreaker()
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("529: overloaded_error", now=0.0)
    assert breaker.transient is True
    assert breaker.state()["transient"] is True

    breaker.record_success()
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("401 OAuth access token is invalid", now=0.0)
    assert breaker.transient is False
    assert breaker.state()["transient"] is False


def test_the_episode_outlives_a_failed_probe() -> None:
    """A wait budget is measured against the OUTAGE, not the latest probe:
    `opened_at` restarts on every failed half-open call, and a budget keyed on
    it would never run out while the provider kept answering the probe."""
    breaker = CircuitBreaker(cooldown=100.0)
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("503 service unavailable", now=0.0)

    breaker.check(now=101.0)
    breaker.record_failure("503 service unavailable", now=101.0)

    assert breaker.retry_in(now=150.0) == pytest.approx(51.0)
    assert breaker.episode_age(now=150.0) == pytest.approx(150.0)

    breaker.record_success()
    assert breaker.episode_age(now=200.0) == 0.0
    assert breaker.retry_in(now=200.0) == 0.0


def test_opens_counts_transitions_so_a_run_knows_it_was_there() -> None:
    """`run_daily` compares this before and after: a circuit that opened on the
    run's watch is what earns the re-fire, not one that was open already."""
    breaker = CircuitBreaker(cooldown=10.0)
    assert breaker.opens == 0
    for _ in range(FAILURE_THRESHOLD + 3):
        breaker.record_failure("529 overloaded", now=0.0)
    assert breaker.opens == 1

    breaker.check(now=11.0)
    breaker.record_failure("529 overloaded", now=11.0)  # failed probe: same episode
    assert breaker.opens == 1

    breaker.check(now=22.0)
    breaker.record_success()
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("529 overloaded", now=30.0)
    assert breaker.opens == 2


def test_a_usage_limit_does_not_send_the_reader_to_their_sign_in() -> None:
    """LIVE, 2026-10-22: a session limit tripped the breaker and every refusal
    told the operator a credential might have expired. It had not; the service
    was saying "not now", and the advice cost the evening it was read in."""
    breaker = CircuitBreaker()
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("429: You've hit your session limit", now=0.0)
    with pytest.raises(BreakerOpen) as refused:
        breaker.check(now=0.0)
    assert "credential" not in str(refused.value)
    assert "usage limit" in str(refused.value)


def test_a_dead_credential_still_points_at_the_sign_in() -> None:
    breaker = CircuitBreaker()
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("401 unauthorized", now=0.0)
    with pytest.raises(BreakerOpen) as refused:
        breaker.check(now=0.0)
    assert "credential has expired" in str(refused.value)
