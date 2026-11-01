"""Reading the subscription's remaining room off a call we were making anyway.

The whole point of this path is that it costs nothing: the CLI emits a
`rate_limit_event` on the stream `executor.py` already parses, so there is no
second request and no second decision to get wrong. What CAN go wrong is silent
-- a reading that never arrives leaves a meter at zero, which reads as an
untouched subscription rather than as no data. Hence the None cases below get as
much attention as the happy one.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from venona.llm.executor import parse_envelope
from venona.llm.harness.claude_code import _rate_limits

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "llm"
STREAM = FIXTURES / "_stream" / "rate_limit_event.jsonl"
ERRORS = FIXTURES / "_errors"


def test_reads_every_window_from_a_real_event() -> None:
    limits = _rate_limits(STREAM.read_text())

    assert limits is not None
    assert {w.name for w in limits.windows} == {"five_hour", "seven_day"}
    assert limits.status == "allowed_warning"
    # The CLI names the window closest to binding, and it is not always the
    # fullest one -- reporting the max instead would occasionally disagree with
    # the CLI's own warning.
    assert limits.representative == "seven_day"
    assert limits.surpassed_threshold == 0.75
    assert limits.using_overage is False


def test_utilization_stays_the_fraction_the_cli_reported() -> None:
    """Not scaled to a percentage here. One conversion, at the render boundary,
    so a second copy cannot disagree with it."""
    windows = {w.name: w for w in _rate_limits(STREAM.read_text()).windows}

    assert windows["seven_day"].utilization == 0.8
    assert windows["five_hour"].utilization == 0.1


def test_resets_at_is_converted_to_our_wire_format() -> None:
    """`resetsAt` is unix epoch SECONDS; every timestamp on our wire is ISO-8601
    UTC with `Z`, and the string comparisons elsewhere depend on it."""
    windows = {w.name: w for w in _rate_limits(STREAM.read_text()).windows}

    assert windows["seven_day"].resets_at == "2026-08-30T21:00:00Z"
    assert windows["five_hour"].resets_at == "2026-08-31T00:10:00Z"


def test_a_transcript_that_carries_a_reading_still_parses_as_a_result() -> None:
    """The ordinary production case: one transcript, both facts. A parser that
    consumed the event lines and lost the envelope would fail every call."""
    result = parse_envelope(STREAM.read_text())

    assert result.ok
    assert result.data is not None
    assert result.rate_limits is not None
    assert result.rate_limits.representative == "seven_day"


def test_recorded_fixtures_go_on_parsing_unchanged() -> None:
    """Not one of them may be rekeyed, re-recorded or made to raise.

    The corpus is two formats: 44 single-object envelopes from before the
    streaming switch, and 71 transcripts from after it. Both must survive, which
    is the property that let this feature be added without touching `make
    record`.
    """
    recorded = sorted(p for p in FIXTURES.rglob("*.json") if "request" not in p.name)

    assert recorded, "no recorded envelopes to check against"
    for path in recorded:
        _rate_limits(path.read_text())  # must not raise on any of them


def test_an_older_cli_reports_no_measurable_window() -> None:
    """CLI 2.1.220 emits `rate_limit_info` with a reset time and a status but no
    `utilization` and no `unifiedWindows`; every one of the 67 recorded
    transcripts carrying an event is that older shape, and production ran it
    until the pin moved to 2.1.251 (ADR 0180).

    So a reading arrives and yields no windows, and `available` stays false
    rather than the meter drawing an unmeasured bar at zero. A window needs a
    level for a meter to mean anything, and inventing one is worse than saying
    nothing. Kept as a test because the fixture corpus is still that shape, and
    because a downgrade must degrade rather than crash.
    """
    older = {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed",
            "resetsAt": 1786932600,
            "rateLimitType": "five_hour",
            "overageStatus": "allowed",
            "isUsingOverage": False,
        },
    }
    limits = _rate_limits(json.dumps(older))

    assert limits is not None
    assert limits.windows == ()
    assert limits.status == "allowed"


def test_a_single_object_envelope_yields_no_reading() -> None:
    """A `--output-format json` envelope has no transcript to carry an event."""
    single = [
        p
        for p in (FIXTURES / "analyze").glob("*.json")
        if "request" not in p.name and "\n" not in p.read_text().strip()
    ]

    assert single, "no single-object envelopes left to check"
    for path in single[:5]:
        assert _rate_limits(path.read_text()) is None
        assert parse_envelope(path.read_text()).rate_limits is None


def test_no_reading_from_empty_or_eventless_input() -> None:
    assert _rate_limits("") is None
    assert _rate_limits("   ") is None
    assert _rate_limits('{"type":"result","is_error":false}') is None


def test_an_error_envelope_still_carries_its_reading() -> None:
    """A rate-limited call is exactly when the reading is worth having. It is
    attached in `parse_envelope` rather than at each result construction so no
    branch can quietly omit it."""
    envelope = json.loads((ERRORS / "rate_limited.json").read_text())
    event = json.loads(
        next(line for line in STREAM.read_text().splitlines() if "rate_limit_event" in line)
    )
    transcript = json.dumps(event) + "\n" + json.dumps(envelope) + "\n"

    result = parse_envelope(transcript)

    assert result.error is not None
    assert result.rate_limits is not None
    assert result.rate_limits.status == "allowed_warning"


def test_malformed_event_does_not_crash_the_call() -> None:
    """A shape change in a CLI upgrade must cost the reading, never the run."""
    for line in (
        '{"type":"rate_limit_event"}',
        '{"type":"rate_limit_event","rate_limit_info":null}',
        '{"type":"rate_limit_event","rate_limit_info":{"unifiedWindows":"nonsense"}}',
        '{"type":"rate_limit_event","rate_limit_info":{"unifiedWindows":{"x":{}}}}',
    ):
        transcript = line + '\n{"type":"result","is_error":false}\n'
        limits = _rate_limits(transcript)
        assert limits is None or limits.windows == ()


def test_a_window_missing_half_its_reading_is_dropped() -> None:
    """A bar with no reset time cannot answer "how long until this clears",
    which is the question a full window actually raises."""
    event = {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed",
            "unifiedWindows": {
                "five_hour": {"utilization": 0.2, "resetsAt": 1788135000},
                "seven_day": {"utilization": 0.9},
            },
        },
    }
    limits = _rate_limits(json.dumps(event))

    assert [w.name for w in limits.windows] == ["five_hour"]


def test_replay_never_reports_a_reading(tmp_path: Path) -> None:
    """Load-bearing, not theoretical: 67 recorded transcripts already carry a
    `rate_limit_event`, every one of them months old. Replaying a fixture must
    not assert a window utilization as though it were now, the same way replay
    reports no CLI health.
    """
    from venona.llm.executor import ReplayExecutor, cache_key_for
    from venona.settings import get_settings

    task, schema = "analyze", {"type": "object"}
    key = cache_key_for(
        task=task,
        system_prompt="",
        user_content="x",
        schema=schema,
        model="sonnet",
        effort="medium",
        tools=(),
    )
    (tmp_path / "llm" / task).mkdir(parents=True)
    (tmp_path / "llm" / task / f"{key}.json").write_text(STREAM.read_text())

    settings = get_settings().model_copy(update={"fixtures_dir": tmp_path})
    result = asyncio.run(ReplayExecutor(settings).call(task, user_content="x", schema=schema))

    assert result.ok
    assert result.rate_limits is None


def test_the_newest_reading_wins() -> None:
    """Several events in one transcript: the last supersedes the rest, the same
    way `_result_line` takes the last result."""

    def event(util: float) -> str:
        return json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "allowed",
                    "unifiedWindows": {"seven_day": {"utilization": util, "resetsAt": 1788123600}},
                },
            }
        )

    limits = _rate_limits(event(0.1) + "\n" + event(0.6) + "\n")

    assert limits.windows[0].utilization == 0.6
