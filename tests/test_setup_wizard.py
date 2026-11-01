"""The domain-setup generation run, offline.

A stub executor stands in for the model, keyed by task -- the orchestration
under test is `setup_service`'s: which drafts land, how a failed document
degrades, and that a stored dossier makes a single-document re-run cheap.
Nothing here spends; nothing here touches the real config or profile trees.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from venona.db import core as db
from venona.pipeline.types import build_context
from venona.services import config_service as cs
from venona.services import draft_service, run_service, setup_service
from venona.services.run_scope import RunScope
from venona.settings import get_settings

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path: Path):
    real = get_settings()
    scratch = copy.deepcopy(real)
    scratch.offline = True
    scratch.profile_dir = tmp_path / "profile"
    scratch.config_dir = tmp_path / "config"
    scratch.profile_dir.mkdir()
    scratch.config_dir.mkdir()
    for meta in cs.EDITABLE.values():
        if meta["dir"] == "ontology":
            continue
        source = (real.profile_dir if meta["dir"] == "profile" else real.config_dir) / meta["file"]
        target = (scratch.profile_dir if meta["dir"] == "profile" else scratch.config_dir) / meta[
            "file"
        ]
        if source.exists():
            target.write_text(source.read_text())
    return scratch


class StubExecutor:
    """Answers by task. A task absent from `outputs` raises, like a missing
    fixture would -- the degradation path under test."""

    def __init__(self, outputs: dict[str, object]) -> None:
        self.outputs = outputs
        self.calls: list[str] = []

    async def call(self, task, *, user_content, schema, **kwargs):
        from venona.llm.executor import LlmError, LlmResult, Usage

        self.calls.append(task)
        if task not in self.outputs:
            raise LlmError(f"stub has no answer for {task}")
        payload = self.outputs[task]
        if callable(payload):
            payload = payload(user_content)
        return LlmResult(data=payload, usage=Usage(), cache_key="stub")


class StubHttp:
    """Every feed probe fails, so the sources stage exercises its removal
    path without any network."""

    async def get(self, url, **kwargs):
        raise OSError("offline")


class Target:
    def __init__(self, conn, ctx, run_id="run:setup-test"):
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


SURVEY_OUT = {"summary": "a survey.", "items": [{"name": "Example Org", "detail": "builds things"}]}
SOURCES_OUT = {
    "feeds": [
        {
            "id": "example_feed",
            "name": "Example Feed",
            "url": "https://example.com/rss",
            "publisher": "Example",
        }
    ],
    "sweeps": [
        {"id": "sweep_core", "name": "Core", "tags": ["core_theme"], "rationale": "the middle"}
    ],
}
ANCHORS_OUT = {
    "entities": [
        {"name": "Example Org", "type": "Company", "summary": "builds things", "relevance": 0.9},
        {"name": "Example Country", "type": "Country", "summary": "a state", "relevance": 0.3},
    ],
    "edges": [
        {"src_name": "Example Org", "predicate": "BASED_IN", "dst_name": "Example Country"},
        {"src_name": "Example Org", "predicate": "PART_OF", "dst_name": "Nowhere"},
    ],
    "ambiguous": [{"surface": "EO", "reason": "two readings"}],
}


def _document_outputs() -> dict[str, object]:
    real = get_settings()
    exemplar = lambda name: (real.fixtures_dir / "exemplars" / name).read_text()  # noqa: E731
    return {
        "setup_survey": SURVEY_OUT,
        "setup_sources": SOURCES_OUT,
        "setup_ontology": {"document": exemplar("ontology.yaml")},
        "setup_domain": {"document": exemplar("domain.yaml")},
        "setup_relevance": {"document": "# Who I am\n\nI read this domain.\n"},
        "setup_research_prompt": {"document": "### Core\n\nLook for core things.\n"},
        "setup_brief_prompt": {"document": "Write plainly.\n"},
        "setup_rubric": {"document": exemplar("scoring_rubric.yaml")},
        "setup_sections": {"document": "sections:\n  - name: CORE\n    covers: core themes\n"},
        "setup_lexicon": {"document": "expansions: {}\nstoplist: []\n"},
        "setup_anchors_trunk": ANCHORS_OUT,
        "setup_anchors_expand": {"entities": [], "edges": [], "ambiguous": []},
    }


def _run(settings, conn, outputs, params, run_id="run:setup-test"):
    llm = StubExecutor(outputs)
    ctx = build_context(run_id=run_id, db=conn, llm=llm, settings=settings, http=StubHttp())
    target = Target(conn, ctx, run_id)
    run_service.create_run(
        conn,
        run_id=target.run_id,
        kind="domain_setup",
        params=params,
        profile_hash=None,
        now=ctx.clock.now(),
    )
    import asyncio

    outcome = asyncio.run(setup_service.run_domain_setup(target, settings, params))
    return outcome, llm, target


def test_a_full_run_lands_every_draft(settings, conn: db.Conn) -> None:
    outcome, llm, target = _run(settings, conn, _document_outputs(), {})

    drafted = set(outcome.data["drafted"])
    assert drafted == set(setup_service.ALL_DOCS)
    # Every document drafted, so the run DELIVERED. `status` is left unset by
    # the work and derived from `ctx.degraded` in `run_launcher._runner`, which
    # this harness does not go through -- so the dimension asserted here is the
    # one the work itself decides. ADR 0183.
    assert outcome.outcome == "delivered"
    assert not target.ctx.degraded
    assert setup_service.load_dossier(settings) is not None

    # the drafts are real files the editor can open, with provenance
    content, meta = draft_service.read_draft(settings, "relevance_profile")
    assert meta.run_id == target.run_id
    assert "I read this domain." in content

    # the sources draft carries the dropped feed with its reason
    sources, _ = draft_service.read_draft(settings, "sources")
    assert "removed:" in sources and "offline" in sources
    parsed = yaml.safe_load(sources)
    assert any(s["id"] == "sweep_core" for s in parsed["sources"])

    # anchors: ids minted, the dangling edge dropped, ambiguity kept
    anchors, _ = draft_service.read_draft(settings, "anchors")
    doc = yaml.safe_load(anchors)
    assert any(e["id"].startswith("company:") for e in doc["entities"])
    assert all(edge["dst"] != "Nowhere" for edge in doc["edges"])
    assert doc["ambiguous"][0]["surface"] == "EO"


def test_a_failed_document_degrades_to_partial(settings, conn: db.Conn) -> None:
    outputs = _document_outputs()
    del outputs["setup_lexicon"]
    outcome, llm, target = _run(settings, conn, outputs, {})
    # Two claims, and they used to be one word: some drafts landed (the
    # outcome) and the run could not do everything it set out to (the
    # degradation, which `_runner` reads off `ctx.degraded`).
    assert outcome.outcome == "some"
    assert target.ctx.degraded
    assert "lexicon" in outcome.data["failed"]
    assert "relevance_profile" in outcome.data["drafted"]


def test_a_failed_stage_records_itself_as_failed(settings, conn: db.Conn) -> None:
    """⭐ The stage strip said every part of setup succeeded.

    `finish` hardcoded `status="ok"`, and a failing stage still calls it after
    its `except` block — so a stage whose model call raised appended itself to
    `failed`, warned on the run, and then wrote an `ok` row into `run_stages`.
    The wizard's stage strip reads those rows, so the one surface a person
    watches to see WHICH part of setup went wrong reported that none of it did.
    """
    outputs = _document_outputs()
    del outputs["setup_lexicon"]
    outcome, _llm, target = _run(settings, conn, outputs, {})

    assert outcome.outcome == "some"
    assert target.ctx.degraded
    rows = {
        row["stage"]: row
        for row in conn.execute(
            "SELECT stage, status, error FROM run_stages WHERE run_id = ?", (target.run_id,)
        ).fetchall()
    }
    assert rows, "the run recorded no stages at all"
    # ONE STAGE IS NOT ONE DOCUMENT: `profile` drafts several, and the failure is
    # recorded under the DOCUMENT's key, so matching on the stage name alone
    # would report a profile stage that lost a document as `ok`.
    assert rows["profile"]["status"] == "error"
    assert "lexicon" in (rows["profile"]["error"] or ""), (
        "a failed stage must say WHICH document went wrong"
    )
    # The stages that DID work still say so — an error status on everything
    # would be the same lie in the other direction.
    assert any(row["status"] == "ok" for stage, row in rows.items() if stage != "profile")


def test_a_single_doc_rerun_reuses_the_dossier(settings, conn: db.Conn) -> None:
    _run(settings, conn, _document_outputs(), {})

    outputs = _document_outputs()
    outcome, llm, target = _run(
        settings,
        conn,
        outputs,
        {"docs": ["lexicon"], "refresh_survey": False},
        run_id="run:setup-test-2",
    )
    assert outcome.data["drafted"] == ["lexicon"]
    # no survey calls: the stored dossier answered
    assert "setup_survey" not in llm.calls
    assert llm.calls == ["setup_lexicon"]


def test_a_tiny_budget_degrades_not_fails(settings, conn: db.Conn) -> None:
    trimmed = copy.deepcopy(settings)
    trimmed.llm.setup_max_calls = 6
    outcome, llm, target = _run(trimmed, conn, _document_outputs(), {})
    assert outcome.outcome == "some"
    assert target.ctx.degraded
    assert outcome.data["drafted"]  # something landed before the cap
    assert outcome.data["failed"]  # and something was cut off by it


def test_is_configured_truth_table(settings) -> None:
    assert setup_service.is_configured(settings)  # checked-in defense file

    placeholder = dict(
        setup_service.initial_descriptor(slug="x", display_name="X", tagline="x", login_blurb="x")
    )
    placeholder["slug"] = setup_service.PLACEHOLDER_SLUG
    (settings.config_dir / "domain.yaml").write_text(yaml.safe_dump(placeholder))
    assert not setup_service.is_configured(settings)

    (settings.config_dir / "domain.yaml").unlink()
    assert setup_service.is_configured(settings)  # defaults apply


def test_the_dossier_is_deterministic_json(settings) -> None:
    setup_service.save_dossier(settings, {"b": 1, "a": {"z": 2, "y": 3}})
    text = setup_service._dossier_path(settings).read_text()
    assert json.loads(text) == {"a": {"y": 3, "z": 2}, "b": 1}
    assert text.index('"a"') < text.index('"b"')


def test_anchors_refuses_the_skeleton_ontology_instead_of_an_empty_enum(
    settings, conn: db.Conn, tmp_path: Path
) -> None:
    """A fresh scaffold's ontology is version 0 with no types and no
    predicates, and the anchor schema's two enums are built from those lists.

    Found by recording the wizard live (epic C): the ontology stage timed out,
    so anchors fell back to the installed skeleton and the CLI refused the call
    outright -- `--json-schema is not a valid JSON Schema: ... enum must NOT
    have fewer than 1 items`, four sub-paths, no mention of the ontology. The
    run degraded correctly; what it could not do was say why.
    """
    from venona import ontology as ontology_module

    skeleton = tmp_path / "skeleton-ontology.yaml"
    skeleton.write_text("version: 0\nentity_types: {}\npredicates: {}\n")
    original = ontology_module.ONTOLOGY_PATH
    ontology_module.ONTOLOGY_PATH = skeleton
    ontology_module.get_ontology.cache_clear()
    try:
        outputs = _document_outputs()
        # The ontology stage produces nothing, so `draft_ontology` stays None
        # and the fallback is the skeleton -- the live run's exact shape.
        del outputs["setup_ontology"]
        outcome, llm, target = _run(settings, conn, outputs, {"docs": ["ontology", "anchors"]})
    finally:
        ontology_module.ONTOLOGY_PATH = original
        ontology_module.get_ontology.cache_clear()

    assert "anchors" in outcome.data["failed"]
    assert any("no ontology to anchor against" in w.text for w in target.ctx.warnings), (
        f"the warning must name the ontology, got {target.ctx.warnings}"
    )
    # Refused before spending: an invalid schema is knowable without a call.
    assert "setup_anchors_trunk" not in llm.calls


def test_an_invalid_ontology_draft_earns_its_repair(
    settings, conn: db.Conn, tmp_path: Path
) -> None:
    """The ontology's YAML slip, from the live recording (epic C).

    One attempt returned `guidance: Symmetric: stored once with endpoints in
    ...` -- an unquoted scalar holding a second `: `, which fails the parse. A
    retry from the identical prompt was valid, so the slip is a formatting one
    rather than a systematic one. What makes it worth a call is the ordering:
    anchors are drafted from the ontology DRAFT, so one bad line costs two
    documents and the reviewer is left with neither.

    The skeleton `ONTOLOGY_PATH` is what makes the second half of that
    provable. `_draft_anchors` falls back to the LIVE ontology when there is
    no usable draft, so against a populated one anchors draft whether the
    repair fired or not, and the assertion would hold for the wrong reason.
    """
    from venona import ontology as ontology_module

    real = get_settings()
    good = (real.fixtures_dir / "exemplars" / "ontology.yaml").read_text()
    broken = good + "\nguidance: Symmetric: stored once with endpoints in either order\n"
    # Pinned, so the test cannot rot into passing on a document that parses.
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(broken)

    outputs = _document_outputs()
    # The repair is the only call whose user content carries the validator's
    # complaint, which is what tells the two attempts apart here.
    outputs["setup_ontology"] = lambda user_content: {
        "document": good if "failed validation" in user_content else broken
    }

    skeleton = tmp_path / "skeleton-ontology.yaml"
    skeleton.write_text("version: 0\nentity_types: {}\npredicates: {}\n")
    original = ontology_module.ONTOLOGY_PATH
    ontology_module.ONTOLOGY_PATH = skeleton
    ontology_module.get_ontology.cache_clear()
    try:
        outcome, llm, target = _run(settings, conn, outputs, {"docs": ["ontology", "anchors"]})
    finally:
        ontology_module.ONTOLOGY_PATH = original
        ontology_module.get_ontology.cache_clear()

    assert llm.calls.count("setup_ontology") == 2, (
        f"the invalid draft must earn exactly one repair, got {llm.calls}"
    )
    _, meta = draft_service.read_draft(settings, "ontology")
    assert meta.valid, f"the repaired draft must validate: {meta.error}"
    assert outcome.data["failed"] == [], target.ctx.warnings
    assert "anchors" in outcome.data["drafted"], (
        "anchors read the ontology draft, so the repair has to land before them"
    )


def test_the_anchors_exemplar_satisfies_the_rule_it_teaches() -> None:
    """The excerpt is the only place the model sees what a trunk looks like, and
    `_draft_anchors` drops any edge naming an entity it did not declare.

    It shipped with 14 entities, all of one type, and 26 edges of which 26 had an
    endpoint declared nowhere in the file. Fed back through the wizard it would
    have lost every edge it has, while teaching that a document of unattached
    nodes is what a mature instance looks like.
    """
    import yaml
    from venona.settings import get_settings

    path = get_settings().fixtures_dir / "exemplars" / "anchors_excerpt.yaml"
    doc = yaml.safe_load(path.read_text())
    declared = {e["id"] for e in doc["entities"]}

    dangling = [
        f"{e['src']} -{e['predicate']}-> {e['dst']}"
        for e in doc["edges"]
        if e["src"] not in declared or e["dst"] not in declared
    ]
    assert not dangling, (
        f"{len(dangling)} edge(s) name an entity the excerpt does not declare, "
        f"so the wizard would drop them: {dangling[:3]}"
    )

    # And it must show structure, not a list of names: an excerpt whose entities
    # carry no edges teaches exactly the shape this whole exercise was about.
    attached = {e["src"] for e in doc["edges"]} | {e["dst"] for e in doc["edges"]}
    floating = declared - attached
    assert len(floating) < len(declared) / 2, (
        f"{len(floating)} of {len(declared)} entities carry no edge; the worked "
        f"example is meant to demonstrate a connected trunk"
    )
