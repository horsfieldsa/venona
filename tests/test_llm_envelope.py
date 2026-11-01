"""Envelope parsing and the error paths.

The error paths are the code most likely to be wrong and least likely to be
exercised by hand, so they get the most attention here. The
`permission_denied` case in particular is the one that fails *silently* in
production — exit 0, `is_error: false`, and a plausible empty answer.

Every assertion below is checked against real recorded envelopes plus four
hand-crafted ones under `fixtures/llm/_errors/`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from venona.llm import budget as budget_mod
from venona.llm.executor import (
    LlmError,
    PermissionDenied,
    ReplayExecutor,
    cache_key_for,
    parse_envelope,
)
from venona.llm.harness import resolve
from venona.llm.harness.claude_code import _result_line
from venona.llm.schemas import analyze_schema, compute_overall, load_rubric, tier_for

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "llm"
ERRORS = FIXTURES / "_errors"
RECORDED = FIXTURES / "analyze"


def envelope(name: str) -> str:
    return (ERRORS / name).read_text()


def recorded_envelopes(root: Path) -> list[Path]:
    """Every recorded ENVELOPE under `root`, and nothing else.

    `*.request.json` files sit beside the envelopes and are the call arguments
    -- effort, model, schema keys, system prompt hash. They are not envelopes
    and `parse_envelope` reports `no structured_output` for all of them, so a
    glob that swept them in doubled the apparent denominator with files that
    passed without exercising anything: 43 of the 86 in `analyze/` alone, and
    one more with every recording.
    """
    return sorted(p for p in root.glob("*.json") if "request" not in p.name)


# ── the silent failure ───────────────────────────────────────────────


@pytest.mark.negative
def test_permission_denial_raises_rather_than_returning_empty() -> None:
    """The worst failure mode in the whole system.

    `--permission-mode dontAsk` denies WebSearch/WebFetch while exiting 0 with
    `is_error: false` and a well-formed answer. Treating that as a result means
    discovery and acronym Tier 3 return "found nothing" forever, invisibly.
    """
    with pytest.raises(PermissionDenied) as exc:
        parse_envelope(envelope("permission_denied.json"))
    assert "WebSearch" in str(exc.value)
    assert "allowedTools" in str(exc.value)


# ── the other error paths ────────────────────────────────────────────


@pytest.mark.negative
def test_refusal_is_flagged_not_raised() -> None:
    """A refusal must not abort a run -- record it, skip the document, continue."""
    result = parse_envelope(envelope("refusal.json"))
    assert result.refused is True
    assert result.ok is False
    assert result.data is None
    # Usage is still ledgered: a refusal costs money.
    assert result.cost_usd > 0


@pytest.mark.negative
def test_rate_limit_is_classified_retryable() -> None:
    result = parse_envelope(envelope("rate_limited.json"))
    assert result.ok is False
    assert result.error is not None
    assert budget_mod.is_retryable(result.error)


@pytest.mark.negative
def test_schema_violation_does_not_crash() -> None:
    result = parse_envelope(envelope("schema_violation.json"))
    assert result.ok is False
    assert result.data is None


@pytest.mark.negative
def test_unparseable_stdout_is_an_error_not_an_exception() -> None:
    result = parse_envelope("this is not json")
    assert result.ok is False
    assert "unparseable" in (result.error or "")


# ── cost and usage accounting ────────────────────────────────────────


def test_cost_sums_across_model_usage() -> None:
    """Every invocation carries a phantom haiku entry alongside the requested
    model. Reading cost off "the model I asked for" under-reports."""
    result = parse_envelope(envelope("refusal.json"))
    assert set(result.models) == {"claude-haiku-4-5-20251001", "claude-sonnet-5"}
    assert result.cost_usd == pytest.approx(sum(result.models.values()), rel=1e-6)


def _result_object(text: str) -> dict:
    """The `result` object, from either stdout shape the CLI has produced.

    PRODUCTION'S OWN FUNCTION, imported rather than reimplemented.

    TWO SHAPES EXIST IN `fixtures/` and both are real recordings. Fixtures
    captured earlier are a SINGLE `result` object. Fixtures captured on
    2026-08-16 are the full JSONL stream -- thirteen lines of `system`,
    `rate_limit_event`, `assistant`, `user` and finally `result`. The CLI in use
    had drifted from its pin by then (2.1.233 against `llm.pinned_version`
    2.1.220, which `/api/health` reports as `version_matches_pin: false`).

    `parse_envelope` copes with both, which is why 65 calls parsed and the run
    succeeded. This helper existed because the TEST did not: it called
    `json.loads` on the whole file and died with "Extra data: line 2" the first
    time a streamed fixture landed beside the old ones. It was then written a
    second time, by hand, and the copies diverged in both directions -- the
    hand-written one had a bare `json.loads` per line where production skips
    unparseable ones, so a CLI banner or a truncated final line raised out of
    the helper instead of failing an assertion; and it tried whole-text parsing
    for multi-line input where production refuses to, so a pretty-printed
    object passed the test and `parse_envelope` called it unparseable. Either
    way `make test` goes red with a traceback pointing at the test, on a fixture
    production reads fine.

    Tolerating both shapes is deliberate rather than a stopgap. The 84
    pre-existing analyze fixtures are dead-keyed against ontology v3 and will
    only be replaced when a run does enough analysis to re-record them, so both
    will sit in these directories for a while.
    """
    return _result_line(text)


@pytest.mark.skipif(not RECORDED.exists(), reason="no recorded fixtures yet")
def test_recorded_envelopes_parse_and_are_full_envelopes() -> None:
    """Fixtures must be the FULL stdout, not just structured_output -- otherwise
    the cost ledger, cache assertions and refusal path are all untestable."""
    files = recorded_envelopes(RECORDED)
    assert files, "expected at least one recorded analyze fixture"

    for path in files:
        raw = _result_object(path.read_text())
        for key in ("usage", "modelUsage", "is_error", "permission_denials"):
            assert key in raw, f"{path.name} is missing {key}"

        result = parse_envelope(path.read_text())
        assert result.ok, f"{path.name} did not parse as a success"
        # Real successes carry stop_reason "tool_use"; success must never be
        # gated on end_turn.
        assert raw["stop_reason"] == "tool_use"


# ── cache key stability ──────────────────────────────────────────────


def test_cache_key_is_stable_and_order_independent() -> None:
    """Frozen before the first fixture was recorded -- changing the recipe
    rekeys every file under fixtures/llm/."""
    kwargs = dict(
        task="analyze",
        system_prompt="sys",
        user_content="doc",
        schema={"type": "object"},
        model="sonnet",
        effort="medium",
    )
    a = cache_key_for(**kwargs, tools=("WebSearch", "WebFetch"))
    b = cache_key_for(**kwargs, tools=("WebFetch", "WebSearch"))
    assert a == b, "tool order must not change the key"
    assert len(a) == 32

    c = cache_key_for(**{**kwargs, "user_content": "different"}, tools=())
    assert c != a


# ── replay ───────────────────────────────────────────────────────────


@pytest.mark.negative
async def test_missing_fixture_fails_loudly(tmp_path) -> None:
    """A missing fixture must never fall through to a live call -- that is how
    an "offline" suite quietly starts spending money and stops being
    deterministic."""
    from venona.settings import get_settings

    settings = get_settings()
    executor = ReplayExecutor(settings)
    executor._root = tmp_path  # empty fixture dir

    with pytest.raises(LlmError) as exc:
        await executor.call("analyze", user_content="x", schema={}, system_prompt="y")

    message = str(exc.value)
    assert "no fixture" in message
    assert "make record" in message, "the error must say how to fix it"


# ── rubric-driven scoring ────────────────────────────────────────────


def test_rubric_weights_sum_to_one() -> None:
    rubric = load_rubric()
    assert sum(float(a["weight"]) for a in rubric["axes"]) == pytest.approx(1.0, abs=1e-6)


def test_overall_is_computed_in_python_not_by_the_model() -> None:
    """Deterministic, and reweightable with zero model calls."""
    ids = [a["id"] for a in load_rubric()["axes"]]
    assert compute_overall({i: 5 for i in ids}) == 100
    assert compute_overall({i: 0 for i in ids}) == 0
    assert 0 < compute_overall({ids[0]: 5}) < 100


def test_tier_boundaries() -> None:
    """Boundaries are read from the rubric, not hardcoded.

    Tier cuts are USER-EDITABLE and free to change (`POST /api/rescore`), so a
    test asserting 70/45 fails the moment the user retunes them — which is
    exactly the thing the design is meant to make cheap. Assert the boundary
    BEHAVIOUR against whatever is configured.
    """
    import yaml
    from venona.settings import get_settings

    cuts = yaml.safe_load((get_settings().profile_dir / "scoring_rubric.yaml").read_text())["tiers"]
    must, worth = cuts["must_read"], cuts["worth_knowing"]

    assert tier_for(100) == "must_read"
    assert tier_for(must) == "must_read"
    assert tier_for(must - 1) == "worth_knowing"
    assert tier_for(worth) == "worth_knowing"
    assert tier_for(worth - 1) == "background"
    assert tier_for(0) == "background"


def test_analyze_schema_is_generated_from_the_ontology() -> None:
    from venona.ontology import get_ontology

    ontology = get_ontology()
    schema = analyze_schema()
    relations = schema["properties"]["relations"]["items"]

    assert relations["properties"]["predicate"]["enum"] == list(ontology.predicate_names)
    assert schema["properties"]["entities"]["items"]["properties"]["type"]["enum"] == list(
        ontology.type_names
    )
    # Evidence is required, so an unevidenced relation is unrepresentable.
    assert "evidence" in relations["required"]
    # The model returns surfaces; ids are assigned by resolve.py.
    assert "surface" in schema["properties"]["entities"]["items"]["properties"]
    assert "id" not in schema["properties"]["entities"]["items"]["properties"]


def test_retry_classification() -> None:
    assert budget_mod.is_retryable("429 rate_limit_error")
    assert budget_mod.is_retryable("Overloaded")
    assert budget_mod.is_retryable("claude timed out after 300s")
    assert not budget_mod.is_retryable("invalid schema")
    assert not budget_mod.is_retryable(None)


# ── transport: the prompt goes on stdin, not in argv ─────────────────


class _NoFiles:
    """A file cache that declines, so this test is about argv and nothing else."""

    async def materialize(self, content: str, *, kind: str, suffix: str):
        return None


def test_user_content_is_not_in_argv() -> None:
    """A single argv element is capped at MAX_ARG_STRLEN (131072 on Linux).

    The brief prompt carries every retrieved article, so it crosses that on a
    day with long articles — and the failure was an `OSError` from the exec
    itself, before `claude` started: unretryable, unbudgeted, and reported as a
    failed run rather than `succeeded_partial`.

    Capping the content would have "fixed" it by silently truncating the
    evidence the brief exists to carry.
    """
    import asyncio

    from venona.llm.harness import CallRequest
    from venona.llm.harness.claude_code import CLAUDE_CODE

    needle = "SENTINEL_" + "x" * 200
    argv = asyncio.run(
        CLAUDE_CODE.argv(
            CallRequest(
                task="analyze",
                system_prompt="",
                schema={"type": "object"},
                model="sonnet",
                effort="low",
                tools=(),
                user_content=needle,
            ),
            _NoFiles(),
        )
    )

    assert not any(needle in part for part in argv), "user_content must not reach argv"
    assert "-p" in argv, "still prompt mode; the content just arrives on stdin"


def test_the_argv_ceiling_is_real_and_we_clear_it() -> None:
    """Negative control for the above. If this stops raising, the platform
    changed and the comment in `_build_argv` needs revisiting — not deleting."""
    import subprocess

    with pytest.raises(OSError, match="Argument list too long"):
        subprocess.run(["/bin/true", "x" * 200_000], check=False)

    # 200k of prompt is fine on stdin; that is the whole point.
    subprocess.run(["/bin/cat"], input=b"x" * 200_000, capture_output=True, check=True)


@pytest.mark.negative
def test_an_exec_failure_becomes_an_llm_error() -> None:
    """`OSError` at exec time happens before the process exists, so nothing
    downstream classifies it. It must arrive as `LlmError` so `is_retryable`
    and the budget can see it."""
    import asyncio

    from venona.llm.executor import CliExecutor, LlmError
    from venona.settings import get_settings

    executor = CliExecutor(get_settings(), harness=resolve("claude_code"))
    with pytest.raises(LlmError, match="could not exec"):
        asyncio.run(executor._run(["/nonexistent-venona-binary"], timeout=5))


# ── stream-json: the envelope survives the switch ────────────────────


def test_a_streamed_run_parses_from_its_result_line() -> None:
    """`--output-format stream-json` emits JSONL. The LAST `type: result` line
    is byte-identical to what `--output-format json` returned as a single
    object, so the envelope contract (C2) is untouched by the switch."""
    single = envelope("refusal.json")
    env = json.loads(single)

    jsonl = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
            json.dumps({"type": "assistant", "message": {"content": [{"text": "x"}]}}),
            json.dumps(env),
        ]
    )

    assert parse_envelope(jsonl).refused == parse_envelope(single).refused


def test_every_recorded_fixture_still_parses_after_the_switch() -> None:
    """The reason `parse_envelope` accepts BOTH shapes.

    `cache_key_for` is a function of the call arguments alone, so switching
    output format does not rekey anything -- but every envelope under
    `fixtures/llm/` was recorded as a single JSON object. If the new parser only
    understood JSONL, `make test` would have gone offline-red with no fixture
    having changed.

    EVERY TASK DIRECTORY, not just `analyze/`. Pointed at `analyze/` alone this
    covered exactly one streamed fixture while 63 others sat unchecked next
    door: `acronym_read` is 29 of 29 JSONL, `acronym_search` 13 of 13,
    `define_write` 13 of 13, `discovery` 8 of 8. A hand-trimmed or truncated
    `discovery` envelope passed the whole suite and would surface later as
    under-reported sweep spend, or as an unexercisable permission-denial path
    for the two tasks that actually call WebSearch -- which is the silent
    failure this module exists for. `_errors/` is excluded because its fixtures
    are hand-crafted to be broken.

    ONE SERVICE'S FIXTURES, checked by that service's parser. `parse_envelope`
    is Claude Code's, and the directories another service records into carry a
    stream with none of the same event names in it -- so sweeping them here
    would fail on envelopes that are perfectly correct. They are excluded by
    the harness table rather than by a hardcoded name, and each service's own
    fixtures are swept by its own tests.
    """
    from venona.llm.harness import known, resolve

    foreign = {resolve(k).fixture_root for k in known() if resolve(k).fixture_root}
    directories = sorted(
        p
        for p in FIXTURES.iterdir()
        if p.is_dir() and p.name != "_errors" and p.name not in foreign
    )
    assert directories, "no recorded fixture directories to check"

    checked = 0
    for directory in directories:
        for path in recorded_envelopes(directory):
            result = parse_envelope(path.read_text())
            # A recorded refusal is still a correctly PARSED envelope; what must
            # never appear is the parser failing to find the envelope at all.
            assert "unparseable" not in (result.error or ""), (
                f"{directory.name}/{path.name}: {result.error}"
            )
            checked += 1
    assert checked, "found directories but no envelopes -- the filter is too strict"


@pytest.mark.negative
def test_a_stream_with_no_result_line_is_an_error_not_a_crash() -> None:
    """A killed CLI can leave a truncated stream. That must degrade, not raise."""
    jsonl = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant", "message": {"content": []}}),
        ]
    )
    result = parse_envelope(jsonl)
    assert result.data is None
    assert result.error is not None


# ── server-tool counts live in modelUsage, not server_tool_use ───────


def test_web_search_count_comes_from_model_usage() -> None:
    """Measured against 2.1.220, on a call that demonstrably searched.

    `usage.server_tool_use` is present and always zero; the count is attributed
    to the haiku sub-model that actually performs the search. Reading the
    top-level block made `llm_calls.web_search_requests` report 0 on every row
    ever written — including the discovery sweeps that are ~40% of a run's spend
    and consist almost entirely of searching.

    Contract C2 already records this shape for COST. It is true of tool counts
    too, and that was missed.
    """
    from venona.llm.executor import Usage

    usage = Usage.from_envelope(
        {
            "input_tokens": 3,
            "output_tokens": 267,
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        },
        {
            "claude-haiku-4-5-20251001": {"webSearchRequests": 1, "webFetchRequests": 2},
            "claude-sonnet-5": {"webSearchRequests": 0, "webFetchRequests": 0},
        },
    )
    assert usage.web_search_requests == 1
    assert usage.web_fetch_requests == 2


def test_counts_are_summed_across_every_model() -> None:
    """More than one sub-model may do tool work in a single call."""
    from venona.llm.executor import Usage

    usage = Usage.from_envelope(
        {},
        {"a": {"webSearchRequests": 2}, "b": {"webSearchRequests": 3}},
    )
    assert usage.web_search_requests == 5


def test_a_future_cli_that_fills_the_top_level_does_not_double_count() -> None:
    """`max`, not a sum: if `server_tool_use` ever starts reporting the same
    work, the number must not silently double."""
    from venona.llm.executor import Usage

    usage = Usage.from_envelope(
        {"server_tool_use": {"web_search_requests": 4}},
        {"haiku": {"webSearchRequests": 4}},
    )
    assert usage.web_search_requests == 4


def test_a_call_with_no_tools_still_reports_zero() -> None:
    from venona.llm.executor import Usage

    assert Usage.from_envelope({}, {}).web_search_requests == 0


# ── a diagnosis with nothing in `result` ─────────────────────────────


@pytest.mark.negative
def test_an_error_envelope_with_an_empty_result_still_names_its_reason() -> None:
    """Three production ledger rows read literally `is_error`: the CLI gave up
    after the model kept sending unparseable tool input, put the reason in
    `subtype` and `errors`, and left `result` empty. A row that says only
    `is_error` cannot be told from a provider outage."""
    raw = json.dumps(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "",
            "errors": ["StructuredOutput was called with input that could not be parsed"],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "modelUsage": {},
        }
    )
    result = parse_envelope(raw)
    assert result.ok is False
    assert result.error is not None
    assert "error_during_execution" in result.error
    assert "could not be parsed" in result.error
    # Not a provider condition: the retry loop must not spend two more calls on it.
    assert not budget_mod.is_retryable(result.error)


@pytest.mark.negative
def test_an_error_envelope_with_only_a_subtype_reports_the_subtype() -> None:
    raw = json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True})
    assert parse_envelope(raw).error == "error_max_turns"


@pytest.mark.negative
def test_an_api_status_still_leads_when_result_is_empty() -> None:
    raw = json.dumps(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "api_error_status": 529,
            "errors": ["overloaded"],
        }
    )
    error = parse_envelope(raw).error or ""
    assert error.startswith("529")
    assert "overloaded" in error
    assert budget_mod.is_retryable(error)
