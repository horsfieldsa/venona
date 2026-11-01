"""Codex's own stream, parsed by Codex's own driver, from envelopes it emitted.

The Tier-1 envelope tests cannot be retargeted at this service. Their scars are
about `structured_output`, `permission_denials`, `modelUsage` and
`unifiedWindows`, and a Codex stream has none of those: it is JSONL of
`thread.started` / `turn.started` / `item.completed` / `turn.completed`, the
answer arrives as TEXT that this driver has to parse and validate itself, and
there is no usage-window reading at all.

Every fixture here was emitted by codex-cli 0.151.0 against a real account.
None is hand-written, because the point of the file is the shapes nobody would
think to invent -- the placeholder `agent_message` that arrives before the real
one, the `web_search` item whose JSON carries `id` twice, and an error that is
a JSON document nested inside a string field.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from venona.llm.harness import resolve

CODEX = resolve("codex")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "llm" / "_codex"


def load(name: str) -> str:
    path = FIXTURES / name
    assert path.exists(), f"missing recorded envelope: {path}"
    return path.read_text()


def test_a_structured_reply_is_parsed_out_of_the_text() -> None:
    """Codex returns TEXT where Claude Code returns a validated object.

    So the schema is enforced on this side, by this driver, which is what keeps
    the ranges and caps in the shared schemas meaningful for both services.
    """
    result = CODEX.parse(load("_stream/structured_reply.jsonl"), cache_key="k", duration_ms=1)
    assert result.ok, result.error
    assert result.data == {"answer": "ok", "note": None}


def test_the_token_counts_reach_the_ledger() -> None:
    """A different envelope, the same four columns.

    `turn.completed.usage` is plain counts, not the `modelUsage` map, so
    `Usage.from_envelope` does not apply and the driver constructs it.
    """
    result = CODEX.parse(load("_stream/structured_reply.jsonl"), cache_key="k", duration_ms=1)
    assert result.usage.input_tokens == 13966
    assert result.usage.output_tokens == 19


def test_a_search_does_not_get_mistaken_for_the_answer() -> None:
    """The web_search item comes FIRST and is also an `item.completed`.

    Keyed on the item type rather than on position, because the real stream
    also carries a `web_search` item whose JSON object repeats `id` -- once for
    the item and once for the execution -- and any parse that tried to identify
    the answer by id would read whichever the decoder kept.
    """
    result = CODEX.parse(load("_stream/with_web_search.jsonl"), cache_key="k", duration_ms=1)
    assert result.ok, result.error
    assert "North Atlantic Treaty Organization" in result.data["answer"]


def test_a_provider_refusal_is_reported_and_is_not_a_result() -> None:
    """The shape that opened the breaker on a service that was working.

    The message is a JSON document nested inside a string field, twice over
    (`error.message` holding a serialized envelope). A classifier reading the
    outer text for `500` or `429` would be reading request ids.
    """
    result = CODEX.parse(load("_errors/model_not_available.jsonl"), cache_key="k", duration_ms=1)
    assert not result.ok
    assert "not supported" in (result.error or "")
    assert result.data is None


def test_the_result_line_is_never_forwarded_to_the_transcript() -> None:
    """`turn.completed` carries the call's own outcome. Forwarding it would
    persist a whole brief into `run_events`."""
    for line in load("_stream/with_web_search.jsonl").splitlines():
        event = json.loads(line)
        if event.get("type") == "turn.completed":
            assert not CODEX.is_transcript_event(event)
            break
    else:
        pytest.fail("the recorded stream carries no turn.completed to check")


def test_every_line_of_every_recorded_stream_is_decodable() -> None:
    """The truncation check, in this service's spelling.

    A killed CLI leaves a half-written line, and a fixture recorded from one
    would pass every test above while being a lie about what the service sends.
    """
    files = sorted(FIXTURES.rglob("*.jsonl"))
    assert files, "no recorded Codex envelopes to check"
    for path in files:
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        assert lines, f"{path.name} is empty"
        for i, line in enumerate(lines):
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                pytest.fail(f"{path.name} line {i + 1} is not JSON: {exc}")


def test_a_usage_ceiling_is_transient_and_not_the_end_of_the_run() -> None:
    """The failure that cost a real run four stages and its brief.

    The provider does not always send a typed error. What actually came back
    on hitting the ceiling was prose with no machine code in it:

        You've hit your usage limit. Upgrade to Pro (...), visit (...) to
        purchase more credits or try again at 2:25 PM.

    The code lookup found nothing, every one graded `terminal`, the breaker
    opened after six, and `acronym_search`, `define_write`, `adjudicate` and
    the BRIEF were all abandoned -- to a condition whose own message says when
    it ends. The research lane is built to wait one out (ADR 0155) and was
    never given the chance, because waiting only happens for `transient`.
    """
    ceiling = (
        "You've hit your usage limit. Upgrade to Pro "
        "(https://chatgpt.com/explore/pro), visit "
        "https://chatgpt.com/codex/settings/usage to purchase more credits "
        "or try again at 2:25 PM."
    )
    assert CODEX.classify(ceiling) == "transient"
    assert CODEX.classify("Codex timed out after 300s") == "transient"
    assert CODEX.classify("usage_limit_reached: hit it") == "transient"


def test_a_request_id_full_of_digits_is_still_terminal() -> None:
    """Why the prose match is PHRASES and never numbers.

    This vendor appends a 16-hex `cf-ray` and a 32-hex request id, so roughly
    one error in sixteen contains `500` or `429` by accident. Claude Code's
    driver can match numbers in error text because its errors are short and
    structured; doing it here would be a coin flip.
    """
    noisy = (
        "invalid_request_error: no such model "
        "cf-ray 5001a429bbccddee request_id 429500aabbccddeeff00112233445566"
    )
    assert CODEX.classify(noisy) == "terminal"


def test_the_subscription_windows_are_readable_and_are_a_fraction() -> None:
    """`codex exec` carries no window, and an earlier look stopped there.

    It was the wrong surface. `account/rateLimits/read` on the app-server
    answers in about 190ms, and the strip a reader consults before starting
    another run was drawing "nothing measured" while the answer was one
    request away.

    The recorded exchange is the real one, notification and handshake reply
    included, because the parse has to walk past both to find the id it wants.
    """
    limits = CODEX.parse_quota(load("_quota/rate_limits.jsonl"))
    assert limits is not None, "the recorded exchange produced no reading"

    windows = {w.name: w for w in limits.windows}
    assert set(windows) == {"five_hour", "seven_day"}, (
        "the durations must map onto the names the strip already labels"
    )
    # 0-to-1, not 0-to-100. The vendor reports a percentage and the ledger
    # stores a fraction; converting in the driver is what keeps one render
    # site from disagreeing with another.
    assert windows["five_hour"].utilization == 1.0
    assert windows["seven_day"].utilization == 0.16
    assert windows["five_hour"].resets_at == "2026-09-10T21:25:13Z"
    assert limits.representative == "five_hour"
    assert limits.status == "reached"


def test_a_reading_that_never_arrives_is_absent_and_not_zero() -> None:
    """A window at 0% and a window nobody measured are different claims, and
    the strip renders them differently."""
    assert CODEX.parse_quota("") is None
    assert CODEX.parse_quota('{"id":0,"result":{}}\n') is None
    assert CODEX.parse_quota("not json at all\n") is None


def test_another_model_s_allowance_is_not_read_as_this_one_s_session_window() -> None:
    """The bug this fixture was recorded for, and then read backwards.

    `rateLimitsByLimitId` holds one entry per limit the account has, and on this
    payload the second is keyed `codex_bengalfox` and NAMED
    `GPT-5.3-Codex-Spark`. It is a per-model allowance for a model this driver
    never selects, so its five-hour window reads 0 and always will.

    Folding every bucket together put that 0 on the strip as the session limit.
    It was 0 on all 173 samples recorded on this service while the weekly figure
    climbed correctly -- so the one number an operator checks before starting an
    hours-long sweep was the one number that could not move, and it looked like
    plenty of headroom every time.

    The metered limit is the one `rateLimits` names through `limitId`. On this
    plan it reports no five-hour window at all, and the honest rendering of that
    is a row that is not there: a zero is a measurement and there was none.
    """
    limits = CODEX.parse_quota(load("_quota/rate_limits_two_buckets.jsonl"))
    assert limits is not None

    names = [w.name for w in limits.windows]
    assert names == ["seven_day"], (
        f"expected only the metered limit's own window, got {names}. A window "
        f"read off another model's allowance cannot move."
    )
    assert limits.windows[0].utilization == 0.03
    assert limits.representative == "seven_day"


def test_a_reading_this_plan_does_not_meter_is_absent_rather_than_full() -> None:
    """Absent must not read as unlimited either.

    The other direction of the same honesty: a missing window has to be missing,
    not silently filled in at 0 (headroom nobody measured) and not at 1.0 (a
    run refused over a limit that does not exist). The strip renders the rows it
    is given, so the only correct answer here is one row.
    """
    limits = CODEX.parse_quota(load("_quota/rate_limits_two_buckets.jsonl"))
    assert limits is not None
    assert {w.name for w in limits.windows} == {"seven_day"}
    assert limits.status == "ok"


def test_a_window_name_comes_from_its_length_not_from_its_key() -> None:
    """`primary` is the five-hour window on one plan and the seven-day one on
    another. Keying the name off it would label a weekly limit as a session
    limit -- on the strip a reader consults before starting an hours-long
    sweep."""
    plus = CODEX.parse_quota(load("_quota/rate_limits.jsonl"))
    prolite = CODEX.parse_quota(load("_quota/rate_limits_two_buckets.jsonl"))
    assert plus is not None and prolite is not None

    def named(limits, name):
        return next((w for w in limits.windows if w.name == name), None)

    # On `plus` the five-hour window was `primary` and the seven-day one
    # `secondary`; on `prolite` `primary` is the seven-day window. So the same
    # key carries different lengths, and the name has to come from the length.
    assert named(plus, "five_hour") is not None
    assert named(plus, "seven_day") is not None
    assert named(prolite, "seven_day") is not None
