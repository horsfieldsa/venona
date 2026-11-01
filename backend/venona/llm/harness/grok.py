"""Grok Build: argv, envelope, probe, transcript and error vocabulary.

WRITTEN FROM THE VENDOR'S DOCUMENTATION, NOT FROM A MEASUREMENT, which ADR 0229
records as a different kind of artifact from the two drivers beside it. So known
and assumed are marked separately throughout, and
`docs/pm/epics/x-model-services.md` carries the checklist that converts the
second group. Until it is worked this service is selectable and untested, which
is the honest state for a driver nobody has been able to run.

What `xai-org/grok-build`'s headless reference establishes:

  * `-p/--single`, `--prompt-file` and `--prompt-json` all trigger headless.
  * `--json-schema` returns JSON matching a supplied schema.
  * `--output-format streaming-json` emits newline-delimited typed events:
    `thought`, `tool_call`, `tool_call_update`, `text`, `usage`, `plan`,
    `available_commands`, `end`, `error`. `end` is always last and carries the
    spend.
  * `stopReason` on `end` is snake_case: `end_turn`, `max_tokens`,
    `max_turn_requests`, `refusal`, `cancelled`.
  * Exit codes: 0 success, 1 error, 130 SIGINT, 143 SIGTERM.
  * `XAI_API_KEY` is the metered credential; `grok login` writes a cached one.
  * No rate limit reporting in headless, hence `reports_usage_windows=False`.
  * The Messages-format output "omits the schema's `permission_denials` field
    because Grok does not collect that data" -- so the failure CLAUDE.md bans
    treating as a result would be undetectable here rather than merely easy to
    miss. That is what shapes the argv below; ADR 0229 carries the argument.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from venona.llm.harness import (
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
from venona.llm.types import HarnessHealth, LlmResult, PlanHelp, Usage

#: Conditions worth waiting out, as WORDS rather than codes.
#:
#: Same reasoning as `codex.py`'s: this vendor's errors carry a `requestId`, so
#: a numeric match on `429` or `500` is a coin flip against a 32-hex identifier.
#: These are phrases no request id can produce. `usage limit` is here on the
#: other driver's scar, where grading a ceiling terminal cost a run its brief
#: (ADR 0220).
_TRANSIENT_PHRASES = (
    "rate limit",
    "usage limit",
    "quota exceeded",
    "overloaded",
    "capacity",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "timed out",
    "try again",
    "connection reset",
)

#: The model declining, or being handed more than it can read. A result rather
#: than damage: retrying fails identically, and opening the breaker on it would
#: stop work that is fine.
_REFUSAL_REASONS = frozenset({"refusal", "max_tokens", "max_turn_requests"})


@dataclass(frozen=True)
class Grok:
    key: str = "grok"
    label: str = "Grok"
    binary: str = "grok"
    fixture_root: str = "_grok"
    #: The headless reference exposes no rate limit reading and no `usage` or
    #: `limits` subcommand. Declared false rather than left to `quota()`
    #: returning None, because the SURFACE differs: a service that will report a
    #: window has simply not been asked yet, and the strip must not promise one
    #: that never fills in.
    reports_usage_windows: bool = False
    #: ASSUMED, AND DEFENSIVELY: this service edits files and runs shell
    #: commands by default, which is the Codex posture rather than the Claude
    #: Code one. Being wrong this way is mild and self-reporting -- `_home_in_use`
    #: finds no home to grant and `/admin/model` says the call ran unconfined --
    #: while being wrong the other way is a model that can read the credential.
    #: ADR 0229, inheriting ADR 0219.
    wants_confinement: bool = True

    @property
    def aliases(self) -> dict[str, str]:
        """One model, three efforts, and the split is deliberate for now.

        `grok-4.6` is the only model name the headless reference uses, so the
        tiers differ by `--reasoning-effort`, which the reference does
        enumerate. That is a real cost lever and a WEAKER one than three models
        would be; ADR 0229 carries what guessing a lineup cost the other driver.

        The other services' tier words map here too, so an instance that has
        tuned its models keeps working when it switches rather than asking this
        vendor for a model belonging to someone else.
        `tests/test_harness_aliases.py` holds that across all three drivers.
        """
        return {
            "fast": "grok-4.6",
            "standard": "grok-4.6",
            "deep": "grok-4.6",
            "haiku": "grok-4.6",
            "sonnet": "grok-4.6",
            "opus": "grok-4.6",
            "gpt-5.6-luna": "grok-4.6",
            "gpt-5.6-sol": "grok-4.6",
            "gpt-5.6-terra": "grok-4.6",
        }

    @property
    def capabilities(self) -> frozenset[str]:
        """Search only, and `WEB_FETCH` is deliberately absent.

        `--disable-web-search` exists, so search is there to be turned off.
        `WebFetch` appears only among the permission-rule prefixes, which is
        evidence of a tool and not proof of one reachable here -- and declaring
        a capability this service lacks would stop the grounding stage degrading
        on a gap it still has. ADR 0229.
        """
        return frozenset({WEB_SEARCH})

    @property
    def billing_modes(self) -> frozenset[str]:
        return frozenset({"subscription", "api"})

    @property
    def secret_env(self) -> frozenset[str]:
        return frozenset({"XAI_API_KEY"})

    @property
    def metered_env(self) -> frozenset[str]:
        """The same variable, and that is the point of declaring both.

        `XAI_API_KEY` comes from the billing console and is charged per token,
        so its presence on an instance whose settings say `subscription` is the
        contradiction `main.py::_refuse_metered_billing` refuses to boot around. An
        API key is a plan an administrator may choose (ADR 0216); it is only
        refused against a subscription. The subscription credential is not an
        environment variable at all.
        """
        return frozenset({"XAI_API_KEY"})

    @property
    def home_env(self) -> str:
        #: ASSUMED. Named for the confinement's benefit: a CLI whose home is not
        #: granted fails at startup with a permission error rather than a
        #: sign-in one, which is the hardest kind to diagnose.
        return "GROK_HOME"

    @property
    def home_dirname(self) -> str:
        #: ASSUMED, from the binary's name. Verify before relying on the
        #: confinement; getting it wrong degrades to unconfined-and-said-so.
        return ".grok"

    @property
    def credential_paths(self) -> tuple[str, ...]:
        """EMPTY, and deliberately -- this driver has never been run.

        Guessing would be the worst kind of wrong: a captured file that is not
        the credential restores a home that looks populated and does not
        authenticate, which reads as the vendor rejecting the account. Declaring
        nothing costs a sign-in after a restart, which is loud and ordinary.
        Fill it in from the measurement that settles `home_dirname`.
        """
        return ()

    def plan_help(self, billing_mode: str) -> PlanHelp:
        """ASSUMED for the subscription half, in the direction that reports itself.

        Only the API key variable is documented, so the subscription half says
        "run its own sign-in" rather than offering a box that might hold
        nothing: being wrong this way costs an administrator a command they did
        not need to run, the other way a pasted value nothing ever reads.
        """
        if billing_mode == "api":
            return PlanHelp(
                credential="an API key",
                how=(
                    "Create one in the xAI console, under API keys, and paste it here. "
                    "It is charged per token and is separate from a Grok subscription."
                ),
                pasteable=True,
                metered=True,
            )
        return PlanHelp(
            credential="a signed-in session",
            how=(
                "Run `grok login` on the computer where Venona is running and sign in "
                "there. It keeps the session in its own files, so there is nothing to "
                "paste here."
            ),
            pasteable=False,
            metered=False,
            command="grok login",
        )

    def env_for(self, billing_mode: str) -> str | None:
        """None on a subscription: `grok login` writes a cached credential into
        the home and refreshes it there, so there is no variable to put a pasted
        value in and nothing for Venona to hold."""
        return "XAI_API_KEY" if billing_mode == "api" else None

    # ── invocation ───────────────────────────────────────────────────

    async def argv(self, req: CallRequest, files: FileCache) -> list[str]:
        """A single headless turn that answers in a schema and nothing else.

        `--prompt-file` rather than `-p`, for the reason `codex.py` reads its
        prompt from stdin: user content reaches ~192k and a single argv element
        is capped at MAX_ARG_STRLEN (`executor.py::_PromptFiles`, measured
        2026-08-06). The schema goes inline because schemas here are small and
        the reference documents that form.

        `--no-plan` and `--no-subagents` because this is not an agent session.
        Venona asks one question and wants one document back; a plan-first loop
        and parallel subagents are a coding tool's defaults, and both would
        spend a subscription on deliberation nothing reads.
        """
        argv = [
            "--output-format",
            "streaming-json",
            "--no-plan",
            "--no-subagents",
            "-m",
            req.model,
            "--reasoning-effort",
            req.effort,
            # NOT a convenience. There is no terminal to approve at, and this
            # vendor reports no permission denials, so a gated call either hangs
            # or silently answers "found nothing". What bounds a call instead is
            # `--tools`, the `child_env` allowlist and the confinement (ADR 0229,
            # pinned by `tests/test_grok_envelope.py`).
            "--yolo",
            "--json-schema",
            json.dumps(req.schema, separators=(",", ":")),
        ]
        # EITHER VOCABULARY, for the reason `codex.py` states: callers name
        # capabilities and older call sites named one vendor's tools, so
        # matching only one disables search while the prompt and the schema
        # still ask the model to search, and the answer comes back empty.
        wants_search = any(t.lower().replace("_", "") == "websearch" for t in req.tools)
        if not wants_search:
            argv.append("--disable-web-search")
        if req.system_prompt:
            prompt_file = await files.materialize(
                req.system_prompt, kind="system_prompts", suffix=".md"
            )
            if prompt_file is not None:
                argv += ["--system-prompt-override", str(prompt_file)]
        user_file = await files.materialize(req.user_content, kind="prompts", suffix=".md")
        if user_file is not None:
            argv += ["--prompt-file", str(user_file)]
        else:
            argv += ["-p", req.user_content]
        return argv

    def child_env(self, base: dict[str, str], cred: Credential) -> dict[str, str]:
        """AN ALLOWLIST, not an inheritance.

        Same argument as `codex.py`'s and the same reason: this service runs a
        shell, so the question is not whether it is trustworthy but what is
        within reach. With only these names set, a prompt that talks the model
        into running `printenv` learns nothing -- the database URL, the session
        secret, the Neo4j password and the management token are simply absent.
        """
        allowed = ("PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")
        env = {name: base[name] for name in allowed if name in base}
        # The one inherited group it must keep: without these a deployment
        # behind an egress proxy cannot reach the provider at all.
        env.update({k: v for k, v in base.items() if k.upper().endswith("_PROXY")})
        env["HOME"] = str(cred.home) if cred.home else base.get("HOME", "/tmp")
        if cred.home is not None:
            env[self.home_env] = str(cred.home)
        # A CLI that updates itself is a CLI whose pinned version is a fiction,
        # and the pin is what `/admin/model` reports against.
        env["GROK_DISABLE_AUTOUPDATER"] = "1"
        env.update(cred.env)
        return env

    def adapt_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Nothing to subtract, pending a measurement.

        The shared schemas name every property and express absence as null,
        which `codex.py` measured its own strict validation accepting whole. If
        this one rejects something, the rejection is a parse error naming the
        property and this is where the subtraction goes.
        """
        return schema

    # ── the stream ───────────────────────────────────────────────────

    def parse(self, stdout: str, *, cache_key: str = "", duration_ms: int = 0) -> LlmResult:
        """Newline-delimited events into one result.

        THE LAST TEXT, not the first and not the concatenation. `--json-schema`
        constrains the final answer, and a turn may emit narration before it;
        taking the first returns a well-formed lie, and joining them returns
        something that parses as nothing.
        """
        usage = Usage()
        chunks: list[str] = []
        failure: str | None = None
        stop_reason: str | None = None
        cost: float | None = None
        searches = 0

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "text":
                text = event.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text)
            elif kind == "usage":
                usage = _usage(event.get("usage") or event)
            elif kind == "tool_call":
                # COUNTED FROM THE STREAM, because the usage block carries
                # tokens and nothing else. A zero here does not say "nobody
                # measured", it says "no searches happened" -- about the sweeps
                # that are most of a run's spend and consist of searching.
                if _is_search(event):
                    searches += 1
            elif kind == "end":
                stop_reason = event.get("stopReason") or event.get("stop_reason")
                if isinstance(event.get("usage"), dict):
                    usage = _usage(event["usage"])
                spend = event.get("total_cost_usd")
                if isinstance(spend, int | float):
                    cost = float(spend)
            elif kind == "error":
                failure = _error_text(event)

        usage = replace(usage, web_search_requests=searches)
        # `measured` only when the provider sent a figure. On a subscription it
        # does not, and inventing one would put a price in the column that means
        # "what this was billed".
        basis = "measured" if cost is not None else "none"

        if failure is not None:
            return LlmResult(
                data=None,
                usage=usage,
                error=failure,
                cache_key=cache_key,
                duration_ms=duration_ms,
                cost_usd=cost or 0.0,
                cost_basis=basis,
            )
        if stop_reason in _REFUSAL_REASONS:
            return LlmResult(
                data=None,
                usage=usage,
                refused=True,
                error=f"the model stopped early: {stop_reason}",
                cache_key=cache_key,
                duration_ms=duration_ms,
                cost_usd=cost or 0.0,
                cost_basis=basis,
            )
        if not chunks:
            return LlmResult(
                data=None,
                usage=usage,
                refused=True,
                error="no answer in the stream",
                cache_key=cache_key,
                duration_ms=duration_ms,
                cost_usd=cost or 0.0,
                cost_basis=basis,
            )
        try:
            data = json.loads(chunks[-1])
        except json.JSONDecodeError:
            return LlmResult(
                data=None,
                usage=usage,
                refused=True,
                error=f"no structured output: {chunks[-1][:200]}",
                cache_key=cache_key,
                duration_ms=duration_ms,
                cost_usd=cost or 0.0,
                cost_basis=basis,
            )
        if not isinstance(data, dict):
            return LlmResult(
                data=None,
                usage=usage,
                refused=True,
                error=f"answer was {type(data).__name__}, not an object",
                cache_key=cache_key,
                duration_ms=duration_ms,
                cost_usd=cost or 0.0,
                cost_basis=basis,
            )
        return LlmResult(
            data=data,
            usage=usage,
            cache_key=cache_key,
            duration_ms=duration_ms,
            cost_usd=cost or 0.0,
            cost_basis=basis,
        )

    def classify(self, error: str | None) -> Verdict:
        """Words, never numbers. See `_TRANSIENT_PHRASES`."""
        if not error:
            return "terminal"
        text = error.lower()
        if any(phrase in text for phrase in _TRANSIENT_PHRASES):
            return "transient"
        return "terminal"

    def is_transcript_event(self, raw: dict[str, Any]) -> bool:
        """Everything except the two that carry the call's own outcome."""
        return raw.get("type") not in {"end", "error"}

    def summarize(self, raw: dict[str, Any], *, task: str) -> dict[str, Any] | None:
        """One vendor's stream into contract C6's frozen `kind` vocabulary.

        `available_commands` OPENS THE TRANSCRIPT. It is this format's only
        preamble, so it is the nearest thing to the other services' session
        line, and without one the run page draws an empty transcript over a run
        that plainly did work -- the silent failure C6 names.

        The session id and the tools are passed through ONLY if they are there.
        A driver that invented `tools: []` to fill the shape would draw "0 tools"
        on a call that had several.

        `plan` stays dropped: it is a coding agent's plan for work Venona never
        asked it to do.
        """
        kind = raw.get("type")
        if kind == "available_commands":
            entry: dict[str, Any] = {"kind": "session_started", "task": task}
            session = raw.get("sessionId") or raw.get("session_id")
            if session:
                entry["session_id"] = session
            tools = raw.get("tools") or raw.get("commands")
            if isinstance(tools, list):
                entry["tools"] = [str(t) for t in tools]
            return entry
        if kind == "thought":
            return {"kind": "thinking", "task": task, "text": _clip(raw.get("text"))}
        if kind == "text":
            return {"kind": "text", "task": task, "text": _clip(raw.get("text"))}
        if kind in {"tool_call", "tool_call_update"}:
            # `tool` is the key `ModelTranscript.vue` reads; dropping it renders
            # a tool line with no chip saying what ran.
            return {
                "kind": "tool_use",
                "task": task,
                "tool": str(raw.get("name") or raw.get("tool") or "tool"),
                "input": _clip(raw.get("input") or raw.get("query") or raw.get("command")),
            }
        return None

    def cost_for(self, model: str, usage: Usage) -> float | None:
        """None, and unusually that is the accurate answer rather than a gap.

        This vendor puts `total_cost_usd` on the `end` event, so `parse` records
        the provider's own number as `measured` and a table here would be a
        second, worse estimate competing with it -- the opposite of `codex.py`,
        which reports tokens and never a price. ADR 0229.
        """
        return None

    # ── health ───────────────────────────────────────────────────────

    def quota(self) -> None:
        """Nothing to ask; see `reports_usage_windows`."""
        return None

    def parse_quota(self, stdout: str) -> None:
        return None

    def probe(self) -> ProbeSpec:
        return ProbeSpec(version=("--version",), auth=("login", "status"))

    def parse_probe(self, out: ProbeOutput, *, pinned: str) -> HarnessHealth:
        """Program name first or version first, both handled.

        `codex.py` records why this cannot be shared: its output leads with the
        program name, so a parse taking `split()[0]` read `codex-cli` as a
        version and reported a permanent pin mismatch. Which form this one takes
        is unmeasured, so both are accepted rather than one being assumed.

        The exit code is not the verdict, for the same reason it is not there: a
        sign-in check that prints a meaningful answer and exits non-zero when
        signed out would otherwise report a working install as broken.
        """
        version = None
        parts = out.version.strip().split()
        if parts:
            version = parts[-1] if not parts[0][:1].isdigit() else parts[0]
        text = (out.auth or "").strip().lower()
        logged_in = ("logged in" in text or "signed in" in text) and not (
            "not logged in" in text or "not signed in" in text
        )
        return HarnessHealth(
            ok=logged_in,
            version=version,
            logged_in=logged_in,
            version_matches_pin=matches_pin(version, pinned),
            error=None if logged_in else "not signed in",
        )

    def login(self) -> LoginDriver | None:
        """No flow Venona drives, as for every driver -- ADR 0238.

        `grok login --device-auth` exists and would be the shape to build on,
        but its output is undocumented, so a sign-in scraped from it would be
        scraped from something nobody here has seen.
        """
        return None


GROK = Grok()


def _is_search(event: dict[str, Any]) -> bool:
    name = str(event.get("name") or event.get("tool") or "").lower().replace("_", "")
    return "websearch" in name or name == "search"


def _usage(block: dict[str, Any]) -> Usage:
    def count(*names: str) -> int:
        for name in names:
            value = block.get(name)
            if isinstance(value, int):
                return value
        return 0

    return Usage(
        input_tokens=count("input_tokens", "inputTokens"),
        output_tokens=count("output_tokens", "outputTokens"),
        cache_read_input_tokens=count("cache_read_input_tokens", "cacheReadInputTokens"),
        cache_creation_input_tokens=count(
            "cache_creation_input_tokens", "cacheCreationInputTokens"
        ),
    )


def _error_text(event: dict[str, Any]) -> str:
    message = event.get("message") or event.get("error")
    if isinstance(message, dict):
        message = message.get("message") or json.dumps(message)[:200]
    kind = event.get("errorType") or event.get("code")
    text = str(message or "error").strip()
    return f"{kind}: {text}" if kind else text


def _clip(value: Any, limit: int = 2000) -> str:
    text = value if isinstance(value, str) else json.dumps(value) if value is not None else ""
    return text[:limit]
