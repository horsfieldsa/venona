"""Does widening the schema stop the CLI rejecting the model's tool call?

    uv run python scripts/envelope_experiment.py            # 2 docs x 3 reps
    uv run python scripts/envelope_experiment.py 3 5        # 3 docs x 5 reps

SPENDS REAL SUBSCRIPTION QUOTA -- roughly $0.14 per call, measured, so the
default 2x3x2 arms is about $1.70. Nothing here writes to the database.

`scripts/envelope_report.py` establishes the problem: 39.8% of `analyze`'s
structured-output calls are rejected because the model wraps the payload in a
placeholder envelope -- `{'$PARAMETER_VALUE': '…'}` or `{'response': '…'}` --
with the CORRECT payload nested inside. `--json-schema` makes the CLI reject and
the model regenerate INSIDE the session, so the executor never sees the wrapped
attempt and cannot salvage it. We pay for two ~3.8k-token generations and use
one.

`llm/prompts.py::output_contract` establishes that prompt wording does not touch
this: it is an invocation-layer failure, not a comprehension one, and the
measured fix there (0.75 -> 0.13 rejections per call) only addressed the genuine
schema violations.

**The untested premise** is that if the schema ACCEPTED the wrapper, the first
attempt would validate and there would be no second generation. That is a live
behaviour of the CLI's validator and cannot be checked offline, which is what
this script is for.

Two arms, same documents, same prompt, same model:

    control    `analyze_schema()` exactly as the pipeline sends it
    tolerant   the same schema with the two wrappers accepted at the root

What is measured per arm:

  * rejections per call -- the number the fix has to move. The baseline for
    `analyze` is 0.40 (`envelope_report.py`).
  * cost per accepted payload.
  * whether the accepted payloads AGREE across arms on the same document. An
    arm that is cheaper and returns different analysis is not a win, and this is
    the check that would catch a `oneOf` root quietly changing what the model
    produces.

A third arm -- dropping `--json-schema` for `analyze` and parsing JSON from the
response text -- is NOT here. It cannot be expressed as a schema variant: it
needs a branch in `harness/claude_code.py::argv`, which is a change to the thing under
test rather than a treatment of it. If `tolerant` fails, that arm becomes the
candidate and earns its own pass.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from venona.db import core as db
from venona.db.repositories import diagnostics as diag_repo  # noqa: E402
from venona.llm.executor import build_executor  # noqa: E402
from venona.llm.prompts import analyze_system_prompt, load_profile  # noqa: E402
from venona.llm.runner import build_user_content  # noqa: E402
from venona.llm.schemas import analyze_schema  # noqa: E402
from venona.llm.transcript import summarize  # noqa: E402
from venona.settings import get_settings  # noqa: E402

WRAPPED = ("$PARAMETER", "'response'", '"response"')


def tolerant_schema() -> dict[str, Any]:
    """The control schema, plus the two envelopes the model actually emits.

    The wrappers carry a STRING -- the log shows `{'response': '{\\n"relevance"…'}`,
    a JSON document inside a JSON string, double-encoded. So the alternative
    branches type the value as a string; unwrapping and validating the inner
    payload would be the executor's job if this arm wins.
    """
    inner = analyze_schema()
    return {
        "type": "object",
        "oneOf": [
            inner,
            {
                "type": "object",
                "properties": {"response": {"type": "string"}},
                "required": ["response"],
            },
            {
                "type": "object",
                "properties": {"$PARAMETER_VALUE": {"type": "string"}},
                "required": ["$PARAMETER_VALUE"],
            },
        ],
    }


def pick_documents(conn: db.Conn, n: int) -> list[db.Row]:
    """Pinned by id order, not by recency: the arms must see the same prompt on
    every invocation of this script, or the comparison is against noise."""
    return diag_repo.sample_documents(conn, n)


async def run_arm(name: str, schema: dict, docs: list[db.Row], reps: int) -> dict:
    settings = get_settings()
    conn = db.connect(settings.database_url)
    conn.row_factory = db.Row
    llm = build_executor(settings)
    system_prompt = analyze_system_prompt(conn, load_profile(settings))

    stats: dict[str, Any] = {
        "calls": 0,
        "rejections": 0,
        "wrapped_attempts": 0,
        "accepted": 0,
        "cost": 0.0,
        "payloads": {},
    }

    for doc in docs:
        for rep in range(reps):
            attempts: list[str] = []

            async def on_event(event: dict, _sink: list[str] = attempts) -> None:
                # The stream is the only place a rejected attempt is visible --
                # the final envelope carries the winner alone.
                #
                # Shaped through `transcript.summarize`, which is what writes
                # `run_events`, so this counts the SAME thing
                # `envelope_report.py` reads back. Re-deriving the shape here
                # would let the experiment and the baseline drift apart, and the
                # whole point is comparing one against the other.
                data = summarize(event, task="analyze")
                if not data:
                    return
                if data.get("kind") == "tool_use":
                    _sink.append(str(data.get("input") or ""))
                elif data.get("kind") == "tool_result" and data.get("is_error"):
                    _sink.append("__ERROR__")

            result = await llm.call(
                "analyze",
                user_content=build_user_content(
                    title=doc["title"],
                    url=doc["url"],
                    publisher=doc["publisher"],
                    published_at=doc["published_at"],
                    text=doc["text_nfc"],
                    max_chars=settings.llm.max_doc_chars,
                ),
                schema=schema,
                system_prompt=system_prompt,
                model=settings.llm.analyze_model,
                effort=settings.llm.analyze_effort,
                on_event=on_event,
            )

            stats["calls"] += 1
            stats["cost"] += result.cost_usd or 0.0
            stats["rejections"] += sum(1 for a in attempts if a == "__ERROR__")
            stats["wrapped_attempts"] += sum(1 for a in attempts if any(w in a for w in WRAPPED))
            if result.data:
                stats["accepted"] += 1
                # Keyed by document, first rep only: the comparison across arms
                # is "same document, same answer", not "same rep".
                stats["payloads"].setdefault(doc["id"], result.data)
            print(
                f"  {name:<9} {doc['id'][:12]} rep{rep + 1}  "
                f"attempts={len(attempts)} rejections={attempts.count('__ERROR__')} "
                f"${result.cost_usd or 0:.3f}",
                flush=True,
            )
    return stats


def agreement(a: dict, b: dict) -> str:
    """Do the two arms return the same ANALYSIS for the same document?

    Compared on the fields a reader sees rather than on the whole payload: the
    relevance tier and the entity set are what a brief is built from, and a
    wrapper that changed those would be buying cheapness with accuracy.
    """
    shared = set(a) & set(b)
    if not shared:
        return "no shared documents"
    same = 0
    for doc_id in shared:
        ea = {e.get("surface") for e in (a[doc_id].get("entities") or [])}
        eb = {e.get("surface") for e in (b[doc_id].get("entities") or [])}
        overlap = len(ea & eb) / max(1, len(ea | eb))
        if overlap >= 0.8:
            same += 1
    return f"{same}/{len(shared)} documents agree on >=80% of entities"


async def main() -> int:
    docs_n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    settings = get_settings()
    if settings.offline:
        print("VENONA_OFFLINE=1 -- this experiment needs live calls")
        return 2

    conn = db.connect(settings.database_url)
    conn.row_factory = db.Row
    docs = pick_documents(conn, docs_n)
    if not docs:
        print("no documents in the size band")
        return 2

    print(f"{len(docs)} documents x {reps} reps x 2 arms = {len(docs) * reps * 2} calls")
    print()

    arms = {
        "control": analyze_schema(),
        "tolerant": tolerant_schema(),
    }
    results = {}
    for name, schema in arms.items():
        results[name] = await run_arm(name, schema, docs, reps)

    print()
    print("─" * 72)
    print(f"  {'arm':<10}{'calls':>7}{'reject/call':>13}{'wrapped':>9}{'$/accepted':>12}")
    for name, s in results.items():
        per_call = s["rejections"] / max(1, s["calls"])
        per_ok = s["cost"] / max(1, s["accepted"])
        print(
            f"  {name:<10}{s['calls']:>7}{per_call:>13.2f}{s['wrapped_attempts']:>9}{per_ok:>12.3f}"
        )
    print("─" * 72)
    # NOT comparable to the per-call column above, and it was printed directly
    # beneath it as though it were. `envelope_report.py` divides by tool_use
    # EVENTS; this table divides by `llm.call` invocations, which is a different
    # and larger denominator, so the two numbers are on different scales.
    # Quoting it here invited exactly the comparison it does not support.
    print("  (for the historical rate see `envelope_report.py` -- different denominator)")
    print(f"  {agreement(results['control']['payloads'], results['tolerant']['payloads'])}")
    print("─" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
