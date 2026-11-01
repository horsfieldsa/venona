"""One end-to-end pass through `execute_run` with everything external stubbed.

This exists because the orchestrator is the one piece nothing else covers. Every
stage has its own tests; what those cannot catch is the WIRING between them — a
stage that stops being called, a report whose counters stop being recorded, a
`build_*` call that loses an argument. All three have happened.

It replaces the `test_seed_parity` gate from the original plan, which turned out
to be unaffordable: the LLM cache key hashes the system prompt, which embeds the
entity gazetteer, which every run mutates. The 42 recorded analyze fixtures
already carry two different `system_prompt_sha` values. A missing fixture
surfaces as a *failed run*, so the gate would have gone red for reasons
unrelated to any code change and cost live spend to re-record.

This costs nothing, is deterministic forever, and needs no fixtures.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from venona.db import core as db
from venona.llm import budget as budget_mod
from venona.llm.executor import LlmError, LlmResult, Usage
from venona.pipeline import orchestrator
from venona.settings import get_settings
from venona.sources.http import Response

FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Army picks vendor for NGC2 data layer</title>
    <link>https://example.test/ngc2-award</link>
    <pubDate>Mon, 04 Aug 2026 12:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

ARTICLE = (
    "<html><body><article><p>"
    + (
        "The Army selected a vendor to build the Next Generation Command and "
        "Control (NGC2) common data layer, a decision that pushes inference "
        "toward the tactical edge rather than a central enclave. "
    )
    * 12
    + "</p></article></body></html>"
)

ANALYZE = {
    "relevance": {
        "overall": None,
        "note": "Direct hit on the Army C2 thesis.",
        "so_what": "Confirms an edge-first architecture.",
        "profile_hooks": ["ngc2", "edge_inference"],
        "axes": {
            "product_overlap": {"score": 5, "why": "direct hit"},
            "competitive_threat": {"score": 5, "why": "direct hit"},
            "customer_program": {"score": 5, "why": "direct hit"},
            "technology_signal": {"score": 5, "why": "direct hit"},
            "budget_procurement": {"score": 5, "why": "direct hit"},
            "partnership_channel": {"score": 5, "why": "direct hit"},
            "talent_people": {"score": 5, "why": "direct hit"},
        },
    },
    # Case and field names are load-bearing and must match `analyze_schema()`.
    # These were `"program"`, `"manages"` and `evidence_quote`, none of which
    # the real schema emits: every entity failed to resolve and the relation was
    # dropped for having no `evidence`, so this file asserted a finished brief
    # while `_ingest` wrote nothing at all -- the exact "stage that stops being
    # called" blind spot the module docstring claims to cover.
    "entities": [
        {
            "type": "Program",
            "surface": "Next Generation Command and Control",
            "confidence": 0.95,
            "context_sentence": "The Army selected a vendor to build the Next Generation "
            "Command and Control (NGC2) common data layer",
        },
        {
            # A MilitaryUnit, not a Company. Under one `Organization` type this
            # stub was well-typed either way; MANAGES is now
            # GovernmentBody|MilitaryUnit, so getting it wrong drops the
            # relation and this test notices.
            "type": "MilitaryUnit",
            "surface": "Army",
            "confidence": 0.95,
            "context_sentence": "The Army selected a vendor to build the Next Generation "
            "Command and Control (NGC2) common data layer",
        },
    ],
    "relations": [
        {
            "subject_surface": "Army",
            "subject_type": "MilitaryUnit",
            "predicate": "MANAGES",
            "object_surface": "Next Generation Command and Control",
            "object_type": "Program",
            "confidence": 0.9,
            "evidence": "The Army selected a vendor to build the Next Generation "
            "Command and Control (NGC2) common data layer",
        }
    ],
    "acronyms": [],
}

BRIEF = {
    "headline": "Army commits to an edge-first NGC2 data layer",
    # `cluster_id` is load-bearing: `_persist` drops any item whose id is not in
    # the retrieval set, so a stub with a made-up id produces ZERO items while
    # every brief-row assertion still passes. That is exactly how the previous
    # stub hid a broken persist for a whole slice.
    "items": [
        {
            "cluster_id": "REPLACED_AT_CALL_TIME",
            "section": "COMMAND & CONTROL",
            "headline": "Army picks a vendor for the NGC2 common data layer",
            "state": "new",
            "state_note": "",
            "summary": (
                "The Army selected a vendor to build the Next Generation Command and "
                "Control common data layer."
            ),
            "why_it_matters": (
                "The decision pushes inference toward the tactical edge rather than a "
                "central enclave."
            ),
            "top_line": "The Army just picked who owns the NGC2 data layer.",
        }
    ],
    "mentioned_entity_ids": [],
    "unresolved_mentions": [],
}


class StubExecutor:
    """Canned answers by task name. No subprocess, no `claude` binary, no spend."""

    def __init__(self) -> None:
        self.tasks: list[str] = []
        #: Tasks whose FIRST call fails transiently, so a retry and its wait
        #: actually happen inside a stage. Empty unless a test asks.
        self.stumble: set[str] = set()

    async def call(self, task: str, **kwargs: Any) -> LlmResult:
        if task in self.stumble:
            self.stumble.discard(task)
            self.tasks.append(task)
            raise LlmError("503 upstream unavailable")
        self.tasks.append(task)
        data = {"analyze": ANALYZE, "brief": BRIEF}.get(task)
        if task == "brief" and data is not None:
            # Read the cluster id out of the prompt, exactly as the model must.
            # Hardcoding one would make the test pass while `_persist` silently
            # dropped every item for having an unknown id.
            import copy
            import re

            match = re.search(r"cluster_id: (\S+)", kwargs.get("user_content", ""))
            data = copy.deepcopy(data)
            if match:
                data["items"][0]["cluster_id"] = match.group(1)
        if data is None:
            # Discovery and acronym lookups are not exercised here; returning an
            # error is the shape a missing fixture takes, and the run must
            # survive it.
            return LlmResult(data=None, usage=Usage(), error=f"no stub for task {task!r}")
        return LlmResult(data=data, usage=Usage(), cost_usd=0.01)


class StubHttp:
    """The feed, then the article. Anything else 404s."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def get(self, url: str, **kwargs: Any) -> Response:
        self.urls.append(url)
        if url.endswith("/feed/"):
            return Response(url=url, status=200, text=FEED, headers={})
        if "ngc2-award" in url:
            return Response(url=url, status=200, text=ARTICLE, headers={})
        return Response(url=url, status=404, text="", headers={})

    async def aclose(self) -> None:
        return None


@pytest.fixture
def wired(monkeypatch, tmp_path: Path, make_database):
    """A run whose only external dependencies are the two stubs above.

    An UNSEEDED database: these tests count what the run itself produced, and
    the seed's four documents and its entities would be indistinguishable from
    the run's own output.
    """
    database_url = make_database(seeded=False)
    sources = tmp_path / "config"
    sources.mkdir()
    (sources / "sources.yaml").write_text(
        "sources:\n"
        "  - id: stub\n"
        "    name: Stub Feed\n"
        "    kind: rss\n"
        "    url: https://example.test/feed/\n"
        "    publisher: Stub\n"
    )

    # A sandboxed profile from the blank-instance skeleton, NOT the repo's --
    # the run now auto-loads `anchors.yaml` when the anchor set is empty, and
    # against the repo profile that would pull the whole defense trunk into a
    # fixture whose tests count what the run itself produced.
    profile = tmp_path / "profile"
    profile.mkdir()
    for name in (Path(__file__).resolve().parents[1] / "fixtures" / "skeleton").iterdir():
        if name.name not in ("ontology.yaml", "sources.yaml"):
            (profile / name.name).write_text(name.read_text())

    # `offline` is what makes this test reproducible, not just network-free.
    #
    # Without it `build_context` hands the run a `SystemClock`, so `build_retrieval`
    # measures its 36-hour lookback from WALL TIME while the stub feed carries a
    # hardcoded `Mon, 04 Aug 2026 12:00:00 GMT`. The test therefore passed for
    # about a day and a half after it was written and then began failing at a
    # UTC midnight, with the article 12 minutes outside the window and a failure
    # ("brief made no call") that points nowhere near the cause.
    #
    # `FrozenClock` starts at exactly that timestamp -- the two were always meant
    # to line up. The executor and HTTP client are already stubbed above, so this
    # only swaps the clock, ids and rng.
    settings = get_settings().model_copy(
        update={
            "data_dir": tmp_path,
            "config_dir": sources,
            "profile_dir": profile,
            "offline": True,
            "database_url_override": database_url,
        },
        deep=True,
    )
    # The executor and the HTTP client arrive ON THE CONTEXT now, because
    # `run_launcher` builds both and `execute_run` runs stages rather than
    # owning a run. `_run` below assembles the scope the launcher would hand it.
    llm, http = StubExecutor(), StubHttp()

    conn = db.connect(database_url)
    # A minimal trunk, so the analyze-stage guard for a wiped-and-never-loaded
    # database does not degrade these runs -- the guard exists for exactly the
    # state this fixture would otherwise be in. Deliberately inert: a name and
    # a surface that appear in no stub document, so the counts these tests
    # make ("what the run itself produced") are untouched.
    conn.execute(
        "INSERT INTO entities (id, type, name, canonical_key, status, relevance, "
        "mention_count, source_count, first_seen, last_seen, created_at) "
        "VALUES ('country:trunk-sentinel', 'Country', 'Trunk Sentinel', "
        "'trunk sentinel', 'active', 0, 0, 0, '2026-08-04T12:00:00.000Z', "
        "'2026-08-04T12:00:00.000Z', '2026-08-04T12:00:00.000Z')"
    )
    conn.execute(
        "INSERT INTO ambiguous_surfaces (normalized, reason, created_at) "
        "VALUES ('trunk sentinel surface', 'fixture trunk', '2026-08-04T12:00:00.000Z')"
    )
    conn.commit()
    yield settings, conn, llm, http
    conn.close()


def _run(settings, conn, llm, http, **kwargs):
    """One pipeline run, assembled the way `run_launcher` assembles one.

    The scope is what a run IS now: a context the launcher built and an emitter
    that writes the event log. `execute_run` runs stages inside it and reports
    what it reached; the terminal row is the launcher's and is not exercised
    here.
    """
    from venona.llm.budget import Budget
    from venona.llm.prompts import load_profile
    from venona.pipeline.types import build_context
    from venona.services import run_service
    from venona.services.run_scope import RunScope

    run_service.create_run(
        conn,
        run_id="run:smoke",
        kind="test",
        params={},
        profile_hash=None,
        now="2026-08-05T12:00:00.000Z",
    )

    async def emit(type_, stage, data):
        event = run_service.append_event(
            conn, run_id="run:smoke", type_=type_, stage=stage, data=data, now=ctx.clock.now()
        )
        run_service.registry.publish("run:smoke", event)

    ctx = build_context(
        run_id="run:smoke",
        db=conn,
        llm=llm,
        settings=settings,
        graph=None,
        http=http,
        profile=load_profile(settings),
        emit=emit,
    )
    ctx.budget = Budget(settings=settings.llm)
    scope = RunScope(run_id="run:smoke", conn=conn, ctx=ctx, emit=emit)
    return asyncio.run(
        orchestrator.execute_run(
            scope=scope, run_id="run:smoke", settings=settings, conn=conn, graph=None, **kwargs
        )
    )


# ── the whole chain ──────────────────────────────────────────────────


def test_a_full_run_reaches_a_brief(wired) -> None:
    settings, conn, llm, http = wired
    outcome = _run(settings, conn, llm, http)

    assert outcome.status in {"succeeded", "succeeded_partial"}, outcome.error
    assert outcome.error is None

    # The feed was read, and the article body fetched — not just the feed.
    assert any(u.endswith("/feed/") for u in http.urls)
    assert any("ngc2-award" in u for u in http.urls)

    # Every stage that spends a call actually ran, in order.
    assert llm.tasks[0] == "analyze"
    assert "brief" in llm.tasks

    assert outcome.brief_id, "a run that analyzed a document must produce a brief"

    # A brief row with no blocks renders as an empty page. Asserting only on
    # `brief_id` is what let a broken stub pass for a whole slice.
    blocks = conn.execute(
        "SELECT type, text FROM brief_blocks WHERE brief_id = ? ORDER BY section, ordinal",
        (outcome.brief_id,),
    ).fetchall()
    assert len(blocks) >= 2, f"brief has {len(blocks)} blocks; the model's prose was dropped"
    assert any("Next Generation Command and Control" in b["text"] for b in blocks)


def test_every_stage_records_itself(wired) -> None:
    """`run_stages` is what the UI timeline reads. A stage that runs but does
    not record is invisible."""
    settings, conn, llm, http = wired
    _run(settings, conn, llm, http)

    stages = {
        r["stage"]
        for r in conn.execute("SELECT stage FROM run_stages WHERE run_id = ?", ("run:smoke",))
    }
    assert {"fetch", "analyze", "brief"} <= stages


def test_the_ledger_sees_every_call(wired) -> None:
    """The regression `call_and_record` was written for: the brief call used to
    bypass `record_call` entirely, so its cost appeared nowhere."""
    settings, conn, llm, http = wired
    outcome = _run(settings, conn, llm, http)

    logged = {
        r["task"]
        for r in conn.execute("SELECT task FROM llm_calls WHERE run_id = ?", ("run:smoke",))
    }
    assert "analyze" in logged
    assert "brief" in logged
    assert outcome.counters.get("llm_calls", 0) >= 2
    assert outcome.cost_usd > 0


def test_events_are_durable(wired) -> None:
    """SSE replays from this log, so a client that reconnects mid-run must be
    able to reconstruct what it missed."""
    settings, conn, llm, http = wired
    _run(settings, conn, llm, http)

    rows = list(
        conn.execute(
            "SELECT type, stage FROM run_events WHERE run_id = ? ORDER BY seq", ("run:smoke",)
        )
    )
    assert rows, "a run that emitted nothing cannot be watched"
    assert {"stage_started", "stage_finished"} <= {r["type"] for r in rows}


def test_an_event_that_names_no_stage_gets_the_running_one(wired) -> None:
    """The gap that made the waiting signal inert.

    `RunContext` carries no stage (contract C7), so anything emitted from
    INSIDE a model call -- the retry saying it is waiting for the provider, the
    circuit saying it is sitting out an outage -- has no stage to name. The
    client drops a stage-less `stage_progress` (`if (event.stage)`), so those
    events were written durably, broadcast, and silently discarded: the amber
    segment and the countdown could never render, on any run.

    Everything emitted between `stage_started` and `stage_finished` belongs to
    that stage, and the orchestrator is the only thing that knows which it is.
    """
    settings, conn, llm, http = wired
    _run(settings, conn, llm, http)

    rows = list(
        conn.execute(
            "SELECT type, stage FROM run_events WHERE run_id = ? ORDER BY seq", ("run:smoke",)
        )
    )
    # The three the client keys on a stage. `counter`, `model_message` and
    # `done` are handled above that guard and are legitimately stage-less.
    gated = {"stage_started", "stage_progress", "stage_finished"}
    stageless = [r["type"] for r in rows if r["stage"] is None and r["type"] in gated]
    assert not stageless, (
        f"{sorted(set(stageless))} were written with no stage, so every client "
        f"that keys on the stage drops them"
    )


def test_a_wait_inside_a_stage_is_attributed_to_that_stage(wired, monkeypatch) -> None:
    """The same gap, reproduced rather than asserted.

    A transient failure on the analyze call makes it back off for real, and the
    wait it emits names no stage because a call does not know which one asked
    for it. If the orchestrator does not fill that in, the row lands with a NULL
    stage and the client throws it away.

    ANALYZE specifically, because it is the stage the signal exists for: it runs
    for over an hour, and it reaches the model through `analyze_document`, which
    has no context to emit from and so had no way to say it was waiting.
    """
    settings, conn, llm, http = wired
    monkeypatch.setattr("venona.llm.budget.next_delay", lambda attempt, **kw: 0.0)
    llm.stumble.add("analyze")
    _run(settings, conn, llm, http)

    waits = list(
        conn.execute(
            "SELECT stage, data FROM run_events "
            "WHERE run_id = ? AND type = 'stage_progress' ORDER BY seq",
            ("run:smoke",),
        )
    )

    def payload(row):
        raw = row["data"]
        return raw if isinstance(raw, dict) else json.loads(raw or "{}")

    waiting = [r for r in waits if payload(r).get("waiting")]
    assert waiting, "the retry took a wait and reported none of it"
    assert all(r["stage"] == "analyze" for r in waiting), (
        f"a wait landed on {[r['stage'] for r in waiting]}; a NULL stage is "
        f"dropped by every client that keys on one"
    )


# ── `--only` ─────────────────────────────────────────────────────────


def test_only_fetch_stops_before_analyze(wired) -> None:
    settings, conn, llm, http = wired
    outcome = _run(settings, conn, llm, http, only=["fetch"])

    assert outcome.status in {"succeeded", "succeeded_partial"}
    assert llm.tasks == [], "no stage that spends a call should have run"
    assert outcome.brief_id is None
    assert conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"] == 1


def test_only_brief_reuses_what_is_already_there(wired) -> None:
    """The `--from RUN_ID` replacement. Stages key on STATE, so a second run
    restricted to `brief` re-briefs without re-fetching."""
    settings, conn, llm, http = wired
    _run(settings, conn, llm, http)

    fetches = len(http.urls)
    conn.execute("UPDATE runs SET status = 'succeeded' WHERE id = 'run:smoke'")
    conn.execute("DELETE FROM run_events WHERE run_id = 'run:smoke'")
    conn.execute("DELETE FROM runs WHERE id = 'run:smoke'")

    llm.tasks.clear()
    _run(settings, conn, llm, http, only=["brief"])

    assert len(http.urls) == fetches, "--only brief must not re-fetch"
    assert "analyze" not in llm.tasks


def test_a_window_with_nothing_new_is_not_a_degradation(wired) -> None:
    """A quiet night is a correct night's work, and must say so.

    `docs/RUNBOOK.md` has documented this as `succeeded` + `nothing_to_do`
    since ADR 0183. The code said otherwise: `_empty_retrieval_reason` wrote
    into `BriefReport.error`, the orchestrator warned on every value of that
    field, and so every quiet night reported `degraded` -- our own chip
    contradicting our own runbook. Epic U fixed the neighbouring case (a THIN
    day, where a brief is still written) and left this one.
    """
    settings, conn, llm, http = wired
    _run(settings, conn, llm, http)

    conn.execute("UPDATE runs SET status = 'succeeded' WHERE id = 'run:smoke'")
    conn.execute("DELETE FROM run_events WHERE run_id = 'run:smoke'")
    conn.execute("DELETE FROM runs WHERE id = 'run:smoke'")
    llm.tasks.clear()

    outcome = _run(settings, conn, llm, http, only=["brief"])

    assert outcome.status == "succeeded", outcome.warnings
    assert outcome.outcome == "nothing_to_do"
    # Still SAID, just not as a degradation: the reason is why the page is
    # empty, and hiding it would trade one wrong answer for a missing one.
    quiet = [m for m in outcome.warnings if "nothing new" in m.text]
    assert quiet, f"the reason must still reach the reader, got {outcome.warnings}"
    assert all(m.level == "note" for m in quiet), quiet


def test_a_brief_that_was_owed_and_failed_claims_no_answer(wired) -> None:
    """A model that could not write the brief is not a quiet night.

    Both reached the same `nothing_to_do` while one field carried both facts,
    which told `scheduler._quiet_streak` that a failure was a quiet night and
    told `outage_retry_due` not to bother retrying it. ADR 0183 spells "no
    answer at all" as a null outcome.
    """
    settings, conn, llm, http = wired

    from venona.pipeline import brief as brief_mod

    real = brief_mod.run_brief

    async def broken(ctx, **kwargs: Any):
        report = await real(ctx, **kwargs)
        report.brief_id = None
        report.empty_reason = None
        report.error = "the model returned nothing usable"
        return report

    orig = orchestrator.__dict__.get("run_brief")
    brief_mod.run_brief = broken
    orchestrator.run_brief = broken
    try:
        outcome = _run(settings, conn, llm, http)
    finally:
        brief_mod.run_brief = real
        if orig is not None:
            orchestrator.run_brief = orig

    assert outcome.status == "succeeded_partial"
    assert outcome.outcome is None, "a brief that was owed and failed found nothing to report"
    assert any(m.level == "warning" for m in outcome.warnings), outcome.warnings


# ── failure isolation ────────────────────────────────────────────────


async def _no_sleep(attempt: int, **kwargs: Any) -> float:
    """Retry tests assert the policy, not the wall clock."""
    return 0.0


@pytest.mark.negative
def test_a_partially_useful_run_is_not_a_failure(wired, monkeypatch) -> None:
    """`succeeded_partial` exists so one dead feed does not read as an outage."""
    settings, conn, llm, http = wired

    async def broken(url: str, **kwargs: Any) -> Response:
        http.urls.append(url)
        return Response(url=url, status=503, text="", headers={})

    monkeypatch.setattr(http, "get", broken)
    outcome = _run(settings, conn, llm, http)

    assert outcome.status != "failed"
    assert outcome.warnings, "a failed source must leave a trace"


def test_a_run_always_reaches_a_terminal_status(wired, monkeypatch) -> None:
    """Whatever happens, the run REPORTS a terminal status.

    The row is `run_launcher`'s to write -- one writer, whatever the kind -- so
    what this holds is the half `execute_run` still owns: a stage that threw is
    reported as `failed` rather than propagating and leaving the launcher to
    guess.
    """
    settings, conn, llm, http = wired

    def explode(*args: Any, **kwargs: Any):
        raise RuntimeError("dedup blew up")

    monkeypatch.setattr(orchestrator, "run_fetch", explode)
    outcome = _run(settings, conn, llm, http)

    assert outcome.status == "failed"
    assert "dedup blew up" in (outcome.error or "")


@pytest.mark.negative
def test_a_failing_brief_does_not_take_curate_down_with_it(wired, monkeypatch) -> None:
    """The cascade behind run:06a74b50. An `LlmError` out of the brief call
    unwound past `curate` and the Neo4j projection, so one unreadable envelope
    also cost the run its promotions and left every resolved entity
    unprojected. A missing brief is a missing brief, nothing more."""
    settings, conn, llm, http = wired

    original = llm.call

    async def fail_brief(task: str, **kwargs: Any) -> LlmResult:
        if task == "brief":
            llm.tasks.append(task)
            raise LlmError("claude emitted a line over the stream limit")
        return await original(task, **kwargs)

    monkeypatch.setattr(llm, "call", fail_brief)
    outcome = _run(settings, conn, llm, http)

    assert outcome.brief_id is None
    # The run analysed documents, so it is partial -- not failed.
    assert outcome.status == "succeeded_partial", outcome.error
    assert any("brief" in w.text for w in outcome.warnings), outcome.warnings

    stages = {
        r["stage"]
        for r in conn.execute("SELECT stage FROM run_stages WHERE run_id = ?", ("run:smoke",))
    }
    assert "curate" in stages, "curate must still run after a failed brief"
    assert "analyze" in stages


@pytest.mark.negative
def test_a_raising_call_still_lands_in_the_ledger(wired, monkeypatch) -> None:
    """`llm/usage.py` promises every call is recorded "successful or not". A
    call that raised left no row at all, so a brief that burned three minutes
    of Opus and then threw was invisible in both the ledger and the run cost."""
    settings, conn, llm, http = wired

    original = llm.call

    async def fail_brief(task: str, **kwargs: Any) -> LlmResult:
        if task == "brief":
            raise LlmError("claude emitted a line over the stream limit")
        return await original(task, **kwargs)

    monkeypatch.setattr(llm, "call", fail_brief)
    _run(settings, conn, llm, http)

    row = conn.execute(
        "SELECT status, error FROM llm_calls WHERE run_id = ? AND task = 'brief'",
        ("run:smoke",),
    ).fetchone()
    assert row is not None, "a raising call left no trace in llm_calls"
    assert row["status"] == "error"
    assert "stream limit" in row["error"]


def test_a_retryable_failure_is_retried_before_giving_up(wired, monkeypatch) -> None:
    """The brief is the run's longest call. Losing minutes of Opus to a 429 a
    backoff would have cleared is the expensive version of this bug."""
    settings, conn, llm, http = wired
    monkeypatch.setattr(budget_mod, "backoff", _no_sleep)

    original = llm.call
    attempts = []

    async def flaky_brief(task: str, **kwargs: Any) -> LlmResult:
        if task == "brief":
            attempts.append(task)
            if len(attempts) == 1:
                raise LlmError("529 overloaded")
        return await original(task, **kwargs)

    monkeypatch.setattr(llm, "call", flaky_brief)
    outcome = _run(settings, conn, llm, http)

    assert len(attempts) == 2, "a 529 must be retried"
    assert outcome.brief_id, "the retry should have produced the brief"


@pytest.mark.negative
def test_a_non_retryable_failure_is_not_retried(wired, monkeypatch) -> None:
    """Retrying a deterministic failure just multiplies the spend."""
    settings, conn, llm, http = wired
    monkeypatch.setattr(budget_mod, "backoff", _no_sleep)

    original = llm.call
    attempts = []

    async def fail_brief(task: str, **kwargs: Any) -> LlmResult:
        if task == "brief":
            attempts.append(task)
            raise LlmError("schema validation failed")
        return await original(task, **kwargs)

    monkeypatch.setattr(llm, "call", fail_brief)
    _run(settings, conn, llm, http)

    assert len(attempts) == 1


def test_entity_counters_reach_the_run_card(wired) -> None:
    """C6 declared `entities_new`/`entities_seen` and nothing ever assigned
    them, so a run that minted 115 entities reported 0."""
    settings, conn, llm, http = wired
    outcome = _run(settings, conn, llm, http)

    assert outcome.counters["entities_seen"] > 0
    assert outcome.counters["entities_new"] > 0

    # The other half of the same blind spot: assert the stub's payload is still
    # schema-valid enough to actually ingest, rather than trusting the counters.
    kinds = {
        r["kind"]: r["n"]
        for r in conn.execute(
            "SELECT kind, COUNT(*) n FROM assertions WHERE run_id = ? GROUP BY kind",
            ("run:smoke",),
        )
    }
    assert kinds.get("entity", 0) > 0, "analyze wrote no entity assertions"
    assert kinds.get("relation", 0) > 0, "analyze wrote no relation assertions"


# ── run status semantics ─────────────────────────────────────────────


def test_a_clean_run_reports_succeeded_not_partial(wired) -> None:
    """`succeeded` was unreachable.

    Two messages fire on essentially every run -- the open alias-conflict
    backlog (a curation queue length) and the ontology's per-relation type
    rejections -- and both went through `ctx.warn`, so a run that did its whole
    job still reported `succeeded_partial`. Eight of nine live runs were
    partial; the project met this once and treated it as a rendering problem.
    """
    settings, conn, llm, http = wired
    outcome = _run(settings, conn, llm, http)

    assert outcome.status == "succeeded", outcome.warnings


def test_a_run_against_an_empty_trunk_reports_partial(wired) -> None:
    """The guard for a wiped-and-never-loaded database.

    Run 06a83e69 analyzed 84 clusters against exactly this state -- zero active
    entities and an empty ambiguous_surfaces -- minted 610 entities including
    three US Armies and a publisher-as-Company, and still reported `succeeded`.
    The state must degrade the run (`warn`, not `note`): a run with no prior
    identity to attach to and no ambiguity guard IS partial.
    """
    settings, conn, llm, http = wired
    conn.execute("DELETE FROM entities WHERE id = 'country:trunk-sentinel'")
    conn.execute("DELETE FROM ambiguous_surfaces")
    conn.commit()

    outcome = _run(settings, conn, llm, http)

    assert outcome.status == "succeeded_partial"
    # The two sentences are checked by what they SAY, not by the internal
    # names they used to carry (`gazetteer`, `ambiguous_surfaces`). Both are
    # degradations: with no names loaded, every name in the reading is filed
    # as new and a shared name is filed as one thing.
    assert all(w.level == "warning" for w in outcome.warnings), outcome.warnings
    assert any("knows no names yet" in w.text for w in outcome.warnings), outcome.warnings
    assert any("more than one thing" in w.text for w in outcome.warnings), outcome.warnings


def test_a_notice_is_surfaced_without_downgrading_the_run(wired) -> None:
    """Notices must stay visible -- they just must not decide the status."""
    settings, conn, llm, http = wired

    from venona.pipeline import curate as curate_mod

    real = curate_mod.run_curate

    async def noisy(ctx, **kwargs: Any):
        report = await real(ctx, **kwargs)
        ctx.note("42 unresolved alias conflict(s): 42 containment")
        return report

    orig = orchestrator.__dict__.get("run_curate")
    curate_mod.run_curate = noisy
    try:
        outcome = _run(settings, conn, llm, http)
    finally:
        curate_mod.run_curate = real
        if orig is not None:
            orchestrator.run_curate = orig

    assert outcome.status == "succeeded", "a notice must not downgrade the run"
    notice = next((w for w in outcome.warnings if "alias conflict" in w.text), None)
    assert notice is not None, "a notice must still reach the run's warnings"
    assert notice.level == "note", "and must carry the level that says it is one"


@pytest.mark.negative
def test_a_real_degradation_still_reports_partial(wired, monkeypatch) -> None:
    """The other half: `warn` must still mean something."""
    settings, conn, llm, http = wired

    original = llm.call

    async def fail_brief(task: str, **kwargs: Any) -> LlmResult:
        if task == "brief":
            raise LlmError("claude emitted a line over the stream limit")
        return await original(task, **kwargs)

    monkeypatch.setattr(llm, "call", fail_brief)
    outcome = _run(settings, conn, llm, http)

    assert outcome.status == "succeeded_partial"


def test_anchors_auto_load_on_the_first_run_only(wired) -> None:
    """A never-loaded anchor set loads itself; a loaded one is never touched.

    The trunk reaches the database only through an explicit Apply, which a new
    instance's operator has not necessarily pressed before the first
    (possibly scheduled) run -- and a run without its anchors resolves every
    entity cold. Reload stays exclusive to Apply: the second run here sees a
    changed file and must leave the loaded set alone.
    """
    from venona.services.seed import ANCHOR_RUN_ID

    settings, conn, llm, http = wired
    anchors = settings.profile_dir / "anchors.yaml"
    anchors.write_text(
        "entities:\n  - id: country:auto-trunk\n    type: Country\n    name: Auto Trunk\n"
    )
    _run(settings, conn, llm, http)
    count = conn.execute(
        "SELECT count(*) AS n FROM assertions WHERE run_id = ?", (ANCHOR_RUN_ID,)
    ).fetchone()["n"]
    assert count > 0, "first run must load the never-loaded trunk"

    anchors.write_text(
        anchors.read_text()
        + "  - id: country:auto-trunk-two\n    type: Country\n    name: Auto Trunk Two\n"
    )
    conn.execute("DELETE FROM runs WHERE id = 'run:smoke'")
    conn.commit()
    _run(settings, conn, llm, http)
    after = conn.execute(
        "SELECT count(*) AS n FROM assertions WHERE run_id = ?", (ANCHOR_RUN_ID,)
    ).fetchone()["n"]
    assert after == count, "a loaded anchor set is Apply's to change, never a run's"


@pytest.mark.negative
def test_an_open_circuit_degrades_the_brief_like_any_other_failed_call(wired, monkeypatch) -> None:
    """`BreakerOpen` is deliberately not an `LlmError`, and letting it propagate
    meant it bypassed every `except LlmError` degrade path: the brief's, and
    behind it `curate` and the projection -- the cascade
    `test_a_failing_brief_does_not_take_curate_down_with_it` pins, reopened by
    the breaker. A refused call is a failed call at every call site."""
    from venona.llm.breaker import BreakerOpen

    settings, conn, llm, http = wired
    original = llm.call

    async def refuse_brief(task: str, **kwargs: Any) -> LlmResult:
        if task == "brief":
            raise BreakerOpen("the claude CLI has failed 6 times in a row (529 overloaded)")
        return await original(task, **kwargs)

    monkeypatch.setattr(llm, "call", refuse_brief)
    outcome = _run(settings, conn, llm, http)

    assert outcome.brief_id is None
    assert outcome.status == "succeeded_partial", outcome.error
    assert any("529" in w.text for w in outcome.warnings), outcome.warnings

    stages = {
        r["stage"]
        for r in conn.execute("SELECT stage FROM run_stages WHERE run_id = ?", ("run:smoke",))
    }
    assert "curate" in stages, "curate must still run after a refused brief"

    row = conn.execute(
        "SELECT status, error FROM llm_calls WHERE run_id = ? AND task = 'brief'",
        ("run:smoke",),
    ).fetchone()
    assert row is not None, "a refused call left no trace in llm_calls"
    assert row["status"] == "error"
    assert "529" in row["error"]


def test_a_passed_in_executor_is_the_one_the_run_calls_through(wired, monkeypatch) -> None:
    """The API and the scheduler pass `app.state.llm`, so the breaker that
    `/api/health` reports is the breaker the nightly run actually trips. A run
    that built its own left health green while its calls failed."""
    settings, conn, llm, http = wired

    class Tracking(StubExecutor):
        pass

    mine = Tracking()
    outcome = _run(settings, conn, mine, http)

    assert outcome.status in ("succeeded", "succeeded_partial"), outcome.error
    assert mine.tasks, "the run never called through the executor it was handed"
    # STRUCTURAL NOW, not a discipline: the executor arrives on the context the
    # launcher built, and there is no longer any way for a run to make its own.
    assert not hasattr(orchestrator, "build_executor")


# ── reading without writing (epic Z, ADR 0228) ───────────────────────


def test_a_kind_that_declares_no_brief_never_writes_one(wired) -> None:
    """The whole point of a deep read, and the door it nearly came in by.

    `only=None` means "every stage", and the brief is one of them -- so a deep
    read told to go looking, which names no stage subset, ran the brief stage
    on the strength of the default. Nothing downstream would have said so: the
    router's own gate reads the table this now reads, so the brief would have
    been written, never announced, and its claim on the window's clusters would
    have made the next morning's brief skip them.
    """
    settings, conn, llm, http = wired
    outcome = _run(settings, conn, llm, http, kind="backfill")

    assert "brief" not in llm.tasks, "a deep read must not spend a call on a brief"
    assert outcome.brief_id is None


def test_a_deep_read_reports_what_it_read_rather_than_a_brief(wired) -> None:
    """`nothing_to_do` here means an empty queue, not a quiet day. A run that
    read something and learned from it has `delivered` to claim, and the phrase
    its kind puts on that word is "more read" rather than "brief written"."""
    settings, conn, llm, http = wired
    outcome = _run(settings, conn, llm, http, kind="backfill")

    assert outcome.status in {"succeeded", "succeeded_partial"}, outcome.error
    assert outcome.outcome == "delivered"

    from venona.services import run_types

    assert run_types.type_for("backfill").outcomes[outcome.outcome] == "more read"


def test_a_widened_window_is_refused_for_a_run_that_writes_a_brief(wired) -> None:
    """The backstop behind the router's 422. `retrieval_window` is reached by
    four call sites and three of them write a brief; a widened window arriving
    at any of those is the 2026-08 incident ADR 0228 recounts, so it raises
    rather than quietly scoring nine days against a 36-hour brief."""
    from venona.pipeline.types import MAX_BRIEF_LOOKBACK_HOURS, WindowTooWide, retrieval_window

    settings, conn, llm, http = wired
    wide = settings.model_copy(
        update={
            "pipeline": settings.pipeline.model_copy(
                update={
                    "lookback_hours": MAX_BRIEF_LOOKBACK_HOURS + 1,
                    "first_run_lookback_hours": MAX_BRIEF_LOOKBACK_HOURS + 1,
                }
            )
        }
    )
    with pytest.raises(WindowTooWide):
        retrieval_window(conn, now_iso="2026-08-05T12:00:00.000Z", settings=wide)

    # And the same window is fine for the reading, which is the point.
    cutoff, hours = retrieval_window(
        conn, now_iso="2026-08-05T12:00:00.000Z", settings=wide, writes_brief=False
    )
    assert hours == MAX_BRIEF_LOOKBACK_HOURS + 1
    assert cutoff < "2026-08-05"


def test_a_run_ceiling_never_reaches_the_shared_settings(wired) -> None:
    """`app.state.settings` is one object shared with every other run, the
    scheduler and `/api/health`. `cli.py::_analyze_run` sets its call cap by
    assignment, which is safe only because the CLI is one process and one run."""
    from venona.pipeline.types import with_overrides

    settings, _conn, _llm, _http = wired
    before = settings.llm.max_calls_per_run

    derived = with_overrides(settings, max_calls=before + 500, lookback_hours=2160)

    assert derived.llm.max_calls_per_run == before + 500
    assert derived.pipeline.lookback_hours == 2160
    assert settings.llm.max_calls_per_run == before
    assert settings.pipeline.lookback_hours != 2160
    assert with_overrides(settings) is settings


def test_a_deep_read_reaches_a_cluster_the_brief_window_excludes(wired) -> None:
    """End to end, because the unit test above it would not have caught this.

    The first real deep read reported `succeeded`, finished analyze in under two
    seconds with `clusters_seen: 0`, and left all 171 waiting clusters exactly
    where they were. Everything in the chain was individually right: the window
    override existed, the kind declared no brief, the dialog sent no lookback.
    "No lookback" resolved to the CONFIGURED window rather than to no window,
    and nothing anywhere said so.
    """
    settings, conn, llm, http = wired
    old = "2026-06-09T00:00:00Z"
    body = "x" * 4000
    conn.execute(
        "INSERT INTO documents (id, url, url_key, title, text_nfc, text_chars, "
        "content_hash, cluster_id, published_at, fetched_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "doc:backlog",
            "https://e.test/backlog",
            "https://e.test/backlog",
            "A story from months ago",
            body,
            len(body),
            "hash:backlog",
            "cluster:backlog",
            old,
            old,
            old,
        ),
    )
    conn.execute(
        "INSERT INTO clusters (id, canonical_doc_id, title, published_at, first_seen, last_seen)"
        " VALUES ('cluster:backlog', 'doc:backlog', 'A story from months ago', ?, ?, ?)",
        (old, old, old),
    )
    conn.commit()

    _run(settings, conn, llm, http, kind="backfill")

    analyzed = conn.execute(
        "SELECT analyzed_at FROM clusters WHERE id = 'cluster:backlog'"
    ).fetchone()
    assert analyzed["analyzed_at"] is not None, (
        "a deep read that names no window must read the backlog, "
        "not the brief's last day and a half"
    )


def test_run_now_still_fetches(wired) -> None:
    """The regression that shipped, and the reason this is an END-TO-END test.

    `TriggerRun.stages_only` was written as "drop fetch from any kind that
    declares it", which is true of the deep read and also true of `on_demand`
    and `daily` -- so `Run now` analysed what was already stored and never went
    and got anything. It reported `succeeded` either way. On production,
    `run:06aa4b85` has three stage rows and no `fetch` among them.

    Asserted through the payload rather than the button, because the payload is
    where the decision is made and the button only carries it.
    """
    from venona.api.routers.runs import TriggerRun

    for kind in ("on_demand", "daily"):
        assert TriggerRun(kind=kind).stages_only is None, (
            f"{kind} must run every stage, fetch included"
        )

    # ...while the deep read still leaves fetch out unless asked.
    assert "fetch" not in (TriggerRun(kind="backfill").stages_only or ())
    assert "fetch" in (TriggerRun(kind="backfill", go_looking=True).stages_only or ())
    # ...and a queue-only kind names exactly its own stage.
    assert TriggerRun(kind="adjudicate").stages_only == ("curate",)
