"""Contract C2 -- the only place the `claude` binary is invoked.

Everything model-related funnels through `LlmExecutor.call()`: budget accounting,
the usage ledger, and offline replay all hang off this one seam. Calling the CLI
from anywhere else bypasses all three.

Every verified CLI behaviour is a fact about ONE service, so each is written
down in that service's driver -- `llm/harness/claude_code.py` carries the Claude
Code list, measured against 2.1.220. Contract C2 tabulates them all.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
import sys
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from venona.llm.breaker import BreakerOpen, CircuitBreaker
from venona.llm.harness import (
    CallRequest,
    Confinement,
    Credential,
    Harness,
    ProbeOutput,
    confine,
    confinement_for,
    resolve,
)
from venona.llm.types import (
    HarnessHealth,
    LlmError,
    LlmResult,
    PermissionDenied,
    QuotaWindow,
    RateLimits,
    Usage,
)
from venona.settings import Settings, timeout_for

log = logging.getLogger("venona.llm")

# Strong references to the fire-and-forget notice tasks, so the loop cannot
# collect one mid-flight. `scheduler._tasks` is the same set for the same
# reason: `asyncio.create_task` keeps only a weak reference, and the documented
# symptom is work that silently stops happening.
_pending: set[asyncio.Task] = set()

# The longest one sleep inside an outage wait lasts before re-checking
# `wait_abort`. Bounds how long a cancelled run sits in the wait.
WAIT_SLICE_SECONDS = 10.0

# How much of a child's stderr is kept, in lines. Enough to explain a non-zero
# exit; bounded because a CLI that retries noisily would otherwise hold its
# whole retry storm in memory for the length of the call.
STDERR_KEEP_LINES = 200


#: `LlmError`, `PermissionDenied`, `Usage`, `QuotaWindow`, `RateLimits` and
#: `LlmResult` are imported above rather than declared here, and are part of
#: this module's surface: contract C2 names `LlmResult` as the return of this
#: seam, so where the class is DECLARED must not change where callers find it.
CliHealth = HarnessHealth

__all__ = [
    "CliHealth",
    "HarnessHealth",
    "LlmError",
    "LlmExecutor",
    "LlmResult",
    "PermissionDenied",
    "QuotaWindow",
    "RateLimits",
    "Usage",
    "build_executor",
    "cache_key_for",
    "parse_envelope",
]


# How long a CLI health probe is reused. Long enough that a 3-second poll costs
# nothing, short enough that logging the CLI in or out shows up while you are
# still looking at the screen.
HEALTH_TTL_SECONDS = 60.0

#: How long a subscription-window reading is reused. A service that reports its
#: windows in the envelope of every call needs none of this; one that has to be
#: ASKED gets asked at most this often, because the answer moves by a percent
#: over minutes and a spawn per model call would be a third of the process
#: budget spent on a strip nobody is watching mid-run.
QUOTA_TTL_SECONDS = 120.0


def cache_key_for(
    *,
    task: str,
    system_prompt: str,
    user_content: str,
    schema: dict[str, Any],
    model: str,
    effort: str,
    tools: Sequence[str],
) -> str:
    """Stable fixture key. Frozen before the first fixture is recorded --
    changing the recipe rekeys every file under fixtures/llm/."""
    payload = {
        "task": task,
        "system_prompt_sha": hashlib.sha256(system_prompt.encode()).hexdigest(),
        "user_content": user_content,
        "schema": schema,
        "model": model,
        "effort": effort,
        "tools": sorted(tools),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


# A live message from the CLI session, mid-call. See `CliExecutor._run_streaming`.
OnEvent = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class _PromptFiles:
    """Content-addressed files for the parts of a call argv cannot carry.

    A single argv element is capped at MAX_ARG_STRLEN (131072 on Linux,
    measured 2026-08-06), and both a replaced system prompt and a response
    schema can pass it. Content-addressed rather than per-call so an unchanged
    prompt is written once and the path is stable, which is what lets a
    provider's prefix cache see the same bytes twice.

    `supported` is awaited before a path is handed back because one vendor's
    file flag is undocumented and could vanish on an upgrade; returning None
    tells the driver to inline instead.
    """

    root: Path
    supported: Callable[[], Awaitable[bool]]

    async def materialize(self, content: str, *, kind: str, suffix: str) -> Path | None:
        if not await self.supported():
            return None
        directory = self.root / kind
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{hashlib.sha256(content.encode()).hexdigest()[:16]}{suffix}"
        if not target.exists():
            target.write_text(content)
        return target


class LlmExecutor(Protocol):
    async def call(
        self,
        task: str,
        *,
        user_content: str,
        schema: dict[str, Any],
        system_prompt: str = "",
        model: str = "sonnet",
        effort: str = "medium",
        tools: Sequence[str] = (),
        timeout: int | None = None,
        on_event: OnEvent | None = None,
        #: PER CALL, not an instance attribute. See `CliExecutor.call`.
        on_wait: Callable[[float | None], Awaitable[None]] | None = None,
        wait_abort: Callable[[], bool] | None = None,
    ) -> LlmResult: ...


class CliExecutor:
    """Subprocess wrapper around whichever agentic CLI this instance runs.

    Everything here is true of any of them: one semaphore, one circuit breaker,
    the outage wait, the pumps, reaping, the health cache and fixture recording.
    What differs by vendor lives behind `self._harness` -- argv, the envelope,
    the error vocabulary, the probe and the transcript shape.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        harness: Harness | None = None,
        credential: Credential | None = None,
        record: bool = False,
        concurrency: int | None = None,
        outage_wait_seconds: float | None = None,
    ) -> None:
        self._settings = settings
        self._llm = settings.llm
        self._harness = harness if harness is not None else resolve(self._llm.harness)
        # Set only when a candidate credential is being CHECKED. The admin
        # surface verifies a pasted value before storing it, so a token that
        # does not work is refused at the dialog rather than discovered by
        # the next run, and nothing is written until the probe answers.
        self._credential_override = credential
        # `concurrency` overrides the configured value for a second instance
        # serving the `light` lane. Only the semaphore differs -- the ledger,
        # the budget accounting and offline replay are identical, so contract
        # C2 still holds and this is still the only place the binary is run.
        self._semaphore = asyncio.Semaphore(
            self._llm.concurrency if concurrency is None else concurrency
        )
        # How long a call may sit waiting for an open circuit to close when the
        # failure holding it open is TRANSIENT (a provider incident), measured
        # from the start of the episode. The research lane waits -- a brief
        # thirty minutes late beats no brief -- and the light lane passes 0,
        # because a person is watching a spinner for every one of its calls.
        self._outage_wait = (
            float(self._llm.outage_wait_seconds)
            if outage_wait_seconds is None
            else float(outage_wait_seconds)
        )
        # Set by the orchestrator for the duration of a run, so a cancelled run
        # does not sit out the whole outage wait before noticing.
        self.wait_abort: Callable[[], bool] | None = None
        # Also the orchestrator's, and set the same way: a run sitting out a
        # provider outage is indistinguishable from a wedged one, and this is
        # the longer of the two waits -- up to `outage_wait_seconds`, which is
        # half an hour. Reported rather than logged, because the person who
        # needs to know is watching the run page and not the server's log.
        self.on_wait: Callable[[float | None], Awaitable[None]] | None = None
        self._waiting_since: float | None = None
        self._supports_prompt_file: bool | None = None
        # When set, every successful call is captured as a fixture. Live
        # development runs then double as free, always-accurate test data.
        # The SAME root `ReplayExecutor` reads. Without the service's own
        # subdirectory, recording on one service overwrites another's
        # fixture at the same key, and replay then reads one vendor's
        # stream through the other's parser.
        self._record_dir: Path | None = (
            settings.fixtures_dir / "llm" / self._harness.fixture_root if record else None
        )
        self._health_cache: tuple[float, CliHealth] | None = None
        self._quota_cache: tuple[float, RateLimits | None] | None = None
        self._health_lock = asyncio.Lock()
        self._health_refresh_task: asyncio.Task[None] | None = None
        # Per executor instance, so the research and light lanes fail
        # independently -- a dead credential trips both, but a run that wedges
        # one lane does not silently disable the other.
        #
        # The classifier comes from the harness because "is this transient" is
        # a question about one vendor's error vocabulary, and getting it wrong
        # in the terminal direction aborts a run at the first refusal instead
        # of waiting out an incident.
        self._breaker = CircuitBreaker(classify=self._harness.classify)

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def harness(self) -> Harness:
        """Read by `runner` for the transcript shape and the retry verdict.

        Reached by `getattr` at those sites rather than through `LlmExecutor`,
        for the same reason `health` and `breaker` are: the replay executor and
        the test stubs satisfy the protocol without being subprocesses.
        """
        return self._harness

    def _binary(self) -> str:
        """The program to exec, resolved from a NAME the harness table holds.

        `cli_path` overrides it and is frozen against HTTP writes, which is the
        whole reason the harness selector can be editable: nothing an admin can
        type reaches this string.
        """
        configured = self._llm.cli_path_for(self._harness.key, self._harness.binary)
        return shutil.which(configured) or configured

    def _confinement(self) -> Confinement | None:
        """The filesystem this call may touch, or None to run unrestricted.

        Built from the environment the child will actually receive, not this
        process's. The two differ whenever a harness filters what it passes
        down, and a confinement that grants a directory the child was never
        told about locks it out of the one it does use.
        """
        if self._llm.confine == "off" or not self._harness.wants_confinement:
            return None
        plan = confinement_for(
            self._settings,
            self._harness,
            child_env=self._child_env(),
            binary=self._binary(),
        )
        # An empty plan means the child's home could not be established. There
        # is nothing safe to grant, so the call runs unrestricted and `health`
        # reports that, rather than being confined out of its own settings.
        return plan if plan.writes else None

    def _confined(self, argv: list[str]) -> list[str]:
        """Wrap the CLI in the restriction, when there is one to apply.

        OPPORTUNISTIC, and deliberately so. A kernel without Landlock is a
        deployment fact, not a misconfiguration, and refusing to think at all
        on one would take the app down for a defence in depth that the harness
        in front of it may not even need. What must never happen quietly is the
        reverse -- believing a call was confined when it was not -- so
        `health()` reports the state and `/api/health` carries it.

        The wrapper is `sys.executable` because Landlock is inherited across
        execve: the wrapper restricts ITSELF and then becomes the CLI, which
        cannot climb back out. It is a script path rather than `-m` so it needs
        nothing on `PYTHONPATH`, and a separate process rather than
        `preexec_fn`, which would run between fork and exec on an event loop
        that has a thread pool under it.
        """
        confinement = self._confinement()
        if confinement is None:
            return argv
        # A DRY RUN, not a version check. The ABI number says which bits
        # exist; only building the ruleset says the kernel will accept the one
        # this composes. Asking the wrong question is what makes the failure
        # silent: the wrapper refuses, the call dies with an exit code that
        # reads as a model failure, and every surface still says the sandbox
        # is working.
        if not confine.supported()[0]:
            return argv
        return [
            sys.executable,
            str(Path(confine.__file__)),
            ":".join(confinement.reads),
            ":".join(confinement.writes),
            "--",
            *argv,
        ]

    def _pinned_version(self) -> str:
        return self._llm.pinned_version_for(self._harness.key)

    def _child_env(self) -> dict[str, str]:
        """The environment the CLI runs under: inherited, minus, plus.

        INHERITED rather than an allowlist. `DISABLE_UPDATES` keeps a pinned
        binary pinned, `HOME` is where a rotating refresh token lives, and both
        arrive this way -- a curated list drops one of them the first time
        somebody adds a third.

        MINUS the names this vendor reads on its own, whenever a credential
        connected in the app supersedes them. Every instance that exists sets
        one of those variables, so without this an operator connects an account,
        the page reports success, and every call keeps using the deploy's key.

        The import is local for the same reason `_note_failure`'s is: the
        credential store is a service, and a module-level import of it here
        would put the service layer in `llm/`'s import graph.
        """
        return self._harness.child_env(dict(os.environ), self._credential())

    def _credential(self) -> Credential:
        """What this executor authenticates with, resolved once per use.

        Also the source of the confinement's writable home: the two must agree,
        because pointing the CLI at a configuration directory it is then not
        allowed to write is a failure with no useful message on either side.
        """
        if self._credential_override is not None:
            return self._credential_override

        from venona.services import harness_service

        return harness_service.credential_for(self._settings, self._harness, self._llm.billing_mode)

    async def _probe_with_code(self, argv: list[str], *, timeout: int) -> tuple[str, int]:
        """`_probe`, keeping the exit code, because it is not always a verdict.

        One vendor's sign-in status prints a meaningful answer and exits 1 when
        signed out. Treating non-zero as unusable there reports a working
        install as broken, so the driver reads both and decides.
        """
        try:
            return await self._run_with_code(argv, timeout=timeout)
        except Exception:  # noqa: BLE001 - the retry reports whatever is real
            await asyncio.sleep(0.25)
            return await self._run_with_code(argv, timeout=timeout)

    async def _run_with_code(self, argv: list[str], *, timeout: int) -> tuple[str, int]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._child_env(),
            )
        except OSError as exc:
            raise LlmError(f"could not exec {argv[0]}: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            raise LlmError(f"{argv[0]} timed out after {timeout}s") from None
        finally:
            await _reap(proc)
        # STDOUT, OR STDERR WHEN STDOUT IS EMPTY. One vendor prints its
        # sign-in status to stderr and exits 0, so reading only stdout gets a
        # blank string and reports a signed-in account as signed out -- with
        # nothing in the result to say why. Preferring stdout keeps the other
        # vendor's JSON clean of any warning printed alongside it.
        text = stdout.decode(errors="replace")
        if not text.strip():
            text = stderr.decode(errors="replace")
        return text, int(proc.returncode or 0)

    def _note_failure(self, error: str, *, now: float) -> None:
        """Count it, and say something outward the first time it opens.

        Once, not per call: an open circuit refuses every subsequent call, and a
        notification per refusal would be a hundred messages about one problem.
        """
        if not self._breaker.record_failure(error, now=now):
            return
        try:
            task = asyncio.get_running_loop().create_task(
                self._raise_unavailable(self._breaker.consecutive_failures, error)
            )
            _pending.add(task)
            task.add_done_callback(_pending.discard)
        except Exception:  # noqa: BLE001 - a notifier must never fail a call
            pass

    async def _raise_unavailable(self, failures: int, error: str) -> None:
        """Queue the notice, off the calling path.

        THE IMPORT AND THE CONNECTION BOTH STAY LOCAL. `llm/` is guarded by
        `lint-determinism`, which forbids constructing an httpx client here --
        `notify` owns its own clock, ids and transport for exactly that reason,
        and a module-level import of it would put an outbound subsystem in the
        import graph of the module that must not have one.

        Its own connection, briefly, because an executor holds none: the pool
        belongs to the API's request path and this fires from wherever the sixth
        consecutive failure happened to land.
        """
        from venona import notify
        from venona.db import core as db

        if notify.is_offline():
            return
        try:
            conn = db.connect(self._settings.database_url)
        except Exception:  # noqa: BLE001 - a notifier must never fail a call
            return
        try:
            notify.raise_notice(
                conn, "model.unavailable", failures=str(failures), error=error[:300]
            )
        finally:
            conn.close()

    # ── health ───────────────────────────────────────────────────────
    async def health(self) -> CliHealth:
        """Cached, single-flight. See `_health_uncached` for the probe itself.

        WHY THIS IS CACHED. Each probe spawns TWO `claude` subprocesses. The
        frontend polls `/health` every 30s, and every 3s while any light run is
        in flight -- so a single lookup produced roughly forty process spawns a
        minute, competing with the two-to-four `claude` processes the run itself
        wants. The answer changes when someone logs the CLI in or out, which is
        not a per-second event.

        `loop.time()` is monotonic and is not a nondeterminism source: health is
        never part of a run's output and is never replayed. That -- not the fact
        that `lint-determinism`'s grep happens not to match it -- is why it is
        allowed here.

        The lock makes it single-flight: without it, the first poll after expiry
        lets every concurrent caller through at once, which is precisely the
        thundering herd the cache is for.
        """
        loop = asyncio.get_running_loop()
        now = loop.time()
        if self._health_cache is not None:
            # STALE IS SERVED, refresh happens out of band. `/api/health` sits
            # on the page-load path, and awaiting the probe here means a page
            # load waits on two `claude` process spawns whenever the TTL happens
            # to have lapsed. The strip reads at most one TTL behind, which is
            # the deal the cache already made.
            if now - self._health_cache[0] >= HEALTH_TTL_SECONDS:
                self._schedule_health_refresh()
            return self._health_cache[1]

        async with self._health_lock:
            # Re-check: another caller may have refreshed it while we waited.
            now = loop.time()
            if self._health_cache is not None and now - self._health_cache[0] < HEALTH_TTL_SECONDS:
                return self._health_cache[1]
            probe = await self._health_uncached()
            self._health_cache = (loop.time(), self._with_confinement(probe))
            return self._health_cache[1]

    def _with_confinement(self, probe: CliHealth) -> CliHealth:
        """Stamp what the restriction actually managed, not what was asked for.

        Confinement is opportunistic -- a kernel without Landlock runs the call
        anyway -- so the difference between asked-and-got and asked-and-refused
        is invisible from anywhere else. This is the only place that difference
        is written down, which is why it is reported even when everything
        worked.
        """
        if self._confinement() is None:
            return replace(probe, confined=None, confinement_detail=None)
        ok, detail = confine.supported()
        return replace(probe, confined=ok, confinement_detail=detail)

    def _schedule_health_refresh(self) -> None:
        """At most one background refresh at a time; a done task is replaced."""
        if self._health_refresh_task is not None and not self._health_refresh_task.done():
            return

        async def refresh() -> None:
            async with self._health_lock:
                loop = asyncio.get_running_loop()
                if (
                    self._health_cache is not None
                    and loop.time() - self._health_cache[0] < HEALTH_TTL_SECONDS
                ):
                    return
                probe = await self._health_uncached()
                self._health_cache = (loop.time(), self._with_confinement(probe))

        self._health_refresh_task = asyncio.create_task(refresh())

    def invalidate_health(self) -> None:
        """Drop the cached probe. Called when a config save changes `cli_path`
        or `pinned_version` -- otherwise the strip reports the superseded binary
        for a minute after a pin bump, which reads as the save not having
        worked."""
        self._health_cache = None

    async def _health_uncached(self) -> CliHealth:
        """Cheap: `--version` and `auth status` are local, no model call.

        NOTE what this cannot tell you. Under `CLAUDE_CODE_OAUTH_TOKEN` -- the
        headless deployment path -- `claude auth status` reports
        `loggedIn: true` for ANY token, including an expired or garbage one, and
        omits `subscriptionType` entirely. Verified against 2.1.220. So a green
        CLI line means "a token is present", not "a token that works", and the
        real signal is a call failing with `api_error_status: 401`. That is what
        the circuit breaker and the failure webhook are for.
        """
        binary = self._binary()
        # `which` alone, deliberately. `_binary` has already resolved a name
        # through PATH, so a value that survives it either IS a path or is a
        # program that is not installed -- and stat-ing the filesystem from an
        # async function is a blocking call for an answer already in hand.
        if shutil.which(binary) is None and "/" not in binary:
            return CliHealth(ok=False, error=f"{binary} not found on PATH")

        spec = self._harness.probe()
        try:
            version = (await self._probe([binary, *spec.version], timeout=15)).strip()
        except Exception as exc:  # noqa: BLE001
            return CliHealth(ok=False, error=f"version probe failed: {exc}")

        # The exit code is not the verdict here; `_probe_with_code` says why.
        try:
            auth, returncode = await self._probe_with_code([binary, *spec.auth], timeout=20)
        except Exception as exc:  # noqa: BLE001
            return CliHealth(
                ok=False,
                version=version.split()[0] if version else None,
                error=f"sign-in probe failed: {exc}",
            )

        return self._harness.parse_probe(
            ProbeOutput(version=version, auth=auth, auth_returncode=returncode),
            pinned=self._pinned_version(),
        )

    # ── the call ─────────────────────────────────────────────────────
    async def call(
        self,
        task: str,
        *,
        user_content: str,
        schema: dict[str, Any],
        system_prompt: str = "",
        model: str = "sonnet",
        effort: str = "medium",
        tools: Sequence[str] = (),
        timeout: int | None = None,
        on_event: OnEvent | None = None,
        #: PER CALL, because the executor is shared by a whole LANE. The light
        #: lane holds three runs at once, so an instance attribute points
        #: whichever started last at its own stream while reporting the others'
        #: waits into it, and lets a cancel on one abort another's sleep. The
        #: instance fields stay as the fallback.
        on_wait: Callable[[float | None], Awaitable[None]] | None = None,
        wait_abort: Callable[[], bool] | None = None,
    ) -> LlmResult:
        key = cache_key_for(
            task=task,
            system_prompt=system_prompt,
            user_content=user_content,
            schema=schema,
            model=model,
            effort=effort,
            tools=tools,
        )
        # Keyed on the CALLER's schema, adapted after: contract C2 lets a driver
        # only SUBTRACT, so what it cannot express never rekeys a fixture.
        request = CallRequest(
            task=task,
            system_prompt=system_prompt,
            schema=self._harness.adapt_schema(schema),
            model=model,
            effort=effort,
            tools=tools,
            user_content=user_content,
        )
        argv = self._confined([self._binary(), *await self._harness.argv(request, self._files())])

        async with self._semaphore:
            loop = asyncio.get_running_loop()
            # Checked INSIDE the semaphore so an open circuit does not release a
            # slot every few milliseconds to callers that are about to be
            # refused, and outside the subprocess so an open circuit costs
            # nothing at all.
            await self._admit(loop, on_wait=on_wait, wait_abort=wait_abort)
            started = loop.time()
            try:
                stdout = await self._run_streaming(
                    argv,
                    timeout=timeout or timeout_for(self._llm, task),
                    user_content=user_content,
                    on_event=on_event,
                )
            except Exception as exc:
                self._note_failure(f"{type(exc).__name__}: {exc}", now=loop.time())
                raise
            duration_ms = int((loop.time() - started) * 1000)

        result = self._parse(stdout, cache_key=key, duration_ms=duration_ms, model=model)

        # The subscription reading, for a service that has to be asked for it.
        # Attached HERE so it reaches the ledger by the one path that already
        # records windows -- `usage.record_call` writes whatever the result
        # carries, and it does not care which of the two ways it got there.
        if result.rate_limits is None:
            limits = await self._read_quota()
            if limits is not None:
                result = replace(result, rate_limits=limits)

        # A ROTATION HAPPENS DURING A CALL, so the credential is kept right
        # after one (ADR 0234) -- the file the CLI just rewrote becomes a row
        # before anything can restart. `capture_if_changed` hashes two small
        # files and returns without a query unless they moved.
        if self._credential_override is None:
            from venona.services import harness_service

            harness_service.capture_if_changed(self._settings, self._harness)

        # A refusal is the model declining to answer, which is a real answer
        # about this prompt and says nothing about the provider. Only a
        # transport or API error counts against the circuit.
        if result.error and not result.refused:
            self._note_failure(result.error, now=asyncio.get_running_loop().time())
        else:
            self._breaker.record_success()

        # Recording shares the replay path deliberately: fixtures are the raw
        # stdout bytes, so the offline suite exercises exactly the parsing code
        # production uses. Hand-written fixtures would have been wrong in at
        # least two ways (stop_reason, the phantom haiku modelUsage entry).
        if self._record_dir is not None and result.error is None:
            self._write_fixture(
                task=task,
                key=key,
                stdout=stdout,
                model=model,
                effort=effort,
                tools=tools,
                user_content=user_content,
                system_prompt=system_prompt,
                schema=schema,
            )

        return result

    async def _admit(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        on_wait: Callable[[float | None], Awaitable[None]] | None = None,
        wait_abort: Callable[[], bool] | None = None,
    ) -> None:
        """Pass the breaker, waiting through a transient outage if allowed.

        Raises `BreakerOpen` exactly where the bare `check()` did, in three
        cases: the failure holding the circuit open is terminal (a 401 does not
        get better by waiting), the wait budget for this episode is spent, or
        the run that owns this call has been cancelled. Otherwise it sleeps
        until the next half-open probe is due and becomes that probe.

        Waiting holds the semaphore slot on purpose: nothing behind it could
        proceed either, and releasing it would only let the next caller arrive
        at the same wait.

        THE WAIT IS CLEARED AT EVERY EXIT. Two of the three raise, and a
        surface told a wait began and never told it ended goes on saying so:
        `stage_progress` payloads merge, so nothing later unsets it and the
        strip stays amber for the rest of the stage while the model is
        answering normally. `announced` keeps a call that never waited silent,
        which is the other half of the same rule.
        """
        # The call's own, or the instance's for a caller that set neither.
        on_wait = on_wait or self.on_wait
        wait_abort = wait_abort or self.wait_abort
        announced = False
        while True:
            try:
                self._breaker.check(now=loop.time())
            except BreakerOpen as exc:
                now = loop.time()
                budget_left = self._outage_wait - self._breaker.episode_age(now=now)
                if not self._breaker.transient or budget_left <= 0:
                    if self._waiting_since is not None:
                        self._waiting_since = None
                        log.error(
                            "claude circuit still open after waiting %ds; refusing calls: %s",
                            int(self._breaker.episode_age(now=now)),
                            self._breaker.last_error,
                        )
                    if announced and on_wait is not None:
                        await on_wait(None)
                    raise
                if wait_abort is not None and wait_abort():
                    if announced and on_wait is not None:
                        await on_wait(None)
                    raise BreakerOpen(f"{exc} (wait abandoned: the run was cancelled)") from None
                if self._waiting_since is None:
                    self._waiting_since = now
                    log.warning(
                        "claude circuit open on a transient error (%s); waiting up to %ds "
                        "for the provider to recover",
                        self._breaker.last_error,
                        int(budget_left),
                    )
                slice_ = max(
                    0.0,
                    min(self._breaker.retry_in(now=now), budget_left, WAIT_SLICE_SECONDS),
                )
                if on_wait is not None:
                    # NOT `slice_`. That is how long this loop sleeps before
                    # looking again, capped at ten seconds, so reporting it as
                    # "retrying in" repeats the same ten for half an hour: a
                    # countdown that never counts down and never fires. What a
                    # reader is owed is when the next attempt is actually due.
                    announced = True
                    await on_wait(max(0.0, min(self._breaker.retry_in(now=now), budget_left)))
                await asyncio.sleep(slice_)
                continue
            if self._waiting_since is not None:
                log.warning(
                    "claude circuit half-open after %ds; probing",
                    int(loop.time() - self._waiting_since),
                )
                self._waiting_since = None
                # The wait is over the moment the circuit lets a call through,
                # and a surface still showing "waiting" while the model answers
                # is the same lie in the other direction.
                if announced and on_wait is not None:
                    await on_wait(None)
            return

    def _write_fixture(
        self,
        *,
        task: str,
        key: str,
        stdout: str,
        model: str,
        effort: str,
        tools: Sequence[str],
        user_content: str,
        system_prompt: str,
        schema: dict[str, Any],
    ) -> None:
        directory = self._record_dir / task
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{key}.json").write_text(stdout)

        # A sidecar so a human can tell what a hex-named file is without
        # parsing it. The full user_content is not stored -- it can be large --
        # but its hash is, so a mismatch is diagnosable.
        (directory / f"{key}.request.json").write_text(
            json.dumps(
                {
                    "task": task,
                    "model": model,
                    "effort": effort,
                    "tools": list(tools),
                    "system_prompt_sha": hashlib.sha256(system_prompt.encode()).hexdigest(),
                    "user_content_sha": hashlib.sha256(user_content.encode()).hexdigest(),
                    "user_content_preview": user_content[:400],
                    "schema_keys": sorted((schema.get("properties") or {}).keys()),
                },
                indent=2,
                sort_keys=True,
            )
        )

    def _files(self) -> _PromptFiles:
        """Owned here rather than by a driver, so `Harness.argv` receives paths
        that already exist and the location stays in one place. See
        `_PromptFiles` for why argv cannot carry the payload."""
        return _PromptFiles(self._settings.data_dir / "cache", self._prompt_file_ok)

    async def _prompt_file_ok(self) -> bool:
        """Whether `--system-prompt-file` parses, probed once per process.

        The flag is absent from `claude --help`, so this is the only way to know
        it is still there after an upgrade. An unknown flag says so explicitly;
        any other failure means it parsed and something later objected.
        """
        if self._supports_prompt_file is not None:
            return self._supports_prompt_file
        proc = await asyncio.create_subprocess_exec(
            self._binary(),
            "--system-prompt-file",
            "/nonexistent-venona-probe",
            "--help",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._child_env(),
        )
        try:
            _, stderr = await proc.communicate()
        finally:
            await _reap(proc)
        self._supports_prompt_file = b"unknown option" not in stderr.lower()
        return self._supports_prompt_file

    async def _run_streaming(
        self,
        argv: list[str],
        *,
        timeout: int,
        user_content: str,
        on_event: OnEvent | None,
    ) -> str:
        """Surface each session message as it arrives; return the whole stdout.

        stdin and stdout are pumped by SEPARATE tasks rather than
        `communicate()`. They cannot be sequenced: `user_content` is up to ~192k
        and the CLI starts emitting `system/init` immediately, so writing the
        whole prompt before reading a byte deadlocks once stdout's pipe buffer
        (64k on Linux) fills. `communicate()` gets this right but only hands the
        output back at the end, which is the one thing streaming cannot accept.

        A raised `on_event` is swallowed on purpose. The transcript is
        observability; a UI consumer that throws must not fail a model call that
        otherwise succeeded.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._child_env(),
            )
        except OSError as exc:
            # NOT `argv[0]`, which is the confinement wrapper's interpreter on
            # the confined path. This message reaches a reader on
            # /admin/runs/:id, and naming a Python binary there is both wrong
            # and the internal vocabulary that surface is not allowed to use.
            raise LlmError(f"could not run {self._harness.label}: {exc}") from exc

        lines: list[str] = []
        errbuf: deque[bytes] = deque(maxlen=STDERR_KEEP_LINES)

        async def pump_stdin() -> None:
            assert proc.stdin is not None
            # `RuntimeError` is uvloop's spelling of the same condition: the
            # transport closed under us because the CLI exited before reading
            # ("the handler is closed"). See `_run`.
            try:
                proc.stdin.write(user_content.encode())
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError):
                pass  # the CLI exited early; the result line (or lack of) decides
            finally:
                with contextlib.suppress(BrokenPipeError, ConnectionResetError, RuntimeError):
                    proc.stdin.close()

        async def handle_line(raw: bytes) -> None:
            line = raw.decode(errors="replace")
            lines.append(line)
            if on_event is None:
                return
            stripped = line.strip()
            if not stripped:
                return
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                return
            # The result line is the return value, not a transcript entry --
            # and which line that is, is a fact about one vendor's stream.
            if isinstance(event, dict) and self._harness.is_transcript_event(event):
                with contextlib.suppress(Exception):
                    await on_event(event)

        async def pump_stderr() -> None:
            """Drained CONCURRENTLY, not after stdout closes.

            stderr is a pipe with the same 64k buffer stdout has. A CLI that
            logs while it works -- a retry, a reconnect, a warning -- fills it,
            blocks on the write, and therefore stops writing stdout too. The
            symptom is the call hanging until its timeout with nothing to show
            for it, and the cause invisible, because the diagnosis is sitting
            in the buffer nobody is reading.
            """
            if proc.stderr is None:
                return
            while True:
                raw = await proc.stderr.read(65536)
                if not raw:
                    return
                errbuf.extend(raw.splitlines(keepends=True))

        async def pump_stdout() -> None:
            """Split lines by hand instead of `readline()`.

            `StreamReader.readline` raises `ValueError` once a line passes the
            reader's 64k limit rather than returning a partial line, and the
            final `{"type":"result"}` envelope carries the WHOLE structured
            output -- a brief or a dense analyze result clears 64k easily. That
            cost a completed run its brief: the CLI had already emitted the
            tool_use and exited 0, and the envelope was discarded in the client
            three minutes and several dollars after the model finished the work.
            `read(n)` is not subject to the limit (it bounds `readline`/
            `readuntil` and the transport's pause threshold, nothing else), so
            buffering chunks and cutting on newlines has no ceiling at all.
            """
            assert proc.stdout is not None
            buf = bytearray()
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    # A final line with no trailing newline is still a line.
                    if buf:
                        await handle_line(bytes(buf))
                    return
                buf += chunk
                while (nl := buf.find(b"\n")) != -1:
                    raw = bytes(buf[: nl + 1])
                    del buf[: nl + 1]
                    await handle_line(raw)

        try:
            await asyncio.wait_for(
                asyncio.gather(pump_stdin(), pump_stdout(), pump_stderr()), timeout=timeout
            )
            stderr = b"".join(errbuf)
            await proc.wait()
        except TimeoutError:
            raise LlmError(f"{self._harness.label} timed out after {timeout}s") from None
        except OSError as exc:
            raise LlmError(f"{self._harness.label} i/o failed: {exc}") from exc
        finally:
            # EVERY exit, not just the two named above. CANCELLATION is the one
            # that costs money: a sibling raising makes `asyncio.gather` cancel
            # the rest and `wait_for` raise `CancelledError` here, and `_reap`
            # says what a child nobody kills goes on spending.
            await _reap(proc)

        stdout = "".join(lines)
        if proc.returncode != 0 and not stdout:
            detail = stderr.decode(errors="replace").strip()[:400]
            raise LlmError(f"{self._harness.label} exited {proc.returncode}: {detail}")

        return stdout

    async def _probe(self, argv: list[str], *, timeout: int) -> str:
        """A health probe: `_run`, retried once.

        One failed spawn is not an outage. `/api/health` reads `ok: false` as
        "claude down" and the Runs page disables every control that spends on
        it, so a single transport hiccup under a busy loop painted a working
        instance red for a full health TTL. A second attempt a quarter-second
        later separates that from a binary that is actually gone.
        """
        try:
            return await self._run(argv, timeout=timeout)
        except Exception:  # noqa: BLE001 - the retry reports whatever is real
            await asyncio.sleep(0.25)
            return await self._run(argv, timeout=timeout)

    async def _run(self, argv: list[str], *, timeout: int, user_content: str = "") -> str:
        """`user_content` goes over stdin, never argv -- MAX_ARG_STRLEN, C2.

        `OSError` is caught because exec-time failures (argv too long, binary
        vanished mid-run, fd exhaustion) happen BEFORE the process exists, so
        nothing downstream classifies them. Uncaught, they escape as a bare
        OSError, miss `is_retryable`, and fail a run that should have degraded.

        NO STDIN PIPE WITHOUT CONTENT. `claude --version` and `auth status`
        exit without reading stdin, and `communicate(b"")` still WRITES to it.
        Under uvloop, once the child has exited and the pipe transport closed
        -- which, on a loop busy pumping four streaming calls, happens before
        the write is scheduled -- that write raises `RuntimeError("unable to
        perform operation on <WriteUnixTransport closed=True ...>; the handler
        is closed")`, which `communicate()` does not swallow. The probe then
        "failed", `/api/health` said the CLI was down, and the Runs page
        disabled itself for the length of every concurrent run. Measured live
        on 2026-08-22 with a research run and two lookups in flight.
        """
        stdin = asyncio.subprocess.PIPE if user_content else asyncio.subprocess.DEVNULL
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._child_env(),
            )
        except OSError as exc:
            raise LlmError(f"could not exec {argv[0]}: {exc}") from exc

        if user_content:
            assert proc.stdin is not None
            # The same tolerance `_run_streaming` has: a child that exits
            # before reading is reported by its result line, not by the pipe.
            with contextlib.suppress(BrokenPipeError, ConnectionResetError, RuntimeError):
                proc.stdin.write(user_content.encode())
                await proc.stdin.drain()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError, RuntimeError):
                proc.stdin.close()

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            raise LlmError(f"claude timed out after {timeout}s") from None
        except OSError as exc:
            raise LlmError(f"claude i/o failed: {exc}") from exc
        finally:
            await _reap(proc)

        if proc.returncode != 0 and not stdout:
            detail = stderr.decode(errors="replace").strip()[:400]
            raise LlmError(f"{self._harness.label} exited {proc.returncode}: {detail}")

        return stdout.decode(errors="replace")

    def _parse(
        self, stdout: str, *, cache_key: str, duration_ms: int, model: str = ""
    ) -> LlmResult:
        result = self._harness.parse(stdout, cache_key=cache_key, duration_ms=duration_ms)
        return self._priced(result, model)

    async def _request_response(
        self, argv: list[str], *, request: str, done_when: str, timeout: int
    ) -> str:
        """Write a request, read until the answer, then stop the child.

        NOT `_run`, and the difference is the whole reason this exists. `_run`
        writes stdin and CLOSES it, which is right for a CLI that takes a
        prompt and answers. A request/response server reads until EOF and exits
        when it gets one, so closing the pipe to mean "that is the whole
        request" cancels the request instead -- measured 2026-09-10, and the
        symptom is a reply to the handshake and nothing after it.

        So the pipe stays open, the read ends on `done_when`, and the child is
        stopped rather than waited for. Nothing here spends; the reaping is the
        same as every other spawn's.
        """
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self._child_env(),
        )
        lines: list[str] = []
        try:

            async def pump() -> None:
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(request.encode())
                await proc.stdin.drain()
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        return
                    line = raw.decode(errors="replace")
                    lines.append(line)
                    if done_when and done_when in line:
                        return

            await asyncio.wait_for(pump(), timeout=timeout)
        except (TimeoutError, OSError, RuntimeError):
            # A reading nobody gets is not a failed call. The caller records
            # nothing and the strip says nothing was measured.
            pass
        finally:
            await _reap(proc)
        return "".join(lines)

    async def _read_quota(self) -> RateLimits | None:
        """Ask the service how much of the subscription is left.

        Only for a service that has to be ASKED. The other reports its windows
        in the envelope of every call it answers, so `quota()` is None there and
        this never runs.

        CACHED, and failures are cached too. The reading is a spawn, and the
        thing it feeds is a strip that already reads one TTL behind by design;
        a service that cannot answer must not be re-asked once per model call
        for the length of a run.
        """
        spec = self._harness.quota()
        if spec is None:
            return None

        loop = asyncio.get_running_loop()
        if self._quota_cache is not None and loop.time() - self._quota_cache[0] < QUOTA_TTL_SECONDS:
            return self._quota_cache[1]

        limits: RateLimits | None = None
        try:
            stdout = await self._request_response(
                [self._binary(), *spec.argv],
                request=spec.stdin,
                done_when=spec.done_when,
                timeout=30,
            )
            limits = self._harness.parse_quota(stdout)
        except Exception:  # noqa: BLE001 - a reading nobody gets is not a failed call
            # DELIBERATELY SWALLOWED. This runs beside a call that has already
            # succeeded, and a run must never fail because the app could not
            # draw a progress bar. The strip renders "nothing measured", which
            # is what it renders before the first reading anyway.
            limits = None
        self._quota_cache = (loop.time(), limits)
        return limits

    def _priced(self, result: LlmResult, model: str = "") -> LlmResult:
        """What `cost_usd` is, and what it MEANS, on this row.

        Four kinds of number share the column and summing across them is
        meaningless, so each row records which it carries. A subscription's
        figure is what the same tokens would have cost at published rates and
        binds nothing (ADR 0180); a metered provider's is an invoice; a token
        count multiplied by a table this repo maintains is an estimate; and a
        service that reports neither says so rather than reporting zero, which
        would read as free.
        """
        metered = self._llm.billing_mode == "api"
        if result.cost_usd:
            return replace(result, cost_basis="measured" if metered else "list_price")

        # THE PRICE TABLE APPLIES ON A SUBSCRIPTION TOO. A service that sends
        # no figure of its own leaves this at zero, and zero is not a smaller
        # number than the other service's -- it is a different claim, that the
        # work was free. It also makes the figure unrecoverable later: the
        # price quoted in front of `Look up all` is an average over past runs
        # that SPENT, so a service reporting nothing can never accumulate a
        # sample and the modal quotes a constant forever.
        #
        # The model ASKED FOR, because a service that prices in tokens does
        # not name the model back. The other reports its own usage map, and
        # has already reported a figure by the time this runs.
        priced = self._harness.cost_for(model or next(iter(result.models), ""), result.usage)
        if priced is None:
            return replace(result, cost_basis="none")
        return replace(result, cost_usd=priced, cost_basis="computed")


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Make sure the child is gone before the caller leaves.

    Idempotent by design: on the ordinary path `proc.wait()` has already
    returned, `returncode` is set, and this does nothing. It matters on every
    other path -- a timeout, an i/o error, and above all a CANCELLATION, where
    the coroutine unwinds without anyone having decided to stop the subprocess.

    A `claude` process that outlives its caller is not idle. It runs its model
    call to completion, holds a slot the executor's semaphore believes is free,
    and spends against the subscription with nothing left to record the spend --
    the ledger write is in the code that was cancelled.

    `ProcessLookupError` is the benign race: the child exited between the check
    and the signal.
    """
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(Exception):
        await proc.wait()


def parse_envelope(
    stdout: str, *, cache_key: str = "", duration_ms: int = 0, harness: Harness | None = None
) -> LlmResult:
    """Shared by the live executor and the replay executor so fixtures exercise
    exactly the parsing path production uses.

    `harness` defaults to Claude Code because every fixture recorded before
    there were two was recorded from it, and a test that hands this function an
    envelope without saying whose it is means that one.
    """
    driver = harness if harness is not None else resolve("claude_code")
    return driver.parse(stdout, cache_key=cache_key, duration_ms=duration_ms)


class ReplayExecutor:
    """Offline backend. Reads recorded envelopes from fixtures/llm/<task>/.

    A missing fixture is a hard failure, never a fall-through to a live call --
    that fall-through is how an "offline" suite quietly starts spending money.
    """

    def __init__(self, settings: Settings, *, harness: Harness | None = None) -> None:
        # The harness decides both WHERE a fixture lives and HOW it parses. A
        # recorded envelope is one vendor's shape, so replaying it through
        # another's parser reports a truncated file as a refusal.
        self._harness = harness if harness is not None else resolve(settings.llm.harness)
        self._root: Path = settings.fixtures_dir / "llm" / self._harness.fixture_root

    async def call(
        self,
        task: str,
        *,
        user_content: str,
        schema: dict[str, Any],
        system_prompt: str = "",
        model: str = "sonnet",
        effort: str = "medium",
        tools: Sequence[str] = (),
        timeout: int | None = None,
        on_event: OnEvent | None = None,
        #: Accepted and ignored: replay never waits. See `CliExecutor.call`.
        on_wait: Callable[[float | None], Awaitable[None]] | None = None,
        wait_abort: Callable[[], bool] | None = None,
    ) -> LlmResult:
        key = cache_key_for(
            task=task,
            system_prompt=system_prompt,
            user_content=user_content,
            schema=schema,
            model=model,
            effort=effort,
            tools=tools,
        )
        path = self._root / task / f"{key}.json"
        if not path.exists():
            raise LlmError(
                f"no fixture for task={task} key={key}\n"
                f"  expected: {path}\n"
                f"  record it with: make record"
            )
        # The quota reading is dropped for the same reason replay reports no CLI
        # health: a fixture recorded weeks ago would otherwise assert a window
        # utilization as though it were now, and the offline suite would show a
        # subscription filling up that nothing is using.
        return replace(self._harness.parse(path.read_text(), cache_key=key), rate_limits=None)


def build_executor(
    settings: Settings,
    *,
    record: bool = False,
    concurrency: int | None = None,
    outage_wait_seconds: float | None = None,
) -> LlmExecutor:
    """`record` OR the env var. `venona run --record` sets the parameter; the
    env var is how `make record` and the one-shot `venona analyze` ask for the
    same thing. Recording only half a run -- HTTP but not the model envelopes --
    produces fixtures that cannot replay, which is worse than not recording.

    `concurrency` and `outage_wait_seconds` build the light lane's second
    instance. Both are ignored by `ReplayExecutor`, which makes no subprocesses
    and has nothing to throttle or wait for.
    """
    harness = resolve(settings.llm.harness)
    if settings.llm.backend == "replay":
        return ReplayExecutor(settings, harness=harness)

    # THE REAL METERED-BILLING GUARD. `main.py` refuses a key in the server's
    # environment at boot, but billing mode is editable over HTTP, so a boot
    # check cannot hold: switch to subscription after starting in API mode and
    # every call runs on metered billing with nothing left to notice. Here both
    # the mode and the environment are re-read, and an executor is rebuilt
    # whenever either changes.
    _refuse_unservable(settings, harness)

    record = record or os.environ.get("VENONA_LLM_RECORD") == "1"
    return CliExecutor(
        settings,
        harness=harness,
        record=record,
        concurrency=concurrency,
        outage_wait_seconds=outage_wait_seconds,
    )


def _refuse_unservable(settings: Settings, harness: Harness) -> None:
    """A pairing this instance cannot serve is a refusal, not a run that fails.

    Both halves matter, and both are about the same scar (ADR 0216, which keeps
    0001's finding): a key in the environment silently moves every call onto
    metered billing. That is as true of the other vendor's key as of
    `ANTHROPIC_API_KEY`, so the names come from `Harness.metered_env` rather
    than from a literal, and a harness added later cannot forget to be covered.
    """
    from venona.services import harness_service

    mode = settings.llm.billing_mode
    if mode not in harness.billing_modes:
        raise LlmError(
            f"{harness.label} cannot be used on a {mode} plan. "
            f"Choose another way to pay for it, or another service."
        )

    present = sorted(n for n in harness.metered_env if os.environ.get(n))
    if mode == "subscription" and present and not harness_service.has_stored(settings, harness):
        raise LlmError(
            f"{', '.join(present)} is set in the server's environment while "
            f"{harness.label} is on a subscription plan, so every call would be "
            f"billed per token instead. Remove it, or switch to an API key plan."
        )
