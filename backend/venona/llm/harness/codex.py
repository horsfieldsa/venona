"""Codex: argv, the JSONL stream, the error vocabulary, the probe.

Verified behaviours this driver encodes (measured against codex-cli 0.151.0 on
2026-09-10, not inferred -- see docs/adr/):

  * `--output-schema` is sent as a STRICT `text.format` and the provider
    rejects, with HTTP 400 `invalid_json_schema`, any schema whose `required`
    omits a property. `minimum`, `maximum`, `maxItems` and runtime-built enums
    all pass.
  * `codex exec` READS STDIN even when the prompt is an argument. Handed a pipe
    nobody writes to, it waits forever -- the same shape as the probe that gave
    `claude --version` a stdin pipe it would not read (ADR 0155 addendum).
  * A turn can emit SEVERAL `agent_message` items. The first can be a
    placeholder ("Searching authoritative sources...") that parses as a valid
    document; the LAST one is the answer.
  * `turn.failed.error.message` is a JSON STRING carrying the provider's own
    envelope. The machine-readable code is inside it, so classification parses
    rather than greps.
  * `codex exec --json` emits NO rate-limit event. The seven event types are
    `thread.started`, `turn.started`, `turn.completed`, `turn.failed`,
    `item.started`, `item.updated`, `item.completed`. A window reading belongs
    to a different protocol, which is what `quota()` speaks (ADR 0220).
  * `codex --version` prints `codex-cli 0.151.0` -- the name first -- and
    `codex login status` prints prose and exits 1 when signed out.
  * `--sandbox read-only` blocks WRITES and permits READS of the whole
    filesystem, by design. It is not a confinement venona can rely on; see
    `llm/harness/confine.py` and ADR 0219.
"""

from __future__ import annotations

import contextlib
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
    QuotaSpec,
    Verdict,
    matches_pin,
)
from venona.llm.types import HarnessHealth, LlmResult, PlanHelp, QuotaWindow, RateLimits, Usage

#: Dollars per million tokens, by model, for METERED billing only. Kept here
#: because this service reports tokens and never a price, so a figure derived
#: from it is this repo's arithmetic rather than the provider's invoice.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-5.6-sol": (1.25, 10.00),
    "gpt-5.6-terra": (2.50, 20.00),
    "gpt-5.6-luna": (0.60, 4.00),
    "gpt-5.4-mini": (0.25, 2.00),
}

#: Provider conditions worth waiting out, matched as symbols rather than as
#: digits a request id could also contain.
_TRANSIENT = frozenset(
    {
        "rate_limit_exceeded",
        "rate_limit_reached",
        "usage_limit_reached",
        "usage_limit_exceeded",
        "quota_exceeded",
        "server_overloaded",
        "internal_server_error",
        "http_connection_failed",
        "response_stream_connection_failed",
        "response_stream_disconnected",
        "response_too_many_failed_attempts",
        "retry_limit",
        "timeout",
        "request_timeout",
    }
)

#: The model declining, or being handed more than it can read. A result rather
#: than damage: it must not count against the circuit, because retrying it
#: would fail identically and opening the breaker would stop work that is fine.
_REFUSAL = frozenset({"context_window_exceeded", "cyber_policy", "misalignment_policy_violation"})

#: PHRASES, for the failures that arrive with no machine code at all. A usage
#: ceiling arrives as prose, so 36 of one run's 73 calls graded `terminal`, the
#: breaker opened, and the run lost `acronym_search`, `define_write` and its
#: BRIEF to a condition whose own message says when it ends (ADR 0220).
#:
#: WORDS, NEVER NUMBERS. This vendor appends a 16-hex `cf-ray` and a 32-hex
#: request id, so a match on `500` or `429` flips on roughly one error in
#: sixteen for reasons having nothing to do with the error. Anything added here
#: must be a phrase no request id can produce.
_TRANSIENT_PHRASES = (
    "usage limit",
    "rate limit",
    "try again at",
    "try again later",
    "timed out",
    "temporarily unavailable",
    "overloaded",
)


@dataclass(frozen=True)
class Codex:
    key: str = "codex"
    label: str = "Codex"
    binary: str = "codex"
    fixture_root: str = "_codex"
    #: Measured against 0.151.0: no `codex exec --json` event carries a window,
    #: so the reading is not a field of a call's result here. It IS obtainable
    #: on this vendor's app-server protocol, which is what `quota()` speaks.
    reports_usage_windows: bool = True
    #: The default model's tool mode runs code, and `--sandbox read-only`
    #: restricts writes while permitting READS by design -- including of the
    #: repository and of the credential this app keeps on disk. ADR 0219.
    wants_confinement: bool = True

    @property
    def aliases(self) -> dict[str, str]:
        """Tier words to models a SUBSCRIPTION can actually call.

        The mini models are not one of them. Measured 2026-09-10 on a ChatGPT
        account: `gpt-5.4-mini` and `gpt-5.6-sol-mini` both come back
        `invalid_request_error: ... not supported when using Codex with a
        ChatGPT account`, while `luna`, `sol` and `terra` all answer. The
        cheapest tier pointed at a mini model, so every `fast` call on the
        billing mode this project runs on failed until the breaker gave up on a
        service that was working. `luna` is the cheapest of the three that do
        answer, which is what makes it the fast tier.
        """
        return {
            "fast": "gpt-5.6-luna",
            "standard": "gpt-5.6-sol",
            "deep": "gpt-5.6-terra",
            # The other services' model names, so an instance that has tuned
            # its models keeps working when it switches rather than asking this
            # provider for a model belonging to someone else. Grok runs one
            # model at three efforts, so it has no tier to preserve and lands on
            # the middle one.
            "haiku": "gpt-5.6-luna",
            "sonnet": "gpt-5.6-sol",
            "opus": "gpt-5.6-terra",
            "grok-4.6": "gpt-5.6-sol",
        }

    @property
    def capabilities(self) -> frozenset[str]:
        """Search, and no way to fetch a named page.

        Declared rather than silently absent: the grounding stage asks for both
        and can work with search alone, so `narrow_to_capabilities` records the
        tool it dropped rather than the stage running narrower unexplained.
        """
        return frozenset({WEB_SEARCH})

    @property
    def billing_modes(self) -> frozenset[str]:
        return frozenset({"subscription", "api"})

    @property
    def secret_env(self) -> frozenset[str]:
        return frozenset({"OPENAI_API_KEY", "CODEX_API_KEY"})

    @property
    def metered_env(self) -> frozenset[str]:
        return frozenset({"OPENAI_API_KEY", "CODEX_API_KEY"})

    @property
    def home_env(self) -> str:
        return "CODEX_HOME"

    @property
    def home_dirname(self) -> str:
        return ".codex"

    @property
    def credential_paths(self) -> tuple[str, ...]:
        """MEASURED 2026-09-14: `~/.codex/auth.json`, 4,178 bytes in a 138 MB
        directory. THE SERVICE THIS MATTERS MOST FOR: nothing here can be
        pasted, so a subscription sign-in survives a deploy only if a disk does.
        """
        return ("auth.json",)

    def plan_help(self, billing_mode: str) -> PlanHelp:
        """The subscription is not pasteable and saying so is the useful half.

        This vendor writes `auth.json` into its own home and refreshes it there,
        so there is no value to hold. An administrator offered a box for it
        pastes something nothing reads, and the next run fails somewhere that
        looks unrelated. ADR 0238.
        """
        if billing_mode == "api":
            return PlanHelp(
                credential="an API key",
                how=(
                    "Create one in the OpenAI platform console, under API keys, and "
                    "paste it here. It is charged per token and is separate from a "
                    "ChatGPT subscription."
                ),
                pasteable=True,
                metered=True,
            )
        return PlanHelp(
            credential="a signed-in session",
            how=(
                "Run `codex login` on the computer where Venona is running and sign "
                "in there. It keeps the session in its own files and renews it "
                "itself, so there is nothing to paste here."
            ),
            pasteable=False,
            metered=False,
            command="codex login",
        )

    def env_for(self, billing_mode: str) -> str | None:
        """None on a subscription: the session lives in the home this vendor
        refreshes for itself, so there is no variable to put a value in."""
        return "OPENAI_API_KEY" if billing_mode == "api" else None

    # ── invocation ───────────────────────────────────────────────────

    async def argv(self, req: CallRequest, files: FileCache) -> list[str]:
        schema = await files.materialize(
            json.dumps(req.schema, separators=(",", ":")), kind="schemas", suffix=".json"
        )
        argv = [
            "exec",
            "--json",
            # `--ephemeral` leaves no session behind and `--ignore-user-config`
            # keeps an operator's own settings out of a run. Neither suppresses
            # skills or plugins, which is why the working root is empty and the
            # environment is an allowlist rather than an inheritance.
            "--ephemeral",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-m",
            req.model,
            "-c",
            f"model_reasoning_effort={req.effort}",
        ]
        if schema is not None:
            argv += ["--output-schema", str(schema)]
        if req.system_prompt:
            prompt_file = await files.materialize(
                req.system_prompt, kind="system_prompts", suffix=".md"
            )
            if prompt_file is not None:
                argv += ["-c", f"model_instructions_file={prompt_file}"]
        # EITHER VOCABULARY, because call sites name capabilities and older ones
        # name one vendor's tools. A membership test for only one of the two
        # ships `web_search=false` while the prompt and the schema still ask the
        # model to search, and the answer comes back well-formed and empty --
        # "silently returns found nothing, forever", with nothing to detect it.
        wants_search = any(t.lower().replace("_", "") == "websearch" for t in req.tools)
        argv += ["-c", f"tools.web_search={'true' if wants_search else 'false'}"]
        # `-` reads the prompt from stdin, which is where it has to go: a single
        # argv element is capped at MAX_ARG_STRLEN and this content reaches 192k.
        argv.append("-")
        return argv

    def child_env(self, base: dict[str, str], cred: Credential) -> dict[str, str]:
        """AN ALLOWLIST, not an inheritance, and that is the difference that matters.

        This service runs a shell and reads any file the process can, so the
        question is what is within reach. The environment is the half of that
        answer needing no kernel feature: with only these names set, a prompt
        that talks the model into running `printenv` learns nothing, because the
        database URL, the session secret, the Neo4j password and the management
        token are simply absent. `CODEX_HOME` drops with them, which is why
        `harness._home_in_use` reads the child's home off THIS mapping.
        """
        allowed = ("PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")
        env = {name: base[name] for name in allowed if name in base}
        # Proxy settings are the one inherited group it must keep: without them
        # a deployment behind an egress proxy cannot reach the provider at all.
        env.update({k: v for k, v in base.items() if k.upper().endswith("_PROXY")})
        env["HOME"] = str(cred.home) if cred.home else base.get("HOME", "/tmp")
        if cred.home is not None:
            env[self.home_env] = str(cred.home)
        env.update(cred.env)
        return env

    def adapt_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Nothing to subtract, and that was measured rather than assumed.

        The shared schemas name every property and express absence as null, so
        the strict validation this service applies accepts them whole. What it
        rejects is a `required` that omits a property, which no schema here does.
        """
        return schema

    def cost_for(self, model: str, usage: Usage) -> float | None:
        """Dollars for a call this service priced in tokens.

        An estimate rather than a fact, which is what `cost_basis: computed`
        records on the row. A model absent from `PRICES` yields None rather than
        zero -- a missing price is not free.
        """
        rate = PRICES.get(model)
        if rate is None:
            return None
        cached = usage.cache_read_input_tokens
        fresh = max(0, usage.input_tokens - cached)
        # Cached input bills at a fraction of fresh input, but the ratio moves,
        # so cached tokens are charged at the full rate rather than at a
        # discount this table would have to track.
        return (fresh * rate[0] + cached * rate[0] + usage.output_tokens * rate[1]) / 1_000_000

    # ── the stream ───────────────────────────────────────────────────

    def parse(self, stdout: str, *, cache_key: str = "", duration_ms: int = 0) -> LlmResult:
        usage = Usage()
        answers: list[str] = []
        failure: str | None = None
        model = ""
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
            if kind == "turn.completed":
                usage = _usage(event.get("usage") or {})
            elif kind == "turn.failed":
                failure = _error_text(event.get("error"))
            elif kind == "item.completed":
                item = event.get("item") or {}
                item_kind = item.get("item_type") or item.get("type")
                if item_kind == "agent_message":
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        answers.append(text)
                elif item_kind == "web_search":
                    # COUNTED FROM THE ITEMS, because the usage block carries
                    # tokens and nothing else -- and a `web_search_requests` of
                    # zero reads as "no searches happened" about the discovery
                    # sweeps that consist almost entirely of searching.
                    # `item.completed` only: the same search also arrives as
                    # `item.started`.
                    searches += 1

        usage = replace(usage, web_search_requests=searches)

        if failure is not None:
            return LlmResult(
                data=None,
                usage=usage,
                error=failure,
                cache_key=cache_key,
                duration_ms=duration_ms,
                cost_basis="none",
            )

        # THE LAST ONE: a placeholder message can precede the answer and parses
        # as a valid document, so taking the first returns a well-formed lie.
        if not answers:
            return LlmResult(
                data=None,
                usage=usage,
                refused=True,
                error="no answer in the stream",
                cache_key=cache_key,
                duration_ms=duration_ms,
                cost_basis="none",
            )
        try:
            data = json.loads(answers[-1])
        except json.JSONDecodeError:
            return LlmResult(
                data=None,
                usage=usage,
                refused=True,
                error=f"no structured output: {answers[-1][:200]}",
                cache_key=cache_key,
                duration_ms=duration_ms,
                cost_basis="none",
            )
        if not isinstance(data, dict):
            return LlmResult(
                data=None,
                usage=usage,
                refused=True,
                error=f"answer was {type(data).__name__}, not an object",
                cache_key=cache_key,
                duration_ms=duration_ms,
                cost_basis="none",
            )
        return LlmResult(
            data=data,
            usage=usage,
            models={model: 0.0} if model else {},
            cache_key=cache_key,
            duration_ms=duration_ms,
            cost_basis="none",
        )

    def classify(self, error: str | None) -> Verdict:
        """Read the code first, and the prose only in words (ADR 0220).

        Getting this wrong decides by hash whether the research lane waits out
        an incident or aborts the run.
        """
        # BOTH SHAPES. `parse` flattens the provider envelope into
        # "code: message" before it reaches `LlmResult.error`, and the breaker
        # and the retry both classify THAT string -- so a classifier reading
        # only raw JSON returns "" for every real call and grades every failure
        # terminal, which is a rate limit neither waited out nor retried.
        code = _error_code(error) or _leading_code(error)
        if code in _REFUSAL:
            return "refusal"
        if code in _TRANSIENT:
            return "transient"
        # A ceiling commonly arrives with no code, so the prose is all that is
        # left. Checked AFTER the codes, so a typed error always wins.
        text = (error or "").lower()
        if any(phrase in text for phrase in _TRANSIENT_PHRASES):
            return "transient"
        return "terminal"

    def is_transcript_event(self, raw: dict[str, Any]) -> bool:
        """Everything except the two that carry the call's own outcome."""
        return raw.get("type") not in {"turn.completed", "turn.failed"}

    def summarize(self, raw: dict[str, Any], *, task: str) -> dict[str, Any] | None:
        """This vendor's stream into contract C6's shape.

        `session_started` carries only a thread id: this service names neither
        the model nor the tools it granted, so those fields stay absent rather
        than being filled from the argv we asked for, which is a different fact.
        """
        kind = raw.get("type")
        if kind == "thread.started":
            return {"kind": "session_started", "task": task, "session_id": raw.get("thread_id")}
        if kind != "item.completed":
            return None
        item = raw.get("item") or {}
        item_type = item.get("item_type") or item.get("type")
        if item_type == "agent_message":
            return {"kind": "text", "task": task, "text": _clip(item.get("text"))}
        if item_type == "reasoning":
            return {"kind": "thinking", "task": task, "text": _clip(item.get("text"))}
        if item_type in {"command_execution", "mcp_tool_call", "web_search"}:
            # `tool` is the key `llm/transcript.py` and the run page read;
            # a `name` is silently dropped, and the tool line then shows its
            # input with no chip saying what ran.
            return {
                "kind": "tool_use",
                "task": task,
                "tool": item_type,
                "input": _clip(item.get("command") or item.get("query")),
            }
        return None

    # ── health ───────────────────────────────────────────────────────

    def probe(self) -> ProbeSpec:
        return ProbeSpec(version=("--version",), auth=("login", "status"))

    def quota(self) -> QuotaSpec:
        """One JSON-RPC round trip on the app-server protocol (ADR 0220).

        `account/rateLimits/read` answers while `codex exec` processes are
        mid-run, which is the whole reason a window reading is available at all
        on a service whose calls carry none.

        THE PIPE STAYS OPEN until the answer arrives, per `QuotaSpec`. That is
        what `done_when` is for: `initialized` is a notification and carries no
        id, the third line does, and that id is what says the answer landed.
        """
        request = [
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {"clientInfo": {"name": "venona", "version": "1", "title": "venona"}},
            },
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 1, "method": "account/rateLimits/read", "params": {}},
        ]
        return QuotaSpec(
            argv=("app-server",),
            stdin="".join(json.dumps(line) + "\n" for line in request),
            done_when='"id":1',
        )

    def parse_quota(self, stdout: str) -> RateLimits | None:
        """`usedPercent` is 0-100 here and a 0-to-1 fraction in the ledger.

        Divided in the driver, next to the parse that reads it, which is where
        `llm.types.QuotaWindow` says a vendor's scale is converted.

        ONE LIMIT, the one that meters the calls this driver makes. The reply
        carries `rateLimits` (that limit) and `rateLimitsByLimitId` (every limit
        the account holds, including one per model with its own allowance), and
        the top-level block names which id is which through `limitId`. Both are
        read and merged, because the by-id copy of the SAME id may carry a
        window the top-level block omits.

        NOT the other buckets: a bucket keyed `codex_bengalfox` and named
        `GPT-5.3-Codex-Spark` is a model nothing here calls, so its five-hour
        window sits at 0 forever, and folding it in put a permanent
        `session 0%` on the strip beside a weekly figure that climbed
        correctly. On `prolite` the metered limit reports no five-hour window
        at all, and ABSENT is the honest rendering of that: a zero is a
        measurement, and there was none.

        MERGED BY WINDOW LENGTH, taking the fullest. The NAMES come from the
        durations rather than from the `primary`/`secondary` keys: `primary` is
        the five-hour window on one plan and the seven-day one on another, so
        keying on it would label a weekly limit as a session limit.
        """
        payload: dict[str, Any] | None = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == 1:
                payload = message.get("result") or {}
                break
        if not isinstance(payload, dict):
            return None

        buckets = _metered_buckets(payload)

        fullest: dict[str, QuotaWindow] = {}
        reached = False
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            reached = reached or bool(bucket.get("rateLimitReachedType"))
            for key in ("primary", "secondary"):
                block = bucket.get(key)
                if not isinstance(block, dict):
                    continue
                used = block.get("usedPercent")
                resets = block.get("resetsAt")
                if used is None or resets is None:
                    continue
                window = QuotaWindow(
                    name=_window_name(block.get("windowDurationMins")),
                    utilization=float(used) / 100.0,
                    resets_at=_epoch_to_iso(resets),
                )
                seen = fullest.get(window.name)
                if seen is None or window.utilization > seen.utilization:
                    fullest[window.name] = window

        if not fullest:
            return None

        # Shortest window first, so the strip's rows do not reorder as the day
        # goes on -- a row that moves is one you have to read twice.
        windows = tuple(sorted(fullest.values(), key=lambda w: (_minutes(w.name), w.name)))
        return RateLimits(
            status="reached" if reached else "ok",
            windows=windows,
            representative=max(windows, key=lambda w: w.utilization).name,
        )

    def parse_probe(self, out: ProbeOutput, *, pinned: str) -> HarnessHealth:
        """`codex-cli 0.151.0`, and a sign-in status that is prose.

        The program name comes FIRST, so a parse taking `split()[0]` reads
        `codex-cli` as a version and reports a permanent pin mismatch. The
        sign-in check exits non-zero when signed out while still printing the
        answer, so the exit code is not the verdict.
        """
        version = None
        parts = out.version.strip().split()
        if parts:
            version = parts[-1] if parts[0].startswith("codex") else parts[0]
        text = (out.auth or "").strip()
        logged_in = "logged in" in text.lower() and "not logged in" not in text.lower()
        account = None
        if logged_in and " using " in text:
            account = text.split(" using ", 1)[1].strip() or None
        return HarnessHealth(
            ok=logged_in,
            version=version,
            logged_in=logged_in,
            account=account,
            version_matches_pin=matches_pin(version, pinned),
            error=None if logged_in else "not signed in",
        )

    def login(self) -> LoginDriver | None:
        """Sign-in stays out of band (ADR 0238).

        `codex login --device-auth` exists and its help text is EMPTY (0.151.0),
        so it is undocumented rather than merely unstable, and a flow built on
        scraping its output would break on the vendor's next release -- on the
        one page an operator opens when nothing works.
        """
        return None


CODEX = Codex()


def _metered_buckets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The limit that meters this driver's calls, from both places it appears.

    `rateLimits` is the account's metered limit and `limitId` is its name, so
    the matching entry in `rateLimitsByLimitId` is the same limit and is read
    with it. Every other entry is a per-model allowance for a model this driver
    never selects, and is left out.

    A reply with no top-level block and exactly one bucket is that bucket. More
    than one and there is nothing saying which meters anything, so returning
    none of them reports the reading as absent rather than picking.
    """
    top = payload.get("rateLimits")
    by_id = payload.get("rateLimitsByLimitId")
    by_id = by_id if isinstance(by_id, dict) else {}

    if isinstance(top, dict):
        limit_id = top.get("limitId")
        same = by_id.get(limit_id) if isinstance(limit_id, str) else None
        return [b for b in (top, same) if isinstance(b, dict)]

    buckets = [b for b in by_id.values() if isinstance(b, dict)]
    return buckets if len(buckets) == 1 else []


#: 300 minutes is the five-hour window, 10080 the seven-day one. Named from the
#: duration rather than passed through, so the labels `QuotaWindows.vue` already
#: carries apply to this service's reading unchanged.
_WINDOW_NAMES = {300: "five_hour", 10080: "seven_day"}


def _window_name(minutes: Any) -> str:
    try:
        mins = int(minutes)
    except (TypeError, ValueError):
        return "window"
    known = _WINDOW_NAMES.get(mins)
    if known:
        return known
    # A window nobody has seen before is still a window, named by its own
    # length. Dropping it would under-report the thing that is about to bind.
    if mins % 1440 == 0:
        return f"{mins // 1440}_day"
    if mins % 60 == 0:
        return f"{mins // 60}_hour"
    return f"{mins}_minute"


def _minutes(name: str) -> int:
    """Sort key: a window's length, recovered from the name it was given."""
    for minutes, known in _WINDOW_NAMES.items():
        if known == name:
            return minutes
    if name.endswith("_day"):
        with contextlib.suppress(ValueError):
            return int(name[: -len("_day")]) * 1440
    if name.endswith("_hour"):
        with contextlib.suppress(ValueError):
            return int(name[: -len("_hour")]) * 60
    if name.endswith("_minute"):
        with contextlib.suppress(ValueError):
            return int(name[: -len("_minute")])
    return 1 << 30


def _epoch_to_iso(value: Any) -> str:
    """Unix seconds to the ISO-8601 Z this repo stores everywhere."""
    from datetime import UTC, datetime

    try:
        return datetime.fromtimestamp(int(value), UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _usage(block: dict[str, Any]) -> Usage:
    def n(key: str) -> int:
        return int(block.get(key) or 0)

    return Usage(
        input_tokens=n("input_tokens"),
        output_tokens=n("output_tokens") + n("reasoning_output_tokens"),
        cache_read_input_tokens=n("cached_input_tokens"),
        cache_creation_input_tokens=n("cache_write_input_tokens"),
    )


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            code = _error_code(message)
            inner = _inner_message(message)
            return f"{code}: {inner}"[:300] if code else inner[:300]
        return str(error)[:300]
    return str(error or "the turn failed")[:300]


def _envelope(message: str | None) -> dict[str, Any]:
    """The provider's own error object, which arrives as a JSON string."""
    if not message:
        return {}
    try:
        parsed = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _error_code(message: str | None) -> str:
    body = _envelope(message).get("error")
    if isinstance(body, dict):
        for field in ("code", "type"):
            value = body.get(field)
            if isinstance(value, str) and value:
                return value
    return ""


def _inner_message(message: str) -> str:
    body = _envelope(message).get("error")
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return body["message"]
    return message


def _leading_code(error: str | None) -> str:
    """The `code` off the front of a message this driver already flattened."""
    if not error:
        return ""
    head = error.split(":", 1)[0].strip()
    return head if head and head.replace("_", "").isalnum() and " " not in head else ""


def _clip(value: Any, limit: int = 600) -> str:
    text = value if isinstance(value, str) else json.dumps(value) if value else ""
    return text[:limit]
