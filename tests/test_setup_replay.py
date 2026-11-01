"""The wizard replayed from the envelopes a live run recorded.

`test_setup_wizard.py` stubs the model, so it proves the orchestration and
nothing about the envelopes: a stub answers whatever the test wants, in the
shape the test already believes in. This file runs the same code over the real
`ReplayLlmExecutor` and the envelopes under `fixtures/llm/setup_*/`, which is
the only thing that shows the recorded output survives the parsers, the
per-document validators and the anchor resolver.

Recorded 2026-09-01 against a scaffolded instance; the inputs, the cost and the
survey dossier are in `fixtures/setup/`. Read the inputs from that file rather
than restating them here -- every fixture key is a sha over the assembled
prompt, and the descriptor and the three free-text fields are spliced into all
of them, so a retyped character misses every fixture at once.

The survey stage is deliberately NOT replayed, and that is a property of the
recording rather than a shortcut. `setup_service` appends `Today's date: ...`
to the survey prompt only when `not settings.offline`, so the envelope a live
recording writes is keyed on a prompt an offline run never assembles -- the
same shape as every fixture under `fixtures/llm/discovery/`. What makes the
rest replayable is that a stored dossier skips the survey entirely, which is
the path a single-document re-run already uses.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
import yaml
from venona import ontology as ontology_module
from venona.db import core as db
from venona.llm.executor import build_executor
from venona.pipeline.types import build_context
from venona.services import run_service, setup_service
from venona.services.run_scope import RunScope
from venona.settings import get_settings

REPO = Path(__file__).resolve().parents[1]
SETUP_FIXTURES = REPO / "fixtures" / "setup"

#: What a freshly scaffolded instance carries until the wizard drafts one.
SKELETON_ONTOLOGY = "version: 0\nentity_types: {}\npredicates: {}\n"


@pytest.fixture
def inputs() -> dict:
    return json.loads((SETUP_FIXTURES / "toy_domain.json").read_text())


@pytest.fixture
def stored_dossier() -> dict:
    """The dossier as it stood when the SOURCES call was recorded.

    `_sweeps` is the sources stage's own output, written back into the dossier
    after that call returns -- so the saved file carries a key the prompt that
    produced it never saw. Replaying from the file verbatim rebuilds a longer
    prompt and misses the one fixture, while every later document, which did
    read the dossier with `_sweeps` in it, matches.
    """
    dossier = json.loads((SETUP_FIXTURES / "dossier.json").read_text())
    dossier.pop("_sweeps", None)
    return dossier


@pytest.fixture
def replay_settings(tmp_path: Path, inputs: dict, monkeypatch: pytest.MonkeyPatch):
    """A scaffolded instance's directories, and the real fixtures directory.

    BOTH knobs, and the second one is load-bearing: `build_http_client` reads
    `offline`, but `build_executor` reads `llm.backend`, and `VENONA_OFFLINE=1`
    is the only thing that normally sets the pair together. Nothing in the
    suite sets that variable -- every other test injects a stub executor -- so
    a test that builds a real executor having set `offline` alone gets the
    LIVE one, and the missing-fixture guard it is relying on never runs.
    """
    scratch = copy.deepcopy(get_settings())
    scratch.offline = True
    scratch.llm.backend = "replay"
    scratch.profile_dir = tmp_path / "profile"
    scratch.config_dir = tmp_path / "config"
    scratch.data_dir = tmp_path / "data"
    for directory in (scratch.profile_dir, scratch.config_dir, scratch.data_dir):
        directory.mkdir(parents=True)

    # The descriptor the wizard's step 1 writes, derived the way the router
    # derives it -- `get_domain()` parses this back, so what has to match the
    # recording is the parsed values, not the bytes.
    descriptor = setup_service.initial_descriptor(**inputs["descriptor"])
    (scratch.config_dir / "domain.yaml").write_text(
        yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True, width=100)
    )
    (scratch.config_dir / "ontology.yaml").write_text(SKELETON_ONTOLOGY)

    # `get_domain()` is the one input to these prompts that does NOT arrive
    # through the settings argument: it resolves `get_settings().config_dir`
    # itself. Left alone, the replay assembles every prompt around the repo's
    # own descriptor and misses all twelve fixtures at once, which reads as
    # "the recording is stale" rather than "the test pointed somewhere else".
    monkeypatch.setattr("venona.settings.get_settings", lambda: scratch)
    return scratch


@pytest.fixture
def skeleton_ontology(replay_settings, tmp_path: Path):
    """`get_ontology()` resolves through a module override, not settings."""
    original = ontology_module.ONTOLOGY_PATH
    ontology_module.ONTOLOGY_PATH = replay_settings.config_dir / "ontology.yaml"
    ontology_module.get_ontology.cache_clear()
    yield
    ontology_module.ONTOLOGY_PATH = original
    ontology_module.get_ontology.cache_clear()


class Target:
    def __init__(self, conn, ctx, run_id: str) -> None:
        self.run_id = run_id
        self.conn = conn
        self.ctx = ctx
        self.events: list[tuple[str, str | None, dict]] = []
        # The wizard's steps run on the same lifecycle every other run uses, so
        # the stub carries one rather than modelling a shape nothing has.
        self.scope = RunScope(run_id=run_id, conn=conn, ctx=ctx, emit=self.emit)

    async def emit(self, type_, stage, data):
        self.events.append((type_, stage, data))

    def check_cancel(self):
        return None


def _replay(settings, conn: db.Conn, params: dict, run_id: str = "run:setup-replay"):
    from venona.domain import get_domain
    from venona.sources.http import build_http_client

    get_domain.cache_clear()
    llm = build_executor(settings)
    ctx = build_context(
        run_id=run_id,
        db=conn,
        llm=llm,
        settings=settings,
        http=build_http_client(settings),
    )
    target = Target(conn, ctx, run_id)
    run_service.create_run(
        conn,
        run_id=run_id,
        kind="domain_setup",
        params=params,
        profile_hash=None,
        now=ctx.clock.now(),
    )
    try:
        outcome = asyncio.run(setup_service.run_domain_setup(target, settings, params))
    finally:
        get_domain.cache_clear()
    return outcome, target


def test_every_document_replays_from_the_recorded_envelopes(
    replay_settings, skeleton_ontology, inputs: dict, stored_dossier: dict, conn: db.Conn
) -> None:
    """The whole wizard, offline, from the stored dossier onward.

    A missing envelope raises inside `ReplayLlmExecutor` rather than falling
    through to a live call, so this failing means the fixtures no longer match
    the prompts -- re-record rather than loosen the assertion.
    """
    setup_service.save_dossier(replay_settings, stored_dossier)

    params = dict(inputs["generate"])
    params["domain"] = inputs["descriptor"]["slug"]
    outcome, target = _replay(replay_settings, conn, params)

    assert outcome.data["failed"] == [], (
        f"every document has an envelope, so nothing should fail: {target.ctx.warnings}"
    )
    assert sorted(outcome.data["drafted"]) == sorted(setup_service.ALL_DOCS)
    assert outcome.outcome == "delivered"

    # The survey never ran: the dossier answered, which is what keeps the
    # date-stamped survey envelopes out of the replay path.
    assert not any(stage == "survey" for _, stage, _ in target.events)


def test_the_ontology_and_anchors_drafts_are_valid_documents(
    replay_settings, skeleton_ontology, inputs: dict, stored_dossier: dict, conn: db.Conn
) -> None:
    """The two the recording could not produce on its first attempt.

    The anchor schema's entity-type and predicate enums are built from the
    DRAFTED ontology, so this also pins the ordering: a replay that produced
    anchors from the skeleton would be refusing, not drafting.
    """
    from venona.services import draft_service

    setup_service.save_dossier(replay_settings, stored_dossier)
    params = dict(inputs["generate"])
    params["domain"] = inputs["descriptor"]["slug"]
    _replay(replay_settings, conn, params)

    drafts = {}
    for key in ("ontology", "anchors"):
        content, meta = draft_service.read_draft(replay_settings, key)
        assert meta.valid, f"{key} draft fails its validator: {meta.error}"
        drafts[key] = content

    ontology = ontology_module.parse_ontology(yaml.safe_load(drafts["ontology"]))
    assert ontology.type_names, "the drafted ontology must declare entity types"
    assert ontology.predicate_names, "the drafted ontology must declare predicates"

    anchors = yaml.safe_load(drafts["anchors"])
    declared = {row["type"] for row in anchors["entities"]}
    assert declared <= set(ontology.type_names), (
        f"anchors used types the drafted ontology does not declare: "
        f"{sorted(declared - set(ontology.type_names))}"
    )
