"""Which agentic CLI does the thinking, and everything that differs between them.

Contract C2 says the binary is invoked in one place. This package is what makes
that survivable with more than one binary: `executor.py` still owns the
subprocess, the semaphore, the breaker and the ledger, and a driver here owns
only the parts that are true of one vendor and false of another. ADR 0216 lists
the five methods that are here because Claude-shaped logic wore a general name.

**The table is the interface.** `_table()` is read top to bottom the way
`services/run_types.py` and `api/mcp/registry.py` are, because a table a person
can read beats a decorator scattered across a package. Adding a harness is a
module, a row, an install line and a fixture directory -- and no change to any
call site, stage, `RunContext`, ledger, admin page or run surface.

**`binary` is a NAME, never a path from the wire.** `llm.harness` is editable
over HTTP; `llm.cli_path` is frozen (`config_service.FROZEN_FIELDS`) precisely
because `executor.py` execs it. Nothing an administrator can type reaches argv,
which is the whole reason this table exists rather than a settings key holding a
program name.
"""

from __future__ import annotations

import contextlib
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from venona.llm.types import HarnessHealth, LlmError, LlmResult, PlanHelp, RateLimits

#: What a task may ask a harness to be able to do. Two verbs rather than one:
#: a harness can have a server-side search and no way to fetch a named page,
#: and a single coarse capability would let it run degraded silently -- which
#: is the failure declaring capabilities at all is meant to prevent.
WEB_SEARCH = "web_search"
WEB_FETCH = "web_fetch"

#: How a stage's need is graded. A `required` capability the active harness
#: lacks refuses the call before it is made; a `preferred` one is dropped and
#: noted. `web_grounding`'s search step wants both and can work with search
#: alone, so it asks for one of each rather than for a single tuple.
Grade = Literal["required", "preferred"]

#: How a failure should be treated. `transient` is the provider saying "not
#: now" and is what the research lane waits out (ADR 0155); `terminal` is
#: "never" and opens the circuit; `refusal` is the model declining or exceeding
#: a context it was given, which is a result rather than damage and must not
#: count against the breaker.
Verdict = Literal["transient", "terminal", "refusal"]


class HarnessError(LlmError):
    """The active harness cannot serve this call, and nothing was spent."""


@dataclass(frozen=True)
class Credential:
    """What reaches the child process so the CLI can authenticate.

    `env` is what the harness needs set; `suppress` is what it needs UNSET.
    The second half is not symmetry for its own sake: every vendor in the table
    reads an environment variable ahead of its own credential store, and every
    venona instance that exists already sets one. Without `suppress` an operator
    connects an account, the page reports success, and every call keeps using
    the deploy's key.
    """

    env: Mapping[str, str] = field(default_factory=dict)
    suppress: frozenset[str] = frozenset()
    home: Path | None = None


@dataclass(frozen=True)
class Confinement:
    """The filesystem a model process is allowed, as two path sets.

    Deny-by-default: everything not named here is refused, including the
    repository, the credential store and the reader's home directory. See
    `confine.py` for the mechanism and why it is Landlock rather than bwrap.
    """

    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()


#: What any CLI needs merely to run: the runtime, its libraries, the trust
#: store, the resolver, and the two pseudo-filesystems every language runtime
#: reads itself out of. Read-only, and none of it is ours.
#:
#: `/run` is here for DNS, and it is not obvious. Under systemd-resolved
#: `/etc/resolv.conf` is a SYMLINK to `/run/systemd/resolve/stub-resolv.conf`,
#: so granting `/etc` grants the link and not the file behind it. Without this
#: every name lookup returns EAI_AGAIN, which the CLI reported (2026-09-10) as a
#: dropped stream retried five times -- a failure that reads as the provider
#: being unreachable rather than as a restriction we applied.
_SYSTEM_READS = (
    "/usr",
    "/lib",
    "/lib64",
    "/bin",
    "/sbin",
    "/opt",
    "/etc",
    # `/proc/self` AND NOT `/proc`. Granting the whole of it puts
    # `/proc/<uvicorn pid>/environ` inside the read set, and the model process
    # runs as the same uid -- so on any host with `kernel.yama.ptrace_scope=0`
    # the model could read the database URL, the session secret and the
    # management token, which is precisely the list `child_env`'s allowlist
    # exists to withhold. A process's own `/proc/self` is per-process, so a
    # child still resolves its own.
    "/proc/self",
    "/proc/sys",
    "/proc/cpuinfo",
    "/proc/meminfo",
    "/proc/stat",
    "/proc/uptime",
    "/proc/loadavg",
    "/proc/filesystems",
    "/sys",
    "/run",
    "/var/run",
)

#: `/dev` is a WRITE set member because `/dev/null` is opened for writing by
#: approximately everything, and `/tmp` because a node runtime that cannot
#: write a scratch file fails in a way that reads as a corrupt install.
#: Neither holds anything of venona's -- the credential is under `data_dir`,
#: which is granted one subdirectory at a time below.
_SYSTEM_WRITES = ("/dev", "/tmp", "/var/tmp")


def confinement_for(
    settings: Any, harness: Harness, *, child_env: Mapping[str, str], binary: str
) -> Confinement:
    """The path sets for one call, composed rather than written twice.

    The harnesses want nearly the same filesystem: a runtime, their own
    installation, the prompt files this process wrote for them, and their own
    configuration directory. What differs is only which directory that last one
    is, so it is a parameter and not a driver method.

    NOTE WHAT IS ABSENT. `data_dir` is never granted whole -- only the prompt
    cache under it -- so the credential directory beside it is unreachable even
    though it is one level away. That adjacency is the reason the credential
    was put beside the agent's home rather than inside it.
    """
    reads = [*_SYSTEM_READS]

    # The CLI's own INSTALLATION ROOT, not just the directory the binary sits
    # in. Resolved, because the name on PATH is usually a symlink into a
    # versioned directory and granting the link grants nothing -- and one level
    # up, because a CLI installed as `<root>/bin/<name>` keeps the rest of
    # itself beside `bin`. Codex is measurably that shape (2026-09-14, `ls` on
    # the resolved install): `codex-resources/` and `codex-path/` are siblings
    # of `bin`, and a second 69 MB binary lives in `bin` alongside the first.
    resolved = shutil.which(binary) or binary
    with contextlib.suppress(OSError):
        directory = Path(resolved).resolve().parent
        reads.append(str(directory))
        if directory.name == "bin":
            reads.append(str(directory.parent))

    # The system prompt and schema files. `argv` points the CLI at these by
    # path, so a confined call that cannot read them fails with the schema
    # missing rather than with a permission error.
    reads.append(str(Path(settings.data_dir) / "cache"))

    # THE CONFIGURATION DIRECTORY THE CHILD WILL ACTUALLY USE, which is not
    # always Venona's. `credential_for` points at its own home only once
    # something has been written there, so an instance authenticating through
    # the vendor's own store keeps using the vendor's own directory -- and a
    # restriction that granted only Venona's would stop the CLI before it ever
    # reached the question of who it is signed in as. Measured 2026-09-10: codex
    # extracts helper binaries and PATH aliases into its home at startup and
    # exits 1 if it cannot.
    in_use = _home_in_use(harness, child_env)
    if in_use is None:
        # Nothing to grant and no way to guess. Returning an empty write set
        # would confine the CLI out of its own configuration directory, which
        # it exits on, so the caller runs unconfined and says so instead.
        return Confinement()

    # CREATED HERE, which is a side effect in something that reads like a
    # builder, and the alternative is worse. `confine.py` skips a path that is
    # not there -- it cannot grant what does not exist -- so on a first boot
    # the CLI's home has never been created, is therefore not granted, and the
    # CLI cannot create it. The failure is a permission error at startup on a
    # brand new instance and nowhere else, which is the hardest kind to find.
    with contextlib.suppress(OSError):
        in_use.mkdir(parents=True, exist_ok=True)
    writes = [*_SYSTEM_WRITES, str(in_use)]

    return Confinement(reads=tuple(reads), writes=tuple(writes))


def _home_in_use(harness: Harness, child_env: Mapping[str, str]) -> Path | None:
    """The configuration directory the CHILD will use, read off the child's env.

    FROM THE ENVIRONMENT THE CHILD ACTUALLY GETS, never from this process's.
    The two are not the same and the difference is silent: `codex.child_env` is
    an allowlist that drops `CODEX_HOME` unless Venona is pointing at a home of
    its own, so an operator who sets that variable on the deployment has a
    child that writes to `$HOME/.codex` while a confinement built from the
    parent's environment grants a directory nobody uses.

    None when there is no answer -- no `HOME` in the child's environment, or a
    value that is not an absolute path. A RELATIVE one is the dangerous case:
    it would resolve against this process's working directory, which is the
    repository, and grant the model write access inside the checkout.
    """
    for candidate in (child_env.get(harness.home_env), None):
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        return path if path.is_absolute() else None

    base = child_env.get("HOME")
    if not base:
        return None
    home = Path(base).expanduser()
    if not home.is_absolute() or home == Path("/"):
        # `HOME=/` would put the CLI's directory at the filesystem root and
        # hand the model write access to it.
        return None
    return home / harness.home_dirname


@dataclass(frozen=True)
class CallRequest:
    """One call, with every value already in the harness's own vocabulary.

    Resolution happens before this is built, at the site that reads the setting,
    so `cache_key_for` sees native names and a replayed fixture keys identically
    to the live call that recorded it.
    """

    task: str
    system_prompt: str
    schema: dict[str, Any]
    model: str
    effort: str
    tools: Sequence[str]
    user_content: str


@dataclass(frozen=True)
class ProbeSpec:
    """The two read-only invocations `health()` makes, as argv after the binary.

    Two rather than one because "is it installed" and "is it signed in" fail
    differently and an operator needs to be told which. Neither spends.
    """

    version: Sequence[str]
    auth: Sequence[str]


@dataclass(frozen=True)
class QuotaSpec:
    """A read-only invocation that reports how much of a subscription is left.

    Separate from `ProbeSpec` because it is not part of health and does not
    always exist: a service that reports its windows in the envelope of every
    call it answers needs no invocation at all, and one that reports them
    nowhere in the surface Venona drives for model calls needs a second
    protocol -- which is a spawn, with a request written to stdin.

    `stdin` is what to write. It is NOT closed after writing: a request/response
    server reads until EOF and exits when it gets one, so closing the pipe to
    signal "that is the whole request" also cancels the request. `done_when` is
    the substring that marks the reply worth waiting for, and reaching it is
    what ends the read.
    """

    argv: Sequence[str]
    stdin: str = ""
    done_when: str = ""


@dataclass(frozen=True)
class ProbeOutput:
    """What those two invocations produced, including the auth exit code.

    The exit code is carried because it is not always failure: `codex login
    status` prints a meaningful answer and exits 1 when signed out, so a probe
    that treated non-zero as unusable would report a working install as broken.
    """

    version: str
    auth: str
    auth_returncode: int = 0


class FileCache(Protocol):
    """Content-addressed scratch files for argv that cannot carry a payload.

    Owned by the executor rather than by a driver so that both the reason for
    the cache (argv has a per-element ceiling) and its location (`data_dir`)
    stay in one place, and so `argv()` receives paths that already exist.
    """

    async def materialize(self, content: str, *, kind: str, suffix: str) -> Path | None: ...


class LoginDriver(Protocol):
    """A credential-granting flow the app can drive on the operator's behalf.

    Every driver returns None from `Harness.login`, each for a reason its own
    docstring carries: a flow that needs a browser on the same machine, or a
    device-auth command with no documented output. Connecting is a value pasted
    into the admin page, or a vendor's own sign-in command run on the machine
    Venona runs on. ADR 0238, which also carries the reversal trigger.
    """

    async def begin(self) -> dict[str, Any]: ...
    async def poll(self) -> dict[str, Any]: ...
    async def cancel(self) -> None: ...


class Harness(Protocol):
    key: str
    label: str
    binary: str
    #: Tier names AND effort names to this vendor's own values, with anything
    #: absent passed through unchanged. Pass-through is deliberate: a code-side
    #: list of valid models would have to track a vendor's release calendar and
    #: fails in the worst direction when it lags, refusing the model an operator
    #: is trying to adopt on the day they try. `config_service._check_model_names`
    #: makes the same argument about the same values.
    aliases: Mapping[str, str]
    capabilities: frozenset[str]
    billing_modes: frozenset[str]
    #: Environment variables this vendor reads on its own, ahead of its
    #: credential store. Drives the credential suppression: a stored credential
    #: only wins if these are cleared from the child.
    secret_env: frozenset[str]
    #: The subset of `secret_env` that means METERED BILLING. Separate because
    #: the two are opposites: one of these in the environment of a subscription
    #: instance is the accident `main.py` refuses to boot around (ADR 0216),
    #: while a subscription token in the same place is the ordinary deployment.
    #: Named rather than matched on a suffix, which cannot tell them apart.
    metered_env: frozenset[str]
    #: The variable naming this vendor's own configuration directory.
    home_env: str
    #: What that directory is called under `$HOME` when the variable is unset.
    #: Needed by the confinement: a CLI that Venona has not pointed anywhere
    #: writes to its default home, and a restriction that does not grant it
    #: fails at startup with a permission error rather than a sign-in one.
    home_dirname: str
    #: The files inside that directory that carry authentication (ADR 0234). A
    #: subscription credential rotates and must survive a deploy; the hundreds of
    #: megabytes around it -- models catalog, SQLite state, logs -- regenerate,
    #: so only these are kept as a row.
    #:
    #: EMPTY IS VALID and the safe default: a harness declaring nothing is never
    #: captured and needs a sign-in after a restart, so being wrong by omission
    #: costs a sign-in rather than a silent authentication failure.
    credential_paths: tuple[str, ...]
    #: Whether this service reports how much of a subscription window is left.
    #: Declared because the SURFACE has to differ: a service that will report
    #: one has simply not been asked yet, and saying "the first run will fill
    #: this in" is true. A service that never will must not be described that
    #: way -- it is a promise nothing can keep, on the one strip a reader
    #: consults to decide whether to start another run.
    reports_usage_windows: bool
    #: Subdirectory of `fixtures/llm/` this harness records into. Empty for the
    #: harness whose fixtures predate there being two.
    fixture_root: str
    #: Whether a model process from this harness should be locked to a
    #: filesystem before it runs. Per harness because the risk is not equal:
    #: one is invoked with `--tools ""` on every task but four and so has
    #: nothing to read a file WITH, while the others run code by default and
    #: one of them has a read-only sandbox that permits reads by design.
    wants_confinement: bool

    async def argv(self, req: CallRequest, files: FileCache) -> list[str]: ...

    def env_for(self, billing_mode: str) -> str | None:
        """Which variable carries a credential of this KIND, or None for a
        vendor that keeps this kind in a file rather than the environment.

        Declared rather than guessed. Matching on an `_API_KEY` suffix put a
        pasted subscription token into the variable that means metered billing
        and suppressed the one it belonged in -- so either every call failed
        while the sign-in probe stayed green, or a real key billed per token on
        an instance whose settings said subscription. Both are the metered
        billing scar (ADR 0216), reached through the page built to prevent it.
        """
        ...

    def plan_help(self, billing_mode: str) -> PlanHelp:
        """What connecting on this plan actually asks of an administrator.

        Declared per driver for the same reason `classify` is: the answer is one
        vendor's, and a shared one would be wrong for two of the three. Without
        it the page offers a password box labelled "Paste the credential" for
        every service on every plan, including the two plans where there is
        nothing to paste because the service keeps its own file.
        """
        ...

    def child_env(self, base: Mapping[str, str], cred: Credential) -> dict[str, str]: ...

    def adapt_schema(self, schema: dict[str, Any]) -> dict[str, Any]: ...

    def parse(self, stdout: str, *, cache_key: str, duration_ms: int) -> LlmResult: ...

    def classify(self, error: str) -> Verdict: ...

    def is_transcript_event(self, raw: dict[str, Any]) -> bool: ...

    def summarize(self, raw: dict[str, Any], *, task: str) -> dict[str, Any] | None: ...

    def probe(self) -> ProbeSpec: ...

    def quota(self) -> QuotaSpec | None:
        """How to READ the subscription windows out of band, if that is needed.

        None for a service that reports them per call, which is not a service
        that lacks them -- `reports_usage_windows` is the field that says
        whether they are obtainable at all.
        """
        return None

    def parse_quota(self, stdout: str) -> RateLimits | None:
        return None

    def parse_probe(self, out: ProbeOutput, *, pinned: str) -> HarnessHealth: ...

    def login(self) -> LoginDriver | None: ...


def _table() -> dict[str, Harness]:
    """Built on demand rather than at import so a driver may import this module.

    The same reason `db/models.py` reads the ontology at the point of use: a
    module-level binding here would be a cycle, and a cycle in a registry is
    resolved by someone moving a definition rather than by fixing the design.
    """
    from venona.llm.harness.claude_code import CLAUDE_CODE
    from venona.llm.harness.codex import CODEX
    from venona.llm.harness.grok import GROK

    return {CLAUDE_CODE.key: CLAUDE_CODE, CODEX.key: CODEX, GROK.key: GROK}


def known() -> tuple[str, ...]:
    return tuple(_table())


def resolve(key: str) -> Harness:
    harness = _table().get(key)
    if harness is None:
        raise HarnessError(
            f"{key!r} is not a model service venona knows. Known: {', '.join(known())}."
        )
    return harness


def model_for(harness: Harness, value: str) -> str:
    """A tier name, or a model name this harness already understands.

    Pass-through rather than a lookup that can fail, so an operator adopting a
    model on its release day is not blocked by this table being a week old.
    """
    return harness.aliases.get(value, value)


def matches_pin(version: str | None, pinned: str) -> bool:
    """Does the installed version satisfy the pin, if there is one?

    AN EMPTY PIN IS NO PIN. `grok_pinned_version` ships empty on purpose --
    nobody has run a version of it, and settings.py argues that inventing a
    number "would report every install as wrong". A bare `version == pinned`
    gives an empty pin that exact behaviour: `/admin/model` reports a mismatch
    against nothing, on every install, forever.
    """
    return not pinned or version == pinned


def missing_capabilities(
    harness: Harness, wanted: Mapping[str, Grade]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """What this harness cannot do, split by whether the call may still proceed.

    Returned rather than raised so the caller decides: a required gap refuses
    before anything is spent, a preferred gap is a `ctx.note` and a narrower
    call. Deciding here would make the note unavailable to the run's message
    list, which is where a reader looks for why an answer was thinner.
    """
    missing = {c for c in wanted if c not in harness.capabilities}
    blocked = tuple(sorted(c for c in missing if wanted[c] == "required"))
    degraded = tuple(sorted(c for c in missing if wanted[c] == "preferred"))
    return blocked, degraded
