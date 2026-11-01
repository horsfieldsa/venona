"""Configuration.

The `llm`, `schedule` and `pipeline` groups come from THIS INSTANCE's
`settings.yaml` and nowhere else -- see `settings_path()`. The environment
supplies only what cannot live in an editable document: where the files are,
the database and Neo4j, the auth secrets, and the `VENONA_OFFLINE` switch.

One answer per setting is the point. A knob that the file and the environment
could both claim is a knob the config rail can report as saved while something
outside the file quietly outranks it.

Secrets live only in .env, and ANTHROPIC_API_KEY is not among them: on a
subscription `main._refuse_metered_billing` refuses to boot with one (decision 1).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# `config/settings.yaml` is editable over HTTP -- the Settings rail writes it
# through `services/config_service.py`. Pydantic's default for a `BaseModel` is
# `extra="ignore"`, under which `analyze_limt: 5` validates, snapshots, writes
# and reports success while changing nothing (ADR 0081). Forbidding extras is
# what the separate group models buy; the top-level `Settings` cannot -- see
# `unknown_top_level_keys`.
_STRICT = ConfigDict(extra="forbid")


class LlmSettings(BaseModel):
    """Model tiers per stage. See docs/adr/ for the rationale."""

    model_config = _STRICT

    backend: str = "cli"  # cli | replay
    # WHICH SERVICE DOES THE THINKING. An enum, resolved through
    # `llm/harness/`'s table to a program NAME held in code -- which is what
    # makes it editable over HTTP while `cli_path` stays frozen. Nothing an
    # admin can type here reaches argv.
    harness: str = "claude_code"
    # Subscription or metered. The two differ in more than the bill: on a
    # subscription the binding constraint is a rate-limit window and `cost_usd`
    # is a shadow price (ADR 0180); under an API key there is no window, the
    # dollars are real, and `max_spend_usd_per_run` applies (ADR 0218).
    billing_mode: Literal["subscription", "api"] = "subscription"
    cli_path: str = "claude"
    pinned_version: str = "2.1.251"
    codex_cli_path: str = "codex"
    codex_pinned_version: str = "0.151.0"
    grok_cli_path: str = "grok"
    #: Empty, and that is not an oversight. The other two pins name a version
    #: somebody ran; nobody has run this one, so a number here would be a claim
    #: rather than a pin. `harness.matches_pin` reads an empty pin as NO pin, so
    #: this reports nothing rather than reporting every install as wrong -- which
    #: is what a bare `version == pinned` did with it.
    grok_pinned_version: str = ""
    # Only under `billing_mode: api`, and ignored on a subscription (ADR 0218).
    # `lookup_all` can spend tens of dollars from one press. Hitting it defers
    # the rest and ends the run `succeeded_partial`, exactly like the call cap --
    # never `failed`.
    max_spend_usd_per_run: float = 25.0
    # Whether a model process is locked to a filesystem before it runs.
    # `auto` confines the harnesses that ask for it wherever the kernel can,
    # and runs unconfined where it cannot rather than refusing to work; `off`
    # is the escape hatch for diagnosing a CLI that a restriction broke.
    # Frozen, because it is a safety property and the page that would edit it
    # is reached over HTTP.
    confine: Literal["auto", "off"] = "auto"
    concurrency: int = 2
    # Reserved headroom for the `light` lane (contract C11), as a SEPARATE
    # semaphore rather than a bigger shared one. See `run_launcher.executor_for`
    # for why: a lookup admitted instantly and then parked behind `analyze` is,
    # from the hover card, indistinguishable from one that was refused.
    light_concurrency: int = 2
    max_calls_per_run: int = 250
    timeout_seconds: int = 300
    # Per-TASK ceilings for the calls this default does not fit, keyed by the
    # same task name the ledger and `fixtures/llm/` use. Raising the global
    # instead lengthens every lane's calls, which is how a wedged subprocess
    # stops looking like one. A task absent here falls through to
    # `TASK_TIMEOUT_DEFAULTS` and then to `timeout_seconds`, so setting one
    # override cannot silently drop a shipped one.
    task_timeout_seconds: dict[str, int] = Field(default_factory=dict)
    # How long the research lane waits for the provider to come back once the
    # circuit has opened on a TRANSIENT error (5xx, 529, a timeout), measured
    # from the start of the outage. The light lane never waits; see
    # `CliExecutor._admit`.
    outage_wait_seconds: int = 1800
    # How many times a TRANSIENT failure is tried before the call gives up and
    # reports what it could not get. Four rather than three because the third
    # attempt was the one that decided a run's fate: with waits of a second and
    # two seconds, a call that met the same bad minute three times had exhausted
    # a policy that had not, in total, waited three seconds.
    retry_attempts: int = 4
    # The first wait, doubled per attempt and capped at a minute. At five
    # seconds the waits run 5 / 10 / 20 before jitter, which spans a provider's
    # bad minute; starting at one second spans three seconds and decides nothing.
    retry_base_seconds: float = 5.0

    discover_model: str = "sonnet"
    discover_effort: str = "high"
    analyze_model: str = "sonnet"
    analyze_effort: str = "medium"
    acronym_model: str = "sonnet"
    acronym_effort: str = "low"
    # Study-question generation (epic O): light lane, operator-triggered.
    questions_model: str = "sonnet"
    questions_effort: str = "medium"
    research_model: str = "sonnet"
    adjudicate_model: str = "sonnet"
    brief_model: str = "opus"
    brief_effort: str = "high"
    # A reader's own brief (personal relevance): one light-lane call per
    # reader per generation, over a window analyze already scored. Sonnet
    # rather than the shared brief's opus: the judgement lines it rewrites
    # arrive pre-scored and pre-evidenced, and the call multiplies by
    # readership.
    personal_brief_model: str = "sonnet"
    personal_brief_effort: str = "medium"
    # Spend rails for that multiplication: generations one reader may start
    # per day, and how recently a reader must have USED Venona for the post-run
    # warm pass to pre-generate theirs. Short, because a reader outside the
    # window still has theirs written when they next open the reading surface:
    # widening this buys a shorter wait for the occasional visitor and pays for
    # every reader who never returns. Zero disables the warm pass entirely.
    personal_briefs_per_day: int = 3
    personal_brief_warm_days: int = 3
    # Which shared briefs pull the readership's own briefs along with them.
    # `every_brief` is any run that delivers one, so an operator who runs an
    # extra one mid-afternoon does not leave every reader on the morning's
    # reading; `scheduled_only` restricts that to the daily, and `off` leaves
    # every reader's brief to be written when they next open the app. Per
    # instance, because what it costs depends entirely on how many readers an
    # instance has.
    personal_brief_fanout: Literal["every_brief", "scheduled_only", "off"] = "every_brief"
    # Domain setup: the generation run's tier and its own call cap. The cap is
    # generous headroom over the ~25-call plan, not a target.
    setup_model: str = "sonnet"
    setup_effort: str = "high"
    setup_max_calls: int = 60

    max_doc_chars: int = 12000
    gazetteer_size: int = 400

    @field_validator("harness")
    @classmethod
    def _a_known_service(cls, value: str) -> str:
        """Refused at the boundary, because the failure is otherwise unrecoverable.

        `harness` is editable over HTTP and read by `build_executor` in the
        lifespan. A typo saved through the ordinary config editor writes the
        file, rebinds the settings, and then raises on every subsequent start --
        so the only surface that could fix the value is the API that will not
        boot. Validating here refuses the save instead.
        """
        from venona.llm.harness import known

        if value not in known():
            raise ValueError(f"{value!r} is not a model service. Known: {', '.join(known())}.")
        return value

    def cli_path_for(self, harness: str, default: str = "") -> str:
        """The program name for one harness, per-harness but still FLAT.

        Flat because `config_service.FROZEN_FIELDS` walks exactly two attribute
        levels, `_settings_restart_required` digs the same way, and
        `config_text.key_span` only matches a group's direct children -- so a
        nested block would break the freeze check, the restart report and every
        future pin bump at once, all three silently.

        `default` is the service's own program name, so a service added later
        runs without needing a settings key at all -- which is what makes
        `Harness.binary` the live value rather than a field nothing reads.
        """
        override = {
            "claude_code": self.cli_path,
            "codex": self.codex_cli_path,
            "grok": self.grok_cli_path,
        }.get(harness, "")
        return override or default

    def pinned_version_for(self, harness: str) -> str:
        return {
            "codex": self.codex_pinned_version,
            "grok": self.grok_pinned_version,
        }.get(harness, self.pinned_version)

    @field_validator("task_timeout_seconds")
    @classmethod
    def _sane_task_timeouts(cls, value: dict[str, int]) -> dict[str, int]:
        for task, seconds in value.items():
            # Zero and negatives read as "no limit" and are the opposite: they
            # reach `asyncio.wait_for` and kill the call immediately.
            if seconds < 1:
                raise ValueError(f"{task}: a timeout must be at least 1 second, not {seconds}")
            if seconds > TASK_TIMEOUT_CEILING:
                raise ValueError(
                    f"{task}: {seconds}s is above the {TASK_TIMEOUT_CEILING}s ceiling. A call "
                    f"holding a lane slot for longer than that is indistinguishable from a "
                    f"wedged subprocess, and nothing else would reclaim it."
                )
        return value


# An hour. Long enough for any document the wizard writes, short enough that a
# subprocess which will never answer is still collected the same day.
TASK_TIMEOUT_CEILING = 3600

# Shipped per-task ceilings, under whatever the instance's own file says.
#
# `setup_ontology` is here because the wizard's largest document does not fit
# the 300s default: 2026-09-01, three live attempts killed at the ceiling for
# nothing (15 minutes, no draft), and the identical prompt returned in ~6
# minutes at 900s. It is one call in a rare run, so the cost of the headroom is
# a run that takes longer to fail if the provider is genuinely wedged.
TASK_TIMEOUT_DEFAULTS: dict[str, int] = {
    "setup_ontology": 900,
    # MEASURED 2026-09-13, and a ceiling inside the distribution it bounds
    # censors rather than catches. Successful discovery searches on the defense
    # corpus run to 299s with a real tail past four minutes; at 300s one sweep
    # timed out on 14 of 154 searches, every one at exactly the limit.
    "discovery": 600,
}


def timeout_for(llm: LlmSettings, task: str) -> int:
    """This task's ceiling: the instance's own answer, then ours, then global."""
    return (
        llm.task_timeout_seconds.get(task)
        or TASK_TIMEOUT_DEFAULTS.get(task)
        or (llm.timeout_seconds)
    )


class ScheduleSettings(BaseModel):
    model_config = _STRICT

    cron: str = "0 6 * * *"
    timezone: str = "America/Los_Angeles"
    enabled: bool = True
    # How many consecutive quiet nights before the daily report says so.
    # `scheduler.report_daily` carries the reasoning: a rotation can be entirely
    # alive and simply stop producing anything worth writing about, which no
    # single run can distinguish from a genuinely quiet Tuesday. `runs.outcome`
    # is what makes the question askable in SQL. 0 turns the alarm off.
    quiet_nights_alert: int = 3
    # A daily that met a provider outage -- the circuit opened on its watch and
    # it did not finish clean -- is re-fired this long after it ends, this many
    # times. Stages resume by state, so the retry costs only the work the outage
    # ate: the unanalyzed clusters and, if it was lost, the brief. Three tries
    # spans the long tail of incidents on the provider's status page; past that
    # the next scheduled run picks the backlog up anyway.
    #
    # Settings rather than module constants because this is the one scheduled
    # behaviour that SPENDS on its own initiative, at an hour when nobody is
    # watching, and the operator who wants it off should not need a deploy.
    # `outage_retry_max: 0` turns it off.
    outage_retry_max: int = 3
    outage_retry_delay_seconds: int = 1800


class PipelineSettings(BaseModel):
    model_config = _STRICT

    lookback_hours: int = 36
    # The window a FIRST brief uses, when `briefs` is empty. See
    # `pipeline.types.retrieval_window`.
    first_run_lookback_hours: int = 168
    # How old a DISCOVERED page may be. Discovery only; RSS is bounded by
    # `lookback_hours` at analyze instead. See config/settings.yaml.
    discovery_max_age_hours: int = 48
    # How much a run takes on. Both are `None` for uncapped -- see
    # config/settings.yaml.
    fetch_limit_per_source: int | None = None
    analyze_limit: int | None = None
    brief_top_clusters: int = 12
    brief_max_entities: int = 120
    acronym_min_sightings: int = 3
    acronym_ordinary_min_docs: int = 2  # see the note in config/settings.yaml
    acronym_min_articles: int = 1  # see the note in config/settings.yaml
    acronym_lookup_cap_per_run: int = 8
    # How long a surface that a web lookup already failed to ground waits before
    # it is worth paying for again.
    #
    # A RETRY WINDOW, not a permanent refusal. Most surfaces that fail tier 3 are
    # undefinable -- a typo, an OCR artefact, a string that is not an acronym at
    # all -- and re-searching them every night is the whole nightly cap spent on
    # the same doomed set while genuinely new terms wait behind them. But some
    # fail because the web had not caught up yet, so "never again" would bury a
    # term that becomes definable next month.
    acronym_retry_after_days: int = 30
    # How many surfaces one `lookup_all` run (contract C11) attempts -- one
    # BATCH. See config/settings.yaml for why this is 100 and not the whole
    # queue. `None` is uncapped, which the modal will happily quote a price for
    # if that is what you set.
    acronym_lookup_all_cap: int | None = 100
    # Entity definitions. See config/settings.yaml for what each one buys.
    define_cap_per_run: int = 100
    define_min_salience: float = 0.3
    # How many entities one `definitions` run (contract C11) will pay to ground
    # on the web. Tier 3, so every one is a search plus fetches plus a read --
    # far more expensive per entity than the `define` stage's tiers 1 and 2, and
    # the reason this cap is 10 where `define_cap_per_run` is 100.
    definitions_refresh_cap: int = 10
    provisional_promote_sightings: int = 2
    provisional_promote_sources: int = 2
    provisional_dormant_days: int = 30
    auto_merge_identical: bool = True
    adjudicate_conflicts: bool = True
    #: Apply the adjudicator's `merge` verdict without a curator, but ONLY where
    #: a structural signal corroborates it. See `pipeline.curate.model_merges`.
    auto_merge_model_verdicts: bool = True
    auto_keep_apart_model_verdicts: bool = True
    auto_relate_model_verdicts: bool = True
    #: How many times a PROVISIONAL entity must have been mentioned before a
    #: duplicate conflict is filed about it. 0 files about everything, which is
    #: where this has always been.
    #:
    #: Measured on the live queue: of 455 open conflicts, 383 name a proposed
    #: entity seen fewer than twice and only 23 have two active sides -- and half
    #: of every human keep-apart on a containment pair was spent on an entity
    #: that never corroborated and drained on its own. The filing net was
    #: deliberately wide because "a loose grouping costs a curator one keystroke",
    #: which is true of one row and false of three hundred.
    #:
    #: It DEFERS rather than drops: filing is asked of the whole corpus every
    #: run, so the moment a surface returns and the entity corroborates, the
    #: question is filed then. A declared or active entity is never held back.
    file_duplicates_min_mentions: int = 2
    adjudicate_limit_per_run: int = 60
    adjudicate_batch: int = 20
    research_entities_per_sweep: int = 10
    research_max_sweeps: int = 3
    fuzzy_accept_score: int = 92
    fuzzy_runnerup_margin: int = 4


class Paths(BaseSettings):
    """Where this instance's files are, from the environment and defaults only.

    Separate from `Settings` because `settings.yaml` is FOUND by `config_dir`:
    resolving it cannot require a settings object built from the very file it
    locates. Splitting the five path fields out is what breaks that circle, and
    it is also why a `config_dir:` INSIDE the document is refused rather than
    honoured -- such a key could only disagree with the directory the file was
    just read from.

    One local machine runs several instances, each with its own
    `VENONA_CONFIG_DIR`, so this is the seam that keeps them apart.
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_prefix="VENONA_",
        extra="ignore",
    )

    repo_root: Path = REPO_ROOT
    data_dir: Path = REPO_ROOT / "data"
    config_dir: Path = REPO_ROOT / "config"
    profile_dir: Path = REPO_ROOT / "profile"
    fixtures_dir: Path = REPO_ROOT / "fixtures"
    #: `docs/USER-GUIDE.md` is served from here by `/api/meta/guide`. A setting
    #: rather than a constant so a test can redirect it, the way `api_settings`
    #: in `tests/conftest.py` redirects `profile_dir` and `config_dir` -- a
    #: fixture that only moved the database wrote `MOSA` into the real lexicon.
    docs_dir: Path = REPO_ROOT / "docs"


def settings_path() -> Path:
    """The `settings.yaml` THIS instance reads and the config rail writes.

    One function so the two can never disagree again. `venona config path`
    prints it, and every load error below names it.
    """
    return Paths().config_dir / "settings.yaml"


#: The DSN a clean checkout gets, matching `compose.yaml`. Named so the property
#: below and `database_dsn()` cannot drift -- they are the same answer asked at
#: two different moments in the boot.
DEFAULT_DATABASE_URL = "postgresql://venona:venona-local@127.0.0.1:5432/venona"


def database_dsn() -> str:
    """The database, WITHOUT parsing `settings.yaml`.

    THE BOOT HAS AN ORDERING PROBLEM AND THIS IS THE SEAM (ADR 0234).
    `config_migrations.migrate` runs before `get_settings()`, and the documents
    it migrates are written out of Postgres first -- so the database must be
    reachable before the settings object carrying its URL exists. Possible only
    because this value never came from the document. If the DSN ever becomes a
    settings field, this function cannot exist.
    """
    return os.environ.get("VENONA_DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL


class Settings(Paths):
    #: Read through the `database_url` property below, never directly -- the
    #: property is where the default lives, so nothing has to repeat the DSN.
    #: Named `_override` because a bare `database_url` field would collide with
    #: that property, and given an explicit alias because `env_prefix` would
    #: otherwise make the variable `VENONA_DATABASE_URL_OVERRIDE`. The alias
    #: bypasses the prefix, which is the same reason `NEO4J_URI` below has one.
    database_url_override: str = Field(default="", alias="VENONA_DATABASE_URL")

    #: How many connections the API's request pool may hold open at once.
    #:
    #: This is a share of the SERVER's `max_connections`, not a throughput dial,
    #: and it is deliberately well under it: runs, SSE streams, the scheduler and
    #: any CLI process each open their own connections outside this pool, and a
    #: managed Postgres on a small tier allows around a hundred in total. Raising
    #: it past what the server has spare converts a slow burst into a hard
    #: failure, which arrives disguised as every signed-in user being signed out.
    db_pool_max_size: int = 10

    #: How long shutdown waits for light-lane runs (epic AA). THE TOTAL MUST FIT
    #: INSIDE `maxShutdownDelaySeconds` (300): this is spent after uvicorn drains
    #: connections, capped at 30. `test_shutdown_drain` holds the arithmetic.
    shutdown_drain_seconds: int = 150

    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="venona-local", alias="NEO4J_PASSWORD")

    offline: bool = False

    #: Which configuration updates this instance's `settings.yaml` has already
    #: received. Written by `services/config_migrations` and reported by
    #: `/api/health`. It is a field because the document carries it and a
    #: document key with no field is refused at boot by `unknown_top_level_keys`.
    settings_version: int = 0

    llm: LlmSettings = LlmSettings()
    schedule: ScheduleSettings = ScheduleSettings()
    pipeline: PipelineSettings = PipelineSettings()

    @property
    def database_url(self) -> str:
        """The system of record (invariant 1).

        A DSN rather than a path under `data_dir`, with one consequence worth
        stating where it will be read: a DSN is not isolated by moving a
        directory, so pointing `data_dir` at `tmp_path` does not sandbox the
        database. `tests/conftest.py` isolates with a DATABASE per test, copied
        from a template, and still redirects `profile_dir` and `config_dir` --
        a probe that moved only the database once wrote `MOSA` into the real
        `profile/lexicon.yaml`.

        `VENONA_DATABASE_URL` overrides it, which is how a self-hosted deployment
        points at the `postgres` service on the internal `data` network. The
        default matches `compose.yaml`, so a clean checkout needs no env file.
        """
        return self.database_url_override or DEFAULT_DATABASE_URL

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def unknown_top_level_keys(values: Any) -> list[str]:
    """Top-level `settings.yaml` keys that `Settings(**values)` would discard.

    The nested groups get `extra="forbid"` and need nothing here. The top level
    cannot have it: `env_prefix="VENONA_"` means every `VENONA_*` variable in
    `.env` is offered to this model, and `VENONA_AUTH_ENABLED` is not a field --
    `api/security.py` reads it from the environment directly. `extra="forbid"`
    would refuse to start `make dev`.

    So this checks the FILE, which is the surface the Settings rail writes and
    the only one where a typo is silent rather than loud.

    It compares against ALIASES, not field names, and that distinction is the
    reason this is a function rather than a set literal. `neo4j_uri` declares
    `alias="NEO4J_URI"` without `populate_by_name`, so the YAML key `NEO4J_URI:`
    populates the field and `neo4j_uri:` is dropped -- see `FROZEN_TOP` in
    `services/config_service.py`, which documents the same asymmetry from the
    other side. Checking field names would bless the spelling that does nothing.

    A non-mapping returns `[]` rather than raising or reporting nonsense. A
    top-level YAML sequence would otherwise hand `in` a dict and raise
    `TypeError: unhashable type`, and a bare scalar would report its own
    characters as unknown keys. Neither is this function's question -- both are
    "that is not a settings document", which `Settings(**parsed)` says next.
    """
    if not isinstance(values, dict):
        return []
    accepted = {field.alias or name for name, field in Settings.model_fields.items()}
    return sorted(key for key in values if key not in accepted)


def is_production() -> bool:
    """Whether this process is a real deployment rather than someone's laptop.

    Read from the environment rather than from `Settings`, because the earliest
    caller needs it at import time, before any settings object exists.

    Both production paths set it -- the deployment config and a self-hosted deployment -- and
    nothing a developer runs does. That asymmetry is the whole value: it is the
    one signal that separates "a container came back" from "someone started the
    app to look at it", and those two need different answers about work that
    spends money.
    """
    return os.environ.get("VENONA_ENV", "").lower() == "production"


#: Path fields are resolved by `Paths` from the environment, so a copy of one
#: inside the document can only disagree with the directory the document was
#: read from. Refused outright rather than ignored: a `config_dir:` that agrees
#: today diverges the day someone sets `VENONA_CONFIG_DIR`, and it would point
#: at the file it is written in.
_PATH_KEYS = ("repo_root", "data_dir", "config_dir", "profile_dir", "fixtures_dir", "docs_dir")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    path = settings_path()
    file_values = _load_yaml(path)
    in_document = [key for key in _PATH_KEYS if key in file_values]
    if in_document:
        raise ValueError(
            f"{path}: {in_document} cannot be set here. This file was FOUND at "
            f"this path, so a copy of it inside the file could only disagree "
            f"with where it was just read from. Set VENONA_CONFIG_DIR and the "
            f"rest in the environment."
        )
    unknown = unknown_top_level_keys(file_values)
    if unknown:
        raise ValueError(
            f"{path}: unknown top-level key(s) {unknown}. "
            f"Nothing reads them, so whatever they were meant to change is still "
            f"at its default. Accepted: "
            f"{sorted({f.alias or n for n, f in Settings.model_fields.items()})}"
        )

    # The nested groups enforce themselves, but a raw `ValidationError` at
    # import time is a bare traceback out of `make dev`, `venona <anything>` and
    # `make lint` alike -- with the offending key buried in pydantic's format and
    # no statement of where the file is or what to do. The top-level branch above
    # already says all of that; this makes the nested case say it too.
    try:
        settings = Settings(**file_values)
    except Exception as exc:
        raise ValueError(
            f"{path} is not loadable, so nothing can start:\n\n{exc}\n\n"
            f"An unknown key in one of the groups is the usual cause -- they "
            f"forbid extras, because a key nothing reads is a setting that "
            f"silently stays at its default. Fix the file, or remove the key."
        ) from exc

    # Decision 7: one switch flips every nondeterminism source at once.
    #
    # `harness` is one of them, and became so the moment there was more than
    # one. It decides which driver parses an envelope, which directory a
    # fixture is read from, and which model names the cache key is taken over
    # -- so leaving it to the instance makes the offline suite depend on which
    # service an administrator last chose in the app. On the default instance
    # `config/` IS the repository, so that is not hypothetical: switching
    # services on a workstation changed which code path `make test` exercised.
    #
    # A test that wants another service says so, by handing the executor an
    # explicit harness. The DEFAULT is deterministic.
    if os.environ.get("VENONA_OFFLINE") == "1":
        settings.offline = True
        settings.llm.backend = "replay"
        settings.llm.harness = "claude_code"

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    return settings
