"""Claude Code: argv, envelope, probe, transcript and error vocabulary.

Verified behaviours this driver encodes (measured against claude 2.1.220, not
inferred). Contract C2 tabulates the full list; these are the ones the shape of
this module only makes sense against:

  * `--permission-mode dontAsk` DENIES WebSearch/WebFetch while still exiting 0
    with `is_error: false`. The denial shows up only in `permission_denials`.
    Tool-using calls must pass `--allowedTools`, and a non-empty
    `permission_denials` is a hard failure -- otherwise a tool-using stage
    silently returns "found nothing" forever (ADR 0002).
  * `stop_reason` is "tool_use" on SUCCESS when `--json-schema` is used.
    Gating on "end_turn" rejects every good call.
  * `modelUsage` carries a phantom haiku entry on every invocation. Cost is the
    sum over that map, not the requested model's slice.
  * `--bare` forces ANTHROPIC_API_KEY auth, bypassing the subscription.
  * `--exclude-dynamic-system-prompt-sections` is a documented no-op once the
    system prompt is replaced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from venona.llm import budget as budget_mod
from venona.llm import transcript
from venona.llm.harness import (
    WEB_FETCH,
    WEB_SEARCH,
    CallRequest,
    Credential,
    FileCache,
    LoginDriver,
    ProbeOutput,
    ProbeSpec,
    Verdict,
    matches_pin,
)
from venona.llm.types import (
    HarnessHealth,
    LlmResult,
    PermissionDenied,
    PlanHelp,
    QuotaWindow,
    RateLimits,
    Usage,
)


@dataclass(frozen=True)
class ClaudeCode:
    key: str = "claude_code"
    label: str = "Claude Code"
    binary: str = "claude"
    fixture_root: str = ""
    #: Reported on the stream this driver already reads, at no extra cost.
    reports_usage_windows: bool = True
    #: `--tools ""` on every task but the ones that ask for the web, so there is
    #: no filesystem tool here to confine: the restriction would buy nothing and
    #: could only break an install that works (ADR 0219).
    wants_confinement: bool = False

    @property
    def aliases(self) -> dict[str, str]:
        """Tier names to CLI aliases, and effort names to themselves.

        The CLI's own aliases (`sonnet`, `opus`, `haiku`) are absent on purpose:
        pass-through already returns them unchanged, and listing them would
        imply this table is the set of legal values, which is the allowlist
        `config_service._check_model_names` argues against.

        The OTHER services' model names are here for the opposite reason. Pass-
        through would hand `gpt-5.6-sol` to Anthropic, so an instance that had
        tuned its models would break on the switch rather than carry its tiers
        across. `tests/test_harness_aliases.py` holds every direction of that.
        """
        return {
            "fast": "haiku",
            "standard": "sonnet",
            "deep": "opus",
            "gpt-5.6-luna": "haiku",
            "gpt-5.6-sol": "sonnet",
            "gpt-5.6-terra": "opus",
            "grok-4.6": "sonnet",
        }

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset({WEB_SEARCH, WEB_FETCH})

    @property
    def billing_modes(self) -> frozenset[str]:
        return frozenset({"subscription", "api"})

    @property
    def secret_env(self) -> frozenset[str]:
        """`CLAUDE_CODE_OAUTH_TOKEN` is set on every instance that exists, so a
        credential connected in the app does nothing at all unless these names
        are cleared from the child (`tests/test_harness_credentials.py`).
        `ANTHROPIC_API_KEY` is here for the other half of the same job: it is
        what `main._refuse_metered_billing` refuses on a subscription.
        """
        return frozenset({"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"})

    @property
    def metered_env(self) -> frozenset[str]:
        """`CLAUDE_CODE_OAUTH_TOKEN` is deliberately absent, though `secret_env`
        carries it: it is a SUBSCRIPTION credential and every deployment sets
        it, so calling it metered refuses to boot the entire fleet (ADR 0216)."""
        return frozenset({"ANTHROPIC_API_KEY"})

    @property
    def home_env(self) -> str:
        return "CLAUDE_CONFIG_DIR"

    @property
    def home_dirname(self) -> str:
        return ".claude"

    @property
    def credential_paths(self) -> tuple[str, ...]:
        """MEASURED: `~/.claude/.credentials.json`, 2,676 bytes in a 1.5 GB
        directory (2026-09-14).

        `.claude.json` is deliberately absent -- it is this CLI's settings, and
        keeping it would carry a workstation's preferences onto an instance
        under the name of a credential.
        """
        return (".credentials.json",)

    def plan_help(self, billing_mode: str) -> PlanHelp:
        """Both plans are pasteable here, and they are opposites.

        `setup-token` is an interactive command on a machine with a browser, so
        the token is minted elsewhere and carried here. A SUBSCRIPTION reaches
        this vendor as a value; the drivers that keep a session in their own
        files have nothing a page could hand over.
        """
        if billing_mode == "api":
            return PlanHelp(
                credential="an API key",
                how=(
                    "Create one in the Anthropic Console, under API keys, and paste it "
                    "here. It is charged per token and is separate from a Claude "
                    "subscription."
                ),
                pasteable=True,
                metered=True,
            )
        return PlanHelp(
            credential="a long-lived token",
            how=(
                "On a computer with a browser, run `claude setup-token` and sign in. "
                "It prints a token that starts with sk-ant-oat. Paste that here. The "
                "work comes out of the plan you signed in with, not a bill per token."
            ),
            pasteable=True,
            metered=False,
            command="claude setup-token",
        )

    def env_for(self, billing_mode: str) -> str | None:
        """A variable for BOTH modes, unlike the drivers that leave a
        subscription in their own files and have nothing to name here."""
        return "ANTHROPIC_API_KEY" if billing_mode == "api" else "CLAUDE_CODE_OAUTH_TOKEN"

    # ── invocation ───────────────────────────────────────────────────

    async def argv(self, req: CallRequest, files: FileCache) -> list[str]:
        # `user_content` is deliberately NOT here -- it goes on stdin. A single
        # argv element is capped at MAX_ARG_STRLEN (131072 on Linux, measured
        # 2026-08-06) and an article-dense brief prompt crosses it: the failure
        # is an OSError from the exec itself, before `claude` ever starts, so it
        # cannot be retried, budgeted or degraded to `succeeded_partial`. It is
        # data-dependent too -- a brief ran ~55k (2026-08-06) and worked right up
        # until a day with a dozen long articles -- and capping the content would
        # "fix" it by truncating the evidence the brief exists to carry.
        argv = [
            "-p",
            "--json-schema",
            json.dumps(req.schema, separators=(",", ":")),
            # `stream-json` rather than `json` so the run can be WATCHED. The
            # final `{"type":"result"}` line carries every field contract C2
            # gates on -- verified field-by-field against 2.1.220 -- so the
            # parse reads the same envelope either format produces.
            #
            # `--verbose` is NOT optional. Without it the CLI refuses outright:
            # "When using --print, --output-format=stream-json requires
            # --verbose". It changes stdout framing, not the envelope.
            #
            # Deliberately NOT `--include-partial-messages`: every event on this
            # stream becomes a persisted `run_events` row (contract C6), and
            # token-level chunks would multiply those by ~100x per call
            # (2026-08-06) for a transcript nobody reads back.
            "--output-format",
            "stream-json",
            "--verbose",
            # Subagent text surfaces as messages with `parent_tool_use_id`, so
            # delegated work is visible rather than a silent gap in the stream.
            "--forward-subagent-text",
            "--model",
            req.model,
            "--effort",
            req.effort,
            # Keep the operator's personal Claude Code config out of pipeline
            # runs: CLAUDE.md, plugins, hooks and MCP servers would all make
            # results irreproducible and break prefix caching.
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--permission-mode",
            "dontAsk",
        ]

        if req.tools:
            argv += ["--tools", ",".join(req.tools)]
            # Without this, dontAsk denies the tools and the call "succeeds"
            # with an empty answer.
            argv += ["--allowedTools", " ".join(req.tools)]
        else:
            argv += ["--tools", ""]

        if req.system_prompt:
            # Keeps argv small and caches identically. The flag is absent from
            # `claude --help` but works; the executor probes once per process and
            # hands back None if the CLI ever drops it, and the prompt goes
            # inline instead.
            path = await files.materialize(req.system_prompt, kind="system_prompts", suffix=".md")
            argv += (
                ["--system-prompt-file", str(path)]
                if path is not None
                else ["--system-prompt", req.system_prompt]
            )

        return argv

    def child_env(self, base: dict[str, str], cred: Credential) -> dict[str, str]:
        """Inherited minus plus, which is safe HERE and would not be everywhere.

        This vendor holds no filesystem tool on any pipeline call -- `--tools ""`
        on all but the ones that ask for the web -- so a prompt cannot talk it
        into reading the environment it was handed. The service that can gets an
        allowlist instead (`codex.child_env`).
        """
        env = {k: v for k, v in base.items() if k not in cred.suppress}
        env.update(cred.env)
        if cred.home is not None:
            env[self.home_env] = str(cred.home)
        return env

    def adapt_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Claude Code validates full JSON Schema, so there is nothing to drop.

        The identity here is the reference point for what a driver may do at
        all: SUBTRACT a keyword it cannot express, never add, rename or reshape.
        A driver that returns something semantically different from what the
        caller wrote has moved the contract, and the caller cannot see it.
        """
        return schema

    def cost_for(self, model: str, usage: Any) -> float | None:
        """None: this vendor's envelope carries the figure itself."""
        return None

    # ── the envelope ─────────────────────────────────────────────────

    def parse(self, stdout: str, *, cache_key: str = "", duration_ms: int = 0) -> LlmResult:
        """Shared by the live executor and the replay executor so fixtures
        exercise exactly the parsing path production uses.

        The quota reading is attached HERE rather than at each result
        construction below, so an error envelope carries it too. A refused or
        rate-limited call is precisely when the reading is worth having, and
        threading it through every branch is how one branch ends up missing it.
        """
        result = _envelope_result(stdout, cache_key=cache_key, duration_ms=duration_ms)
        limits = _rate_limits(stdout)
        return replace(result, rate_limits=limits) if limits is not None else result

    def classify(self, error: str | None) -> Verdict:
        """Transient conditions are matched loosely because the CLI surfaces
        them as strings rather than as typed errors.

        Loose matching is safe HERE and would not be on every vendor: Claude
        Code's error text is short and structured ("401: Failed to authenticate.
        API Error: 401 OAuth access token is invalid."), so a numeric token in
        it means what it looks like. A vendor that appends request ids and trace
        headers to the same string turns this into a coin flip, which is why
        classification is a harness method rather than a shared helper.
        """
        return "transient" if budget_mod.is_retryable(error) else "terminal"

    # ── the live transcript (contract C6) ────────────────────────────

    def is_transcript_event(self, raw: dict[str, Any]) -> bool:
        """Every stream line except the final envelope.

        The result line is the call's RETURN VALUE, not a transcript entry, and
        forwarding it would persist a whole brief into `run_events`.
        """
        return raw.get("type") != "result"

    def summarize(self, raw: dict[str, Any], *, task: str) -> dict[str, Any] | None:
        return transcript.summarize(raw, task=task)

    # ── health ───────────────────────────────────────────────────────

    def probe(self) -> ProbeSpec:
        return ProbeSpec(version=("--version",), auth=("auth", "status"))

    def quota(self) -> None:
        """Nothing to ask. This service reports its windows in the envelope of
        every call it answers, so `parse` has already read them by the time
        anything would think to go looking.

        Spelled out rather than inherited: the protocol is structural, so a
        default in its body reaches no implementer.
        """
        return None

    def parse_quota(self, stdout: str) -> None:
        return None

    def parse_probe(self, out: ProbeOutput, *, pinned: str) -> HarnessHealth:
        """`claude --version` prints the number first, then the product name
        (`2.1.220 (Claude Code)`). `claude auth status` prints JSON.

        Both shapes are this vendor's, and neither is guessable from the other:
        a sibling CLI prints its own name first and its status as prose, so a
        shared parse would report a working install as a permanent pin mismatch.
        """
        version = out.version.strip().split()[0] if out.version.strip() else None
        try:
            auth = json.loads(out.auth)
        except (json.JSONDecodeError, TypeError):
            return HarnessHealth(
                ok=False,
                version=version,
                version_matches_pin=matches_pin(version, pinned),
                error="could not read the sign-in status",
            )
        if not isinstance(auth, dict):
            return HarnessHealth(
                ok=False,
                version=version,
                version_matches_pin=matches_pin(version, pinned),
                error="could not read the sign-in status",
            )
        logged_in = bool(auth.get("loggedIn"))
        return HarnessHealth(
            ok=logged_in,
            version=version,
            logged_in=logged_in,
            account=auth.get("email"),
            subscription=auth.get("subscriptionType"),
            version_matches_pin=matches_pin(version, pinned),
            error=None if logged_in else "not signed in",
        )

    def login(self) -> LoginDriver | None:
        """No flow that works without a browser on the same machine: `claude
        setup-token` mints exactly what a headless instance needs and it is
        interactive. The admin page takes the minted token and verifies it
        against this vendor's own probe before storing it. ADR 0238.
        """
        return None


CLAUDE_CODE = ClaudeCode()


# ── envelope internals ───────────────────────────────────────────────


def _result_line(stdout: str) -> dict[str, Any]:
    """The envelope, from either output format.

    `--output-format json` emits one object. `--output-format stream-json`
    emits JSONL whose LAST `{"type":"result"}` line is that same object --
    verified field-by-field against 2.1.220.

    Both are accepted because `cache_key_for` is a function of the call
    arguments alone (contract C2): an envelope under `fixtures/llm/` round-trips
    whichever format recorded it, and the transport can move without rekeying
    a single file.

    Scans in reverse because the result line is always last and is the only line
    that matters; earlier lines are transcript.
    """
    stripped = stdout.strip()
    if not stripped:
        raise json.JSONDecodeError("empty output", stdout, 0)

    if stripped[0] == "{" and "\n" not in stripped:
        return json.loads(stripped)

    for line in reversed(stripped.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return event

    # Not JSONL with a result line -- fall back to whole-body parse so the
    # error message names the real problem rather than "no result line".
    return json.loads(stripped)


def _epoch_to_iso(seconds: Any) -> str | None:
    """`resetsAt` arrives as unix epoch SECONDS; every timestamp on our wire is
    ISO-8601 UTC with `Z` (CLAUDE.md conventions). Converting at this boundary
    is what keeps the string comparisons elsewhere in the system valid."""
    try:
        stamp = datetime.fromtimestamp(float(seconds), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return stamp.isoformat().replace("+00:00", "Z")


def _rate_limits(stdout: str) -> RateLimits | None:
    """The last `rate_limit_event` in a stream-json transcript, or None.

    Scans in reverse for the same reason `_result_line` does: the newest reading
    is the true one, and everything before it is superseded. A `--output-format
    json` envelope is a single object with no transcript, so it yields None by
    construction and no fixture recorded that way is rekeyed.

    The event carries EVERY window in `unifiedWindows`, so one event is several
    rows. The top-level `rateLimitType`/`utilization` pair repeats whichever of
    those windows is closest to binding; recording it as its own window would
    double-count that one.
    """
    stripped = stdout.strip()
    if not stripped or "rate_limit_event" not in stripped:
        return None

    for line in reversed(stripped.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "rate_limit_event":
            continue

        info = event.get("rate_limit_info")
        if not isinstance(info, dict):
            return None

        # Checked rather than assumed: this is the one field whose shape a CLI
        # upgrade could change under us, and the cost of being wrong must be the
        # reading, never the call.
        unified = info.get("unifiedWindows")
        if not isinstance(unified, dict):
            unified = {}

        windows: list[QuotaWindow] = []
        for name, window in unified.items():
            if not isinstance(window, dict):
                continue
            resets_at = _epoch_to_iso(window.get("resetsAt"))
            utilization = window.get("utilization")
            # A window without both halves says nothing a reader can act on:
            # a bar with no reset time cannot answer "how long until this
            # clears", which is the question a full window actually raises.
            if resets_at is None or not isinstance(utilization, int | float):
                continue
            windows.append(
                QuotaWindow(name=str(name), utilization=float(utilization), resets_at=resets_at)
            )

        threshold = info.get("surpassedThreshold")
        return RateLimits(
            status=str(info.get("status") or "unknown"),
            windows=tuple(sorted(windows, key=lambda w: w.name)),
            representative=(
                str(info["rateLimitType"]) if info.get("rateLimitType") is not None else None
            ),
            using_overage=bool(info.get("isUsingOverage")),
            surpassed_threshold=(float(threshold) if isinstance(threshold, int | float) else None),
        )
    return None


def _error_text(api_error: Any, result: Any, *, subtype: Any = None, errors: Any = None) -> str:
    """Status and diagnosis together, in that order, without repeating either.

    `result` is empty on the envelopes that need a diagnosis most: the CLI
    giving up after the model repeatedly produced unparseable tool input, or
    hitting its turn limit, carries the reason in `subtype`
    (`error_during_execution`, `error_max_turns`) and `errors`, and nothing in
    `result`. Three ledger rows read literally `is_error` (2026-08-21): true,
    and useless.
    """
    status = str(api_error) if api_error else ""
    text = str(result or "").strip()
    if not text:
        detail = "; ".join(str(e).strip() for e in (errors or []) if str(e).strip())
        if subtype and subtype != "success":
            text = f"{subtype}: {detail}" if detail else str(subtype)
        else:
            text = detail
    if status and text:
        return f"{status}: {text[:300]}" if status not in text else text[:300]
    return status or text[:300] or "is_error"


def _envelope_result(stdout: str, *, cache_key: str, duration_ms: int) -> LlmResult:
    try:
        env = _result_line(stdout)
    except json.JSONDecodeError as exc:
        return LlmResult(
            data=None,
            usage=Usage(),
            error=f"unparseable envelope: {exc}",
            cache_key=cache_key,
            duration_ms=duration_ms,
        )
    if not isinstance(env, dict):
        return LlmResult(
            data=None,
            usage=Usage(),
            error=f"envelope was {type(env).__name__}, not an object",
            cache_key=cache_key,
            duration_ms=duration_ms,
        )

    model_usage = env.get("modelUsage") or {}
    usage = Usage.from_envelope(env.get("usage") or {}, model_usage)
    models = {name: (info or {}).get("costUSD", 0.0) or 0.0 for name, info in model_usage.items()}
    cost = float(env.get("total_cost_usd") or sum(models.values()))

    denials = env.get("permission_denials") or []
    if denials:
        names = ", ".join(sorted({d.get("tool_name", str(d)) for d in denials}))
        raise PermissionDenied(
            f"claude denied tools [{names}] -- the call ran without them and its "
            f"result is not trustworthy. Pass --allowedTools for these."
        )

    api_error = env.get("api_error_status")
    if env.get("is_error") or api_error:
        return LlmResult(
            data=None,
            usage=usage,
            models=models,
            cost_usd=cost,
            duration_ms=duration_ms,
            # BOTH the status and the text: `str(api_error)` alone leaves the
            # circuit breaker's message and the `llm_calls` ledger carrying the
            # bare string "401", while the sentence that tells an operator what
            # to do is in `result` ("Failed to authenticate. API Error: 401 OAuth
            # access token is invalid.").
            error=_error_text(
                api_error,
                env.get("result"),
                subtype=env.get("subtype"),
                errors=env.get("errors"),
            ),
            cache_key=cache_key,
        )

    structured = env.get("structured_output")
    if not isinstance(structured, dict):
        # A refusal comes back well-formed but without structured output.
        text = str(env.get("result") or "")
        return LlmResult(
            data=None,
            usage=usage,
            models=models,
            cost_usd=cost,
            duration_ms=duration_ms,
            refused=True,
            error=f"no structured_output: {text[:200]}",
            cache_key=cache_key,
        )

    # NOTE: `stop_reason` is deliberately not consulted. With --json-schema it
    # is "tool_use" on success.
    return LlmResult(
        data=structured,
        usage=usage,
        models=models,
        cost_usd=cost,
        duration_ms=duration_ms,
        cache_key=cache_key,
    )
