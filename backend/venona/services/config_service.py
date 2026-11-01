"""Read and write every user-editable configuration document.

These are the files that decide what venona thinks is worth your attention, what
it is allowed to assert, and where it looks. The write path is careful in four
specific ways:

  * **Name allowlist.** The path comes from a URL; only declared documents are
    writable, and nothing may traverse out of its base directory.
  * **Validate through the REAL loader.** Every validator below calls the same
    `parse_*` the boot path calls -- a second implementation is a second thing
    to drift.
  * **Snapshot, then write atomically.** An edit is always reversible.
  * **Say what did NOT take effect.** `config/settings.yaml` has fields whose
    consumers read them once at startup, and a setting that read as applied and
    silently was not has already cost this project once (ADR 0081).

Conflict detection is a content hash, and it is a statement about two PEOPLE
editing; `_exclusive` below is a statement about two WRITES, which is a
different problem and needs a real lock.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from venona import ontology as ontology_module
from venona import untrust
from venona.ontology import ANCHOR_PREDICATES, Ontology, get_ontology
from venona.settings import (
    LlmSettings,
    Paths,
    PipelineSettings,
    ScheduleSettings,
    Settings,
    get_settings,
    unknown_top_level_keys,
)

log = logging.getLogger(__name__)

MAX_BYTES = 256 * 1024

# Stated rather than inherited: `read_text()` with no encoding uses the locale's,
# and these documents carry em-dashes, so under a non-UTF-8 locale the reader
# would fail on a file this module's own writer produced.
ENCODING = "utf-8"

# The groups the documents are listed under, in reading order. A key rather than
# a display string because `ConfigIndexView` needs an order and a sentence per
# group; `title` and `blurb` are read by a person, not by the pipeline.
GROUPS: dict[str, dict[str, str]] = {
    "reading": {
        "title": "What Venona reads",
        "blurb": "The publications it pulls from, and the themes it searches for beyond them.",
    },
    "importance": {
        "title": "What counts as important",
        "blurb": "Your thesis about what matters here, and how each story is scored against it.",
    },
    "names": {
        "title": "The names it recognises",
        "blurb": "The kinds of names Venona knows, how they connect, and how "
        "abbreviations are read.",
    },
    "writing": {
        "title": "How it writes",
        "blurb": "The voice of the brief, the sections it always carries, and how "
        "study questions are phrased.",
    },
    "audience": {
        "title": "Who it reads for",
        "blurb": "The subject Venona covers here, and the worked examples its prompts use.",
    },
    "running": {
        "title": "How it runs",
        "blurb": "Run limits, model choices and the daily schedule.",
    },
    "sent": {
        "title": "What it sends the model",
        "blurb": "Built from the documents above, and shown so you can read what "
        "was actually asked.",
    },
}


def groups() -> list[dict[str, Any]]:
    """The group table, flattened for the wire in declaration order."""
    return [
        {"key": key, "title": spec["title"], "blurb": spec["blurb"]} for key, spec in GROUPS.items()
    ]


# Everything editable. The Settings rail is built from this, so a document added
# here appears there with no frontend change.
#
# `extraction_guide` does not belong here: it is GENERATED from
# config/ontology.yaml, so an entry would be an editable box that does nothing.
# Per-type `guidance:` in the ontology is where that text belongs.
EDITABLE: dict[str, dict[str, str]] = {
    "relevance_profile": {
        "dir": "profile",
        "file": "relevance_profile.md",
        "title": "Relevance profile",
        "description": "What Venona thinks matters. Read on every run; edit freely.",
        "group": "importance",
        "format": "markdown",
        "rescore_hint": "free",
    },
    "scoring_rubric": {
        "dir": "profile",
        "file": "scoring_rubric.yaml",
        "title": "Scoring rubric",
        "description": "The story kinds Venona scores against, and how much each one "
        "counts. Changing a weight is free; changing what a kind means needs a "
        "fresh reading.",
        "group": "importance",
        "format": "yaml",
        "rescore_hint": "weights_free",
    },
    "research_prompt": {
        "dir": "profile",
        "file": "research_prompt.md",
        "title": "Research prompt",
        "description": "The themes Venona searches for beyond the publications it already follows.",
        "group": "reading",
        "format": "markdown",
        "rescore_hint": "next_run",
    },
    "learning_prompt": {
        "dir": "profile",
        "file": "learning_prompt.md",
        # The phrase an operator scans the rail for; titled anything else it hides.
        "title": "Learning prompt",
        "description": "How generated study questions are written.",
        "group": "writing",
        "format": "markdown",
        "rescore_hint": "next_run",
    },
    "brief_prompt": {
        "dir": "profile",
        "file": "brief_prompt.md",
        "title": "Brief voice",
        "description": "How the brief is written. Structure and judgement, not selection.",
        "group": "writing",
        "format": "markdown",
        "rescore_hint": "next_run",
    },
    "lexicon": {
        "dir": "profile",
        "file": "lexicon.yaml",
        "title": "Lexicon",
        "description": "Abbreviation expansions and the acronym ignore list.",
        "group": "names",
        "format": "yaml",
        "rescore_hint": "lexicon",
    },
    "brief_sections": {
        "dir": "profile",
        "file": "brief_sections.yaml",
        "title": "Brief sections",
        "description": "The standing sections of the brief, in reading order.",
        "group": "writing",
        "format": "yaml",
        "rescore_hint": "brief_sections",
    },
    "ontology": {
        "dir": "ontology",
        "file": "ontology.yaml",
        "title": "Types and relationships",
        "description": "The kinds of names Venona recognises, and the relationships it may record.",
        "group": "names",
        "format": "yaml",
        "rescore_hint": "ontology",
    },
    "anchors": {
        "dir": "profile",
        "file": "anchors.yaml",
        "title": "Anchors",
        "description": "The graph's fixed starting points. Saving is not loading; press Apply.",
        "group": "names",
        "format": "yaml",
        "rescore_hint": "anchors",
    },
    "sources": {
        "dir": "config",
        "file": "sources.yaml",
        "title": "Sources",
        "description": "The publications Venona pulls from, and the sweeps that look past them.",
        "group": "reading",
        "format": "yaml",
        "rescore_hint": "sources",
    },
    "settings": {
        "dir": "config",
        "file": "settings.yaml",
        "title": "Settings",
        "description": "Run limits, model choices and the schedule.",
        "group": "running",
        "format": "yaml",
        "rescore_hint": "settings",
    },
    "domain": {
        "dir": "config",
        "file": "domain.yaml",
        "title": "Domain",
        "description": "Who Venona reads for here: the subject phrases and "
        "worked examples its prompts use. The short name is frozen.",
        "group": "audience",
        "format": "yaml",
        "rescore_hint": "next_run",
    },
}

SAFE_KEY = re.compile(r"^[a-z_]+$")

# The config service resolves its own document paths from these, so a bad edit
# would move the editor out from under itself.
PATH_FIELDS = ("repo_root", "data_dir", "config_dir", "profile_dir", "fixtures_dir")

# Settings the server EXECUTES or AUTHENTICATES WITH. `PUT /api/config/settings`
# writes a file `get_settings` layers UNDER env, and `GET` hands it back, so
# anything reachable from it is writable by whoever holds a session cookie. A
# session cookie should not equal a shell.
#
# `cli_path` is the concrete case: `executor.py::_binary` runs `shutil.which` on
# it and execs the result. `llm.harness` is DELIBERATELY NOT HERE, and the
# difference is the point -- it is an enum resolved through a table in code to a
# program name, so nothing an administrator can type reaches argv. The pins are
# frozen because they are compared against what the image installed: changing one
# without the other turns the check green while changing nothing.
FROZEN_FIELDS = (
    ("llm", "backend"),
    ("llm", "cli_path"),
    ("llm", "pinned_version"),
    ("llm", "codex_cli_path"),
    ("llm", "codex_pinned_version"),
    ("llm", "grok_cli_path"),
    ("llm", "grok_pinned_version"),
    ("llm", "confine"),
)

# Same rule, top level. ATTRIBUTE names, not YAML keys: `Settings.neo4j_uri`
# declares `alias="NEO4J_URI"` without `populate_by_name`, so `NEO4J_URI:`
# populates the field while `neo4j_uri:` is dropped by `extra="ignore"`. Freezing
# the raw keys would catch the spelling nobody can use and miss the one they can.
FROZEN_TOP = ("neo4j_uri", "neo4j_user", "neo4j_password", "offline")

# Written by `services/config_migrations`. Its own list because `FROZEN_TOP`'s
# refusal talks about binaries and databases, and a reader who retyped a version
# number deserves an answer about the version number.
DEPLOY_OWNED_TOP = ("settings_version",)


# Settings whose consumers capture them at startup: `build_executor` runs in
# `lifespan`, and rebinding `app.state.settings` does not reach it. The schedule
# is not here -- a save calls `apply_schedule`, which re-points the running job.
# `harness` and `billing_mode` are NOT here on purpose: `_settings_saved`
# rebuilds the executors when they move, so listing them would report a save as
# not having taken effect while it had -- the fourth promise inverted.
class ConfigError(ValueError):
    pass


@dataclass
class ConfigDoc:
    key: str
    title: str
    description: str
    group: str
    format: str
    rescore_hint: str
    content: str
    version: str
    updated_at: float
    bytes: int


@dataclass
class SaveResult:
    doc: ConfigDoc
    #: The saved version this write displaced, when there was one. Named rather
    #: than implied because `.history/` can also hold snapshots that arrived with
    #: the image, so "restore the backup" has to say WHICH.
    snapshot: str | None = None


# ── paths ────────────────────────────────────────────────────────────


def _path_for(settings: Paths, key: str) -> Path:
    if not SAFE_KEY.match(key) or key not in EDITABLE:
        raise ConfigError(f"{key!r} is not an editable document")
    meta = EDITABLE[key]

    # The one document whose loader does NOT take its directory from settings:
    # `get_ontology()` reads a module constant derived from the source tree, so
    # resolving the write target from the same constant makes it impossible to
    # save where the loader will not read.
    #
    # Through the MODULE, never `from venona.ontology import ONTOLOGY_PATH`: that
    # import binds by value, so `tests/test_config_docs.py::ontology_file`
    # redirecting the constant moved the reader and left this writer on the real
    # repo file. The offline suite then saved its fixtures over
    # `config/ontology.yaml`, destroying the load-bearing `org_type` comments on
    # the round trip. A test must not be able to write to the working tree.
    if meta["dir"] == "ontology":
        ontology_path = ontology_module.ontology_path()
        base, path = ontology_path.parent.resolve(), ontology_path.resolve()
    else:
        root = settings.profile_dir if meta["dir"] == "profile" else settings.config_dir
        base = root.resolve()
        path = (base / meta["file"]).resolve()

    # A name allowlist AND containment, not one or the other. `is_relative_to`
    # rather than a string prefix: `startswith` has no separator, so a check
    # against `/repo/config` also passes `/repo/config-backup/x`.
    if not path.is_relative_to(base):
        raise ConfigError(f"{key!r} resolves outside {base}")
    return path


def doc_paths(settings: Settings) -> list[Path]:
    """Every editable document's on-disk path, in `EDITABLE` order.

    `freshness.config_stamp` stats these. Going through `_path_for` keeps the
    ontology's module-resolved location and the containment check as the one
    source of truth.
    """
    return [_path_for(settings, key) for key in EDITABLE]


def _version(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ── read ─────────────────────────────────────────────────────────────


def read_doc(settings: Paths, key: str) -> ConfigDoc:
    path = _path_for(settings, key)
    meta = EDITABLE[key]
    content = path.read_text(encoding=ENCODING) if path.exists() else ""
    return ConfigDoc(
        key=key,
        title=meta["title"],
        description=meta["description"],
        group=meta["group"],
        format=meta["format"],
        rescore_hint=meta["rescore_hint"],
        content=content,
        version=_version(content),
        updated_at=path.stat().st_mtime if path.exists() else 0.0,
        bytes=len(content.encode()),
    )


def list_docs(settings: Settings) -> list[dict[str, Any]]:
    """Metadata only -- the editor fetches content per document."""
    out = []
    for key in EDITABLE:
        doc = read_doc(settings, key)
        out.append(
            {
                "key": doc.key,
                "title": doc.title,
                "description": doc.description,
                "group": doc.group,
                "format": doc.format,
                "rescore_hint": doc.rescore_hint,
                "version": doc.version,
                "bytes": doc.bytes,
                "updated_at": doc.updated_at,
                "readonly": False,
            }
        )
    return out


# ── validation ───────────────────────────────────────────────────────


def _parsed(key: str, content: str) -> Any:
    try:
        return yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc


def _validate_scoring_rubric(parsed: Any, settings: Settings) -> None:
    """The important one: weights that do not sum to 1.0 put `overall` on a
    scale that is not 0-100, and every future score would be quietly wrong."""
    axes = (parsed or {}).get("axes") or []
    if not axes:
        raise ConfigError("the rubric must declare at least one axis")

    ids = [a.get("id") for a in axes]
    if not all(ids):
        raise ConfigError("every axis needs an id")
    if len(set(ids)) != len(ids):
        raise ConfigError("duplicate axis ids")

    total = sum(float(a.get("weight", 0)) for a in axes)
    if abs(total - 1.0) > 0.001:
        raise ConfigError(
            f"weights sum to {total:.3f}, must be 1.0 -- otherwise scores are "
            f"no longer on a 0-100 scale"
        )


def _validate_lexicon(parsed: Any, settings: Settings) -> None:
    if parsed is not None and not isinstance(parsed, dict):
        raise ConfigError("lexicon must be a mapping")


def _validate_brief_sections(parsed: Any, settings: Settings) -> None:
    from venona.llm.schemas import RubricError, parse_brief_sections

    try:
        parse_brief_sections(parsed, origin="brief_sections.yaml")
    except RubricError as exc:
        raise ConfigError(str(exc)) from exc


def _validate_sources(parsed: Any, settings: Settings) -> None:
    from venona.sources.registry import SourceConfigError, parse_sources

    try:
        parse_sources(parsed, origin="sources.yaml")
    except SourceConfigError as exc:
        raise ConfigError(str(exc)) from exc


def _validate_anchors(parsed: Any, settings: Settings) -> None:
    from venona.services.seed import validate_anchors

    try:
        validate_anchors(parsed, origin="anchors.yaml")
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _predicate_contract(ontology: Ontology) -> dict[str, tuple[Any, ...]]:
    """The part of a predicate that other code depends on, as opposed to prose.

    Domain, range and symmetry change what may be asserted; a description does
    not. Only the former earns a forced version bump.
    """
    return {
        name: (spec.domain, spec.range, spec.symmetric)
        for name, spec in ontology.predicates.items()
    }


def _type_contract(ontology: Ontology) -> dict[str, tuple[Any, ...]]:
    """The part of an entity type other code depends on: its existence, its id
    namespace, and its colour slot. Guidance prose changes freely."""
    return {name: (spec.prefix, spec.color_slot) for name, spec in ontology.entity_types.items()}


def _validate_ontology(parsed: Any, settings: Settings) -> None:
    from venona.ontology import OntologyError, parse_ontology

    try:
        new = parse_ontology(parsed)
    except OntologyError as exc:
        raise ConfigError(str(exc)) from exc

    current = get_ontology()

    # The type set is editable (ADR 0139, C10); a prefix rename on a SURVIVING
    # type is not. Ids are minted once and never recomputed (C4), while a
    # removed-then-re-added name may take a new prefix -- it is a new namespace.
    for name, spec in new.entity_types.items():
        if name not in current.entity_types:
            continue
        was = current.entity_types[name].prefix
        if spec.prefix != was:
            raise ConfigError(
                f"{name} prefix {was!r} -> {spec.prefix!r}: an id prefix is the entity "
                f"id namespace (C4) and ids are minted once, never recomputed. Every "
                f"stored id, URL and fixture uses the old one."
            )

    missing_anchors = [p for p in ANCHOR_PREDICATES if p not in new.predicate_names]
    if missing_anchors:
        raise ConfigError(
            f"relationship(s) {missing_anchors} are required for anchoring; "
            f"{list(ANCHOR_PREDICATES)} are built into the anchor answer format, "
            f"which is what constrains `venona anchors propose`. Removing one breaks "
            f"anchoring with nothing to catch it."
        )

    # `rewrites` is part of the contract for the same reason domain/range are:
    # it changes what `analyze` stores, so an `extraction_rewritten` assertion
    # must be attributable to the version whose table was in force.
    contract_changed = (
        _predicate_contract(new) != _predicate_contract(current)
        or new.rewrites != current.rewrites
        or _type_contract(new) != _type_contract(current)
    )
    if contract_changed and new.version == current.version:
        raise ConfigError(
            f"the contract changed (predicates, rewrites, or the entity type set) but "
            f"`version` is still {current.version}. Bump it and add a `changelog` entry "
            f"(C10) -- the version is how a stored assertion is attributable to the "
            f"rules in force when it was made."
        )


#: A model name's SHAPE, not its identity. One token, no whitespace, the
#: characters a model id uses.
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}$")


def _check_model_names(new: Settings) -> None:
    """Every `llm.*_model` is a plausible model name.

    A SHAPE CHECK AND DELIBERATELY NOT AN ALLOWLIST: a list of known names would
    track a vendor's release calendar and fail in the worst direction when it
    lags, refusing the model the operator is adopting on the day they adopt it.

    What is left is a typo, worth catching for how it fails. The value reaches
    the CLI as the paired argument of `--model` in an argv list, so it cannot
    inject a flag -- it just makes every call in the next run fail, one at a
    time, on the model's meter.
    """
    for name in sorted(f for f in type(new.llm).model_fields if f.endswith("_model")):
        value = getattr(new.llm, name)
        if not isinstance(value, str) or not MODEL_NAME.fullmatch(value):
            raise ConfigError(
                f"llm.{name} is {value!r}, which is not a model name. Expected one "
                f"word like 'sonnet' or 'claude-opus-5' -- no spaces."
            )


def settings_load_checks(parsed: Any) -> Settings:
    """Whether this document LOADS, asked through the real models.

    Split out so a check added for the config rail cannot go missing from
    `apply_settings_migration`, which needs these and only these -- not the
    separate question of who is allowed to change what.
    """
    # A top-level YAML SEQUENCE would hand `unknown_top_level_keys` dicts and
    # raise `TypeError: unhashable type` -- a 500 rather than a 400 -- and a bare
    # scalar would report its characters as unknown keys.
    if parsed is not None and not isinstance(parsed, dict):
        raise ConfigError(
            f"settings.yaml must be a mapping of top-level keys, not a {type(parsed).__name__}."
        )

    # Every value here is optional with a default, so a misspelled key is not a
    # missing field -- it is a field that stays at its default while the save
    # reports success, the fourth promise inverted.
    #
    # The nested groups enforce themselves: `extra="forbid"` makes
    # `Settings(**parsed)` below raise for `pipeline.analyze_limt` and name it.
    # Only the top level needs its own check, for the reason
    # `unknown_top_level_keys` documents.
    unknown = unknown_top_level_keys(parsed)
    if unknown:
        raise ConfigError(
            f"unknown top-level key(s) {unknown} -- nothing reads them, so this save "
            f"would report success and change nothing. Note that the three database "
            f"keys are spelled in CAPITALS here (NEO4J_URI, not neo4j_uri)."
        )

    try:
        new = Settings(**(parsed or {}))
    except Exception as exc:  # pydantic ValidationError, or a bad type
        raise ConfigError(f"invalid settings: {exc}") from exc

    _check_model_names(new)

    # `ScheduleSettings` types these as bare strings, so `Settings(**parsed)`
    # accepts a cron that nothing can fire -- and `apply_schedule` would be
    # handed the bad string AFTER the file was written.
    from apscheduler.triggers.cron import CronTrigger

    try:
        CronTrigger.from_crontab(new.schedule.cron, timezone=new.schedule.timezone)
    except Exception as exc:  # noqa: BLE001 - bad cron, or an unknown timezone
        raise ConfigError(
            f"invalid schedule: {exc}. `schedule.cron` takes five crontab fields "
            f'(e.g. "0 6 * * *") and `schedule.timezone` an IANA name.'
        ) from exc

    present = [f for f in PATH_FIELDS if isinstance(parsed, dict) and f in parsed]
    if present:
        raise ConfigError(
            f"path field(s) {present} cannot be set here at all. This file was "
            f"FOUND at a path built from them, so a copy inside it could only "
            f"disagree with where it was just read from. Set VENONA_CONFIG_DIR "
            f"and the rest in the environment."
        )

    return new


def _validate_settings(parsed: Any, settings: Settings) -> None:
    # The file ON DISK, needed only to diff the frozen and path fields, and it
    # must not be able to REFUSE the save -- see `_settings_from_raw_tolerant`.
    old = _settings_from_raw_tolerant(_current_raw(settings, "settings"))

    new = settings_load_checks(parsed)

    # Only a RETYPED marker is refused, not a missing one: the series is
    # idempotent, so a save that drops it replays at the next start and stamps
    # head again, where a refusal would only block an author who never touched it.
    retyped = [
        f
        for f in DEPLOY_OWNED_TOP
        if isinstance(parsed, dict) and f in parsed and getattr(new, f) != getattr(old, f)
    ]
    if retyped:
        raise ConfigError(
            f"{retyped} records which configuration updates this instance "
            f"has already received. Venona writes it; changing it here would "
            f"make the next start either repeat work or skip it."
        )

    frozen = [f for f in FROZEN_TOP if getattr(new, f) != getattr(old, f)]
    frozen += [
        f"{group}.{field}"
        for group, field in FROZEN_FIELDS
        if getattr(getattr(new, group), field) != getattr(getattr(old, group), field)
    ]
    if frozen:
        raise ConfigError(
            f"field(s) {frozen} cannot be changed here -- they decide which binary "
            f"the server executes and which database it authenticates against, and a "
            f"config save is not the right amount of ceremony for either. Edit "
            f"config/settings.yaml or .env directly, on the host, and restart."
        )


def _validate_domain(parsed: Any, settings: Settings) -> None:
    from venona import domain as domain_module

    try:
        descriptor = domain_module.parse_domain(parsed)
    except domain_module.DomainError as exc:
        raise ConfigError(str(exc)) from exc

    # The slug is the deployment's identity -- the hub directory, run
    # attribution and the wizard's bookkeeping all key on it -- so it freezes
    # the way an ontology prefix does: after the first write, only by editing
    # the file on the host.
    path = settings.config_dir / "domain.yaml"
    if path.exists():
        from venona.services.setup_service import PLACEHOLDER_SLUG

        current = domain_module.parse_domain(yaml.safe_load(path.read_text()))
        if current.slug != PLACEHOLDER_SLUG and descriptor.slug != current.slug:
            raise ConfigError(
                f"slug cannot change over HTTP (is {current.slug!r}, save says "
                f"{descriptor.slug!r}) -- it is this deployment's identity. If you "
                f"really mean it, edit config/domain.yaml on the host."
            )


# The four documents spliced VERBATIM into a system prompt: pasted whole under a
# heading `llm/prompts.py` writes, so their bytes are the model's instructions
# (ADR 0166). `MAX_BYTES`, at 256KB, is a cap on a FILE -- it would admit a
# relevance profile sixteen times the length of every other instruction in the
# prompt combined.
#
# Sized from the live documents with room to grow; measured 2026-09-14, the
# largest is research_prompt at 6KB. A save that needs more than this is a
# different document and should be visibly refused.
PROMPT_DOC_MAX_BYTES = 32 * 1024

PROMPT_DOCS = ("relevance_profile", "research_prompt", "brief_prompt", "learning_prompt")


def _validate_prompt_doc(content: str, _settings: Settings) -> None:
    """The four verbatim-spliced documents. There is no schema to check -- prose
    is the point -- so this checks the two things that are not prose."""
    if len(content.encode(ENCODING)) > PROMPT_DOC_MAX_BYTES:
        raise ConfigError(
            f"this document is {len(content.encode(ENCODING)) // 1024}KB; the limit is "
            f"{PROMPT_DOC_MAX_BYTES // 1024}KB. It is pasted whole into the "
            f"instructions Venona sends, so its length is the instructions' length."
        )
    # Not about defending against the author -- a prompt document is trusted --
    # but a stray delimiter here would close the envelope around a document
    # further down the same prompt, turning scraped article text into something
    # the model reads as venona speaking (ADR 0166).
    if untrust.DELIMITER.search(content):
        raise ConfigError(
            "this document contains an <untrusted> tag. Venona wraps quoted "
            "article text in those, so one here would change how a later "
            "quote is read. Remove it."
        )


VALIDATORS = {
    "scoring_rubric": _validate_scoring_rubric,
    "domain": _validate_domain,
    "lexicon": _validate_lexicon,
    "brief_sections": _validate_brief_sections,
    "sources": _validate_sources,
    "anchors": _validate_anchors,
    "ontology": _validate_ontology,
    "settings": _validate_settings,
}


def validate(settings: Settings, key: str, content: str) -> None:
    """Reject a save that would break the next run."""
    if len(content.encode()) > MAX_BYTES:
        raise ConfigError(f"document exceeds {MAX_BYTES // 1024}KB")

    # On the TEXT, before the parse: these four are markdown, so `VALIDATORS` --
    # keyed on a parsed structure -- has nothing to hand them.
    if key in PROMPT_DOCS:
        _validate_prompt_doc(content, settings)

    validator = VALIDATORS.get(key)
    if validator is None:
        return
    validator(_parsed(key, content), settings)


# ── settings helpers ─────────────────────────────────────────────────


def _current_raw(settings: Settings, key: str) -> dict[str, Any]:
    path = _path_for(settings, key)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding=ENCODING)) or {}


def _settings_from_raw(raw: dict[str, Any]) -> Settings:
    return Settings(**(raw or {}))


def _settings_from_raw_tolerant(raw: Any) -> Settings:
    """The file on disk, with anything unloadable dropped rather than raised.

    Used ONLY to build the `old` side of the frozen-field diff. The groups are
    `extra="forbid"`, so a stray key already in the file makes `Settings(**raw)`
    raise: unguarded, every save of this document 500'd, including the save that
    would have removed the key.

    Derived from the FILE rather than the live settings because `PATH_FIELDS` is
    compared here too, and a process on a non-default `config_dir` would
    otherwise refuse every save as a path change.
    """
    if not isinstance(raw, dict):
        return Settings()

    known_groups = {"llm": LlmSettings, "schedule": ScheduleSettings, "pipeline": PipelineSettings}
    accepted = {f.alias or n for n, f in Settings.model_fields.items()}
    clean: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in accepted:
            continue
        model = known_groups.get(key)
        if model is not None and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k in model.model_fields}
        clean[key] = value

    loaded = _loadable(clean)
    if loaded is not None:
        return loaded

    # A bad VALUE, not just an unknown key. Collapsing to `Settings()` here would
    # be this function's own trap one level down: every override in the file --
    # including the frozen ones the guard below compares -- would read as changed,
    # and the save removing the bad value would be refused as an attempt to move
    # the database or the binary. Per FIELD inside a group, not per group,
    # because one group holds both frozen and free fields and a typo in either
    # must not take the other's real value down with it.
    kept: dict[str, Any] = {}
    for key, value in clean.items():
        if _loadable({**kept, key: value}) is not None:
            kept[key] = value
        elif isinstance(value, dict):
            group: dict[str, Any] = {}
            for name, item in value.items():
                if _loadable({**kept, key: {**group, name: item}}) is not None:
                    group[name] = item
            if group:
                kept[key] = group
    return _loadable(kept) or Settings()


def _loadable(raw: dict[str, Any]) -> Settings | None:
    try:
        return Settings(**raw)
    except Exception:  # noqa: BLE001 - any rejection means "not this one"
        return None


def _dig(raw: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = raw
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


# ── write ────────────────────────────────────────────────────────────

# One lock per document key, and the guard that hands them out. A plain module
# dict races on first use: two threads saving the same key for the first time
# both miss and both create a lock, which is two locks and no serialisation.
_KEY_LOCKS: dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        return _KEY_LOCKS.setdefault(key, threading.Lock())


def _lock_path(path: Path) -> Path:
    """Where the cross-process lock for `path` lives.

    Not beside the document: `profile/` is checked into the repository, and a
    lock file appearing in `git status` is noise the user has to learn to ignore.
    Keyed by a digest of the absolute path so two instances on one machine take
    different locks.
    """
    digest = hashlib.sha256(str(path.resolve()).encode(ENCODING)).hexdigest()[:16]
    # KEYED BY UID AS WELL AS BY PATH. A lock file in the shared temp directory
    # is created 0644 by whoever gets there first, and a second user can then
    # never open it for writing -- which on 2026-09-15 was a 500 on every
    # attempt to change the model service, from `PermissionError: [Errno 13]`
    # on `/tmp/venona-config-<digest>.lock`. The entrypoint already runs
    # `config materialize` as the app's user to avoid exactly that, and this is
    # the belt to its braces: two users now take two files rather than
    # contending for one they cannot share.
    return Path(tempfile.gettempdir()) / f"venona-config-{digest}-{os.getuid()}.lock"


@contextlib.contextmanager
def _exclusive(path: Path, key: str) -> Iterator[None]:
    """Serialise one document's whole read-compare-snapshot-replace.

    `os.replace` makes the REPLACE atomic and nothing makes the sequence atomic.
    Two saves that read the same bytes both pass the `base_version` check, then
    both snapshot and both write: the second overwrites the first, and the losing
    version can be absent from the history that exists to make every edit
    reversible. A double-submit is enough.

    Two locks because there are two ways in. `threading.Lock` covers the config
    routes, which are sync `def` and so run two at a time on Starlette's
    threadpool; `flock` covers a second process reaching the same file.
    """
    with _key_lock(key):
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Linux and macOS both have it
            yield
            return
        # A LOCK WE CANNOT TAKE MUST NOT END THE SAVE. `_key_lock` above is
        # already held, so the config routes -- which are sync `def` and run on
        # Starlette's threadpool -- stay serialised against each other; what is
        # lost is exclusion against a SECOND PROCESS, which is the rarer half
        # and is not worth the whole feature.
        #
        # Raising here made the temp directory a single point of failure for
        # every edit in the app: one unwritable lock file and the operator
        # could not change the model service, a source, a prompt or the
        # relevance profile again, with a 500 and a reference id that named
        # nothing they could act on.
        try:
            handle = open(_lock_path(path), "w", encoding=ENCODING)  # noqa: SIM115
        except OSError as exc:
            log.warning(
                "config lock unavailable (%s); saving %s with in-process "
                "locking only, so a second process could race this write",
                exc,
                key,
            )
            yield
            return
        with handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass(frozen=True)
class WriteAudit:
    """Who is writing, plus the identity and clock to record it with.

    ONE PARAMETER RATHER THAN FOUR, because the four are meaningless apart:
    passing them as a unit makes "audited" a single decision per call site
    instead of four that can half-arrive. `event_id` and `now` come from the
    caller because `services/` may not ask the time or mint an id -- the same
    arrangement as `api/routers/learning.py`'s "Time enters HERE".

    `actor_id=None` is expected: the CLI, the setup wizard and the hub's
    provisioning call all write config with no person behind them.
    """

    conn: Any
    actor_id: str | None
    event_id: str
    now: str


#: The store this process was bound to, set by `materialize()` alone. A module
#: value for the reason `harness_service._SECRETS` is one: threading a connection
#: through every caller of the write path is ceremony.
_STORE_DSN: str = ""


@contextlib.contextmanager
def _store(settings: Paths, conn: Any = None) -> Iterator[Any]:
    """A connection to the document store, borrowed or opened (ADR 0234).

    THE OPTIONALITY IS THE TEST SEAM, as in `api/deps.py::_borrow`. The fallback
    DSN is one `materialize()` bound, never one derived from `settings`, whose
    `database_url` has a DEFAULT -- deriving it would put rows from every
    filesystem-only config test into the developer's database.
    """
    if conn is not None:
        yield conn
        return
    if not _STORE_DSN:
        yield None
        return
    from venona.db import core as db

    owned = db.connect(_STORE_DSN)
    try:
        yield owned
    finally:
        owned.close()


def write_doc(
    settings: Settings,
    key: str,
    content: str,
    *,
    base_version: str | None = None,
    audit: WriteAudit | None = None,
    conn: Any = None,
) -> SaveResult:
    path = _path_for(settings, key)
    validate(settings, key, content)
    with _exclusive(path, key):
        return _write_doc(
            settings,
            key,
            content,
            path=path,
            base_version=base_version,
            audit=audit,
            conn=conn,
        )


def _write_doc(
    settings: Settings,
    key: str,
    content: str,
    *,
    path: Path,
    base_version: str | None,
    audit: WriteAudit | None = None,
    conn: Any = None,
) -> SaveResult:
    """The write itself. Callers reach it through `write_doc`, which holds the
    lock the whole sequence needs -- see `_exclusive`.

    THE ROW IS THE COMMIT POINT AND THE FILE IS THE PROJECTION (ADR 0234): row
    first, file second. The other order lets a database failure leave the file
    ahead of the row, and the next boot would silently revert an edit the
    operator watched succeed.
    """
    current = path.read_text(encoding=ENCODING) if path.exists() else ""
    if base_version and _version(current) != base_version:
        raise ConfigError("CONFLICT")

    # A save that changes nothing must not touch the file or the ring: an
    # identical PUT would move the mtime, write a `.bak` and spend one of
    # `_prune_history`'s 20. A client bug must not cost the user history.
    #
    # It does NOT fix the incident that prompted it -- 17 saves each appending
    # one newline, which are genuinely different content and pass this guard.
    # That is the editor's problem, not the writer's.
    #
    # Before the snapshot, deliberately: after it, the slot is already spent.
    if content == current:
        return SaveResult(doc=read_doc(settings, key))

    # The content version is in the filename as well as the mtime. mtime has
    # one-second resolution, so saving, spotting a typo and saving again inside
    # the same second stamped both the same and the second snapshot overwrote
    # the first -- losing exactly the version you wanted back, and showing one
    # `history()` entry where two edits happened.
    snapshot: str | None = None
    stamp = str(int(path.stat().st_mtime)) if path.exists() else "0"
    if current:
        snapshot = f"{key}.{stamp}.{_version(current)}.bak"

    # THE ROW, and the snapshot beside it in one transaction -- see the docstring
    # for why this precedes the file. EITHER ROWS OR `.history/`, never both:
    # writing both double-counts against the keep-20 and lets the two rings
    # disagree about which version is oldest.
    with _store(settings, conn) as store:
        if store is None:
            if current:
                history_dir = path.parent / ".history"
                history_dir.mkdir(exist_ok=True)
                (history_dir / snapshot).write_text(current, encoding=ENCODING)
                _prune_history(history_dir, key)
        else:
            from venona.db import core as db
            from venona.db.repositories import config_documents as docs_repo

            with db.transaction(store):
                if current:
                    docs_repo.snapshot(
                        store, key=key, content=current, version=_version(current), stamp=stamp
                    )
                    docs_repo.prune(store, key=key)
                docs_repo.upsert(
                    store,
                    key=key,
                    content=content,
                    version=_version(content),
                    # TIME ENTERS AT THE CALLER -- `services/` is guarded and the
                    # audit carries a reading. Empty means "no mtime to restore".
                    now=(audit.now if audit is not None else ""),
                )

    # Captured BEFORE the replace and reapplied after: `NamedTemporaryFile`
    # creates at 0600 and `os.replace` carries the temp file's mode onto the
    # destination. Every save silently made the document owner-only --
    # `relevance_profile.md` and `scoring_rubric.yaml` sat at 0600 while every
    # file this had never touched was 0644.
    mode = path.stat().st_mode & 0o777 if path.exists() else None

    # Atomic: a crash mid-write must not leave a truncated config file. The temp
    # file is unlinked on any failure, because `delete=False` -- what makes the
    # replace possible -- also leaves a `tmp…` file in the user's own `config/`
    # or `profile/` directory, where it looks like something they should keep.
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding=ENCODING
    ) as handle:
        temp = Path(handle.name)
        try:
            handle.write(content)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise

    # The filesystem-side record that a user-authored file was rewritten, so
    # "what saved my relevance profile seventeen times?" is one `grep`.
    log.info(
        "config save: %s %s -> %s bytes (version %s)",
        key,
        len(current.encode(ENCODING)),
        len(content.encode(ENCODING)),
        _version(content),
    )

    # WHO did it, which the log line above cannot say. Editing
    # `relevance_profile.md` changes what every reader's brief is written from,
    # and `runs.profile_hash` records which profile each run executed under.
    #
    # AFTER the replace, and allowed to raise: writing the row first would record
    # a version a failing filesystem write never produced, and a database failure
    # here is loud, which is the right failure mode for an audit that did not
    # happen.
    if audit is not None:
        from venona.db.repositories import config_events

        config_events.record_write(
            audit.conn,
            event_id=audit.event_id,
            key=key,
            actor_id=audit.actor_id,
            version=_version(content),
            prev=_version(current) if current else None,
            size_bytes=len(content.encode(ENCODING)),
            now=audit.now,
        )

    _invalidate(key)
    return SaveResult(doc=read_doc(settings, key), snapshot=snapshot)


def apply_settings_migration(paths: Paths, content: str) -> SaveResult:
    """The deploy's own write of `settings.yaml`, for `config_migrations`.

    Not `write_doc`, and the difference is the point. A numbered migration,
    shipped in the image and read in a pull request, is already more ceremony
    than the "edit the file on the host and restart" that `_validate_settings`
    points its refusal at -- and it is the only thing that can carry the version
    marker forward at all.

    So it keeps every check that asks whether the document is LOADABLE and drops
    only the two that ask who is allowed to change what, and still writes through
    `_write_doc` for the snapshot, the mode, the atomic replace and the cache
    invalidation.
    """
    path = _path_for(paths, "settings")
    settings_load_checks(yaml.safe_load(content))
    with _exclusive(path, "settings"):
        return _write_doc(paths, "settings", content, path=path, base_version=None)


def add_to_stoplist(settings: Settings, surface: str) -> bool:
    """Add one surface to `profile/lexicon.yaml`'s `stoplist:` block.

    Returns True if the file changed, False if the surface was already there.

    **Text insertion, not a YAML round-trip.** `lexicon.yaml` is a document a
    person wrote and annotated: the stoplist block carries the reasoning for
    every entry in it. A `safe_load`/`safe_dump` pass would write back a file
    with every comment deleted and every block reordered -- a total loss of the
    user's own writing, in a file the app told them was theirs to edit. So this
    inserts one line and never touches the other bytes.

    It takes the lock around the READ as well, because the surface it inserts is
    decided by lines it read: two curators stoplisting at once would otherwise
    both insert into the same original file and the second write would drop the
    first term. `_exclusive` is not reentrant, so this calls `_write_doc` under
    the lock it already holds.
    """
    path = _path_for(settings, "lexicon")
    with _exclusive(path, "lexicon"):
        return _add_to_stoplist(settings, surface, path=path)


def _add_to_stoplist(settings: Settings, surface: str, *, path: Path) -> bool:
    doc = read_doc(settings, "lexicon")
    lines = doc.content.splitlines()
    token = surface.strip().upper()
    if not token:
        raise ConfigError("empty surface")
    # One YAML line, never more. `.strip()` leaves an interior newline intact, and
    # inserting `- FOO\nexpansions: {}` would rewrite unrelated structure in a file
    # the app promised to leave as the user wrote it.
    if not re.fullmatch(r"[A-Z0-9 ._/-]+", token):
        raise ConfigError(f"{surface!r} is not a valid ignore list entry")

    start = next((i for i, line in enumerate(lines) if re.match(r"^stoplist:\s*$", line)), None)
    if start is None:
        raise ConfigError("lexicon.yaml has no `stoplist:` block to hold the ignore list")

    # The block runs to the next top-level key. Comments and blank lines inside
    # it belong to it and are stepped over, not treated as the end.
    last_item = None
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line and not line[0].isspace() and not line.startswith("#"):
            break
        if re.match(r"^\s*-\s+", line):
            if line.split("-", 1)[1].strip().strip("\"'").upper() == token:
                return False  # already stoplisted; saying so beats a no-op save
            last_item = i
    if last_item is None:
        raise ConfigError(
            "the ignore list (the `stoplist:` block) is empty; add the first entry by hand"
        )

    lines.insert(last_item + 1, f"  - {token}")
    content = "\n".join(lines) + ("\n" if doc.content.endswith("\n") else "")
    validate(settings, "lexicon", content)
    _write_doc(settings, "lexicon", content, path=path, base_version=None)
    return True


def _invalidate(key: str) -> None:
    """Drop the caches that would otherwise serve the previous version.

    Only four of these documents are cached at all; the rest are read fresh by
    their loaders and need nothing.
    """
    if key == "scoring_rubric":
        # Drives runtime schema generation; a stale cache scores the next run
        # against superseded axes.
        from venona.llm.schemas import load_rubric

        load_rubric.cache_clear()

    if key == "ontology":
        get_ontology.cache_clear()

    if key == "domain":
        from venona.domain import get_domain

        get_domain.cache_clear()

    if key == "settings":
        get_settings.cache_clear()


def _snapshot_stamp(path: Path) -> int:
    """When the version INSIDE a snapshot was written, from its filename.

    Not when the snapshot was taken -- the stamp is the mtime of the content
    being copied, so a `.bak` cut at 20:13 holding the version saved at 19:40 is
    stamped 19:40. That is what a history list wants, but the two readings differ
    by one save, so say which one this is.

    The stamp is the second field of `{key}.{stamp}.{version}.bak`, and no
    `EDITABLE` key contains a dot. An unparseable name sorts oldest rather than
    raising: one stray file in `.history/` must not 500 a listing.
    """
    parts = path.stem.split(".")
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0


def _snapshot_order(path: Path) -> tuple[int, str]:
    """A TOTAL order over snapshots: stamp first, then the name.

    The stamp alone is not one -- two saves inside a second share it, and real
    `.history/` directories hold such pairs. `sorted` would then leave them in
    `scandir` order, which decides which of the two the prune deletes and which
    order the reader is offered them in, differently on every machine.
    """
    return (_snapshot_stamp(path), path.name)


def _prune_history(history_dir: Path, key: str, keep: int = 20) -> None:
    snapshots = sorted(history_dir.glob(f"{key}.*.bak"), key=_snapshot_order)
    for old in snapshots[:-keep]:
        old.unlink(missing_ok=True)


def _snapshot_version(path: Path) -> str | None:
    """The content hash inside a snapshot's name, or None when it has none.

    `.history/` also holds three-field names carrying no version. Those are
    readable, so the field is optional rather than a reason to hide them.
    """
    parts = path.stem.split(".")
    return parts[2] if len(parts) > 2 else None


#: How many saved versions one read returns. The editor shows a list somebody
#: scrolls, not an archive; pruning keeps the table near this anyway.
HISTORY_LIMIT = 100


def history(settings: Settings, key: str, conn: Any = None) -> list[dict[str, Any]]:
    """Saved versions of one document, newest first.

    ROWS WHEN THERE IS A STORE, files otherwise (ADR 0234). `file` keeps its
    `{key}.{stamp}.{version}.bak` spelling because the editor sends it back to
    `snapshot()`; it names a row.
    """
    with _store(settings, conn) as store:
        if store is not None:
            from venona.db.repositories import config_documents as docs_repo

            return [
                {
                    "file": f"{key}.{row['stamp']}.{row['version']}.bak",
                    "timestamp": int(row["stamp"]) if str(row["stamp"]).isdigit() else 0,
                    "version": row["version"],
                    "bytes": row["bytes"],
                }
                for row in docs_repo.history(store, key=key, limit=HISTORY_LIMIT)
            ]

    directory = _path_for(settings, key).parent / ".history"
    if not directory.exists():
        return []
    return [
        {
            "file": p.name,
            "timestamp": _snapshot_stamp(p),
            "version": _snapshot_version(p),
            "bytes": p.stat().st_size,
        }
        for p in sorted(directory.glob(f"{key}.*.bak"), key=_snapshot_order, reverse=True)
    ]


def snapshot(settings: Settings, key: str, name: str, conn: Any = None) -> dict[str, Any]:
    r"""One saved version, with its content.

    THE NAME IS VALIDATED BY MEMBERSHIP IN `history()`, not by a regex over its
    shape. A regex has to re-derive what `_write_doc` builds, and the first
    attempt wrote `\d+` for a field that is hex, so it matched no snapshot on
    disk at all. Membership cannot drift from the writer, and it inherits the
    glob, the sort and the directory resolution; the containment check stays as
    the second belt. The store branch reads the same `history()` for the same
    reason.
    """
    listed = {row["file"]: row for row in history(settings, key, conn)}
    row = listed.get(name)
    if row is None:
        raise ConfigError(f"no saved version {name!r} of {key!r}")

    with _store(settings, conn) as store:
        if store is not None:
            from venona.db.repositories import config_documents as docs_repo

            content = docs_repo.snapshot_content(
                store, key=key, version=row["version"], stamp=str(row["timestamp"])
            )
            # Listed a moment ago and gone now: a concurrent save pruned it.
            if content is None:
                raise ConfigError(f"saved version {name!r} of {key!r} could not be read")
            return {**row, "content": content}

    directory = _path_for(settings, key).parent / ".history"
    path = (directory / name).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ConfigError(f"no saved version {name!r} of {key!r}")

    # A snapshot `history()` just listed can still be unreadable -- pruned by a
    # concurrent save, or not decodable. Both are "this version is not
    # available", a 404 on the route; letting the OSError through makes it a 500.
    try:
        content = path.read_text(encoding=ENCODING)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"saved version {name!r} of {key!r} could not be read") from exc

    return {**row, "content": content}


# ── the projection ───────────────────────────────────────────────────


def materialize(conn: Any, settings: Paths, *, defaults: Path | None = None) -> list[str]:
    """Write every editable document out of its row, and bind the store.

    ADR 0234. A row wins, and its `updated_at` restores the file's mtime or every
    deploy looks like every document just changed; failing that a file already
    there is adopted untouched; failing that the image defaults are seeded.

    `conn` rather than a DSN because the caller has just migrated with it.
    """
    from venona.db import core as db
    from venona.db.repositories import config_documents as docs_repo

    global _STORE_DSN
    _STORE_DSN = getattr(conn, "dsn", "") or _STORE_DSN

    root = defaults if defaults is not None else Paths().repo_root / "defaults"
    stored = {row["key"]: row for row in docs_repo.all_docs(conn)}
    written: list[str] = []

    for key, meta in EDITABLE.items():
        path = _path_for(settings, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = stored.get(key)

        if row is None:
            content = path.read_text(encoding=ENCODING) if path.exists() else ""
            if not content:
                seed = root / ("profile" if meta["dir"] == "profile" else "config") / meta["file"]
                content = seed.read_text(encoding=ENCODING) if seed.exists() else ""
            if not content:
                continue
            # THE FILE'S OWN MTIME -- a stat, not a clock, so it is legal in a
            # guarded package. An empty `updated_at` reads as never touched.
            with db.transaction(conn):
                docs_repo.upsert(
                    conn,
                    key=key,
                    content=content,
                    version=_version(content),
                    now=_mtime_iso(path),
                )
            _adopt_legacy_history(conn, settings, key)
            if not path.exists():
                path.write_text(content, encoding=ENCODING)
                written.append(key)
            continue

        # Already identical -- do not touch the file, so its mtime keeps saying
        # when a person last changed the document rather than when this ran.
        if path.exists() and path.read_text(encoding=ENCODING) == row["content"]:
            continue
        path.write_text(row["content"], encoding=ENCODING)
        _restore_mtime(path, row["updated_at"])
        written.append(key)

    _invalidate_all()
    return written


def _mtime_iso(path: Path) -> str:
    """A `stat` rather than a clock, which is what makes it legal here."""
    if not path.exists():
        return ""
    from datetime import UTC, datetime

    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _restore_mtime(path: Path, updated_at: str) -> None:
    """Silent on failure: a wrong mtime costs a cache miss, not a boot."""
    if not updated_at:
        return
    from datetime import datetime

    with contextlib.suppress(ValueError, OSError):
        stamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()
        os.utime(path, (stamp, stamp))


def _adopt_legacy_history(conn: Any, settings: Paths, key: str) -> None:
    """Carry an existing `.history/` into rows, once, while the disk is there.

    THIS IS WHY THE DISK GOES IN A SECOND DEPLOY: removing it while the rows are
    empty detaches the only copy. Only called on the no-row branch.
    """
    from venona.db import core as db
    from venona.db.repositories import config_documents as docs_repo

    directory = _path_for(settings, key).parent / ".history"
    if not directory.exists():
        return
    found = sorted(directory.glob(f"{key}.*.bak"), key=_snapshot_order)
    if not found:
        return
    with db.transaction(conn):
        for snap in found:
            version = _snapshot_version(snap)
            if not version:
                continue
            with contextlib.suppress(OSError, ValueError):
                docs_repo.snapshot(
                    conn,
                    key=key,
                    content=snap.read_text(encoding=ENCODING),
                    version=version,
                    stamp=str(_snapshot_stamp(snap)),
                )
        docs_repo.prune(conn, key=key)
    log.info("config materialize: adopted %d saved version(s) of %s", len(found), key)


def _invalidate_all() -> None:
    for key in EDITABLE:
        _invalidate(key)


# ── assembled system prompts (read-only) ─────────────────────────────
#
# What the model is ACTUALLY told, rendered from the same builders the pipeline
# calls. Nothing here is a copy of a prompt: a second copy would be wrong within
# a week, and the point of this surface is to be trustworthy.
#
# Deliberately not editable. What these hold beyond the documents one rail-group
# up is the hard-rules preamble, whose rules 1 and 2 ARE invariant 3 -- a
# textarea over that switches off the guarantee grounding rests on.

ASSEMBLED: dict[str, dict[str, Any]] = {
    "analyze": {
        "title": "Analyze",
        "description": "The standing instructions sent with every reading call.",
        "generated_from": [
            "the fixed rules (not editable here)",
            "domain",
            "relevance_profile",
            "scoring_rubric",
            "ontology",
            "the required answer format (generated)",
            "the list of known names (generated)",
        ],
    },
    "brief": {
        "title": "Brief",
        "description": "How the daily brief is written.",
        "generated_from": ["relevance_profile", "brief_prompt"],
    },
    "questions": {
        "title": "Questions",
        "description": "The standing instructions sent when generating study questions.",
        "generated_from": ["the grounding preamble (code)", "domain", "learning_prompt"],
    },
    "discovery": {
        "title": "Discovery",
        "description": "Open-ended sweeps for candidate URLs.",
        "generated_from": ["domain", "relevance_profile", "research_prompt"],
    },
    "acronym_search": {
        "title": "Acronym: search",
        "description": "Acronym search, step one: find pages that define it.",
        "generated_from": ["code", "domain"],
    },
    "acronym_read": {
        "title": "Acronym: read",
        "description": "Acronym search, step two: the expansion plus the verbatim sentence.",
        "generated_from": ["code", "domain"],
    },
    "adjudicate": {
        "title": "Adjudicate",
        "description": "Are these two names the same thing?",
        "generated_from": ["code", "domain"],
    },
    "anchor": {
        "title": "Anchor",
        "description": "Attach an unconnected name to the graph's core.",
        "generated_from": ["code", "domain", "ontology"],
    },
}


#: The group every assembled prompt belongs to. Named once: `assembled_list`
#: and `assembled_doc` both build a row, and a key that drifts in one of them
#: drops the whole read-only block off the index without failing anything.
ASSEMBLED_GROUP = "sent"


def assembled_list() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "title": meta["title"],
            "description": meta["description"],
            "group": ASSEMBLED_GROUP,
            "format": "markdown",
            "readonly": True,
        }
        for key, meta in ASSEMBLED.items()
    ]


def assembled_doc(key: str, conn: Any) -> dict[str, Any]:
    from venona.llm import prompts as p

    if key not in ASSEMBLED:
        raise ConfigError(f"{key!r} is not an assembled prompt")

    if key == "analyze":
        content = p.analyze_system_prompt(conn)
    elif key == "brief":
        content = p.brief_system_prompt()
    elif key == "discovery":
        content = p.discovery_system_prompt()
    elif key == "questions":
        content = p.questions_system()
    else:
        content = {
            "acronym_search": p.acronym_search_system(),
            "acronym_read": p.acronym_read_system(),
            "adjudicate": p.adjudicate_system(),
            "anchor": p.anchor_system(),
        }[key]

    meta = ASSEMBLED[key]
    return {
        "key": key,
        "title": meta["title"],
        "description": meta["description"],
        "group": ASSEMBLED_GROUP,
        "format": "markdown",
        "readonly": True,
        "generated_from": meta["generated_from"],
        "content": content,
        "version": _version(content),
        "bytes": len(content.encode()),
        # Rough, and labelled as such wherever it is shown. The real count comes
        # back on the envelope; this is for "is this prefix getting expensive?".
        "tokens_estimate": len(content) // 4,
    }
