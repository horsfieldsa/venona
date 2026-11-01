"""How often the model wraps its structured output in a placeholder envelope.

    uv run python scripts/envelope_report.py              # every run, by task
    uv run python scripts/envelope_report.py run:06a8…    # one run

`--json-schema` validates, so nothing is ever silently wrong -- the CLI hands
the violation back and the model regenerates inside the same session. What that
costs is a second full generation of an ~3.8k-token payload, and neither the
run card nor the `llm_calls` ledger shows it: both count the call that finally
succeeded. The waste is only visible in the streamed tool calls, which is where
this reads from.

Two envelope shapes, both recorded in `run_events`:

    {'$PARAMETER_NAME': …}    the literal tool-call placeholders, with the
    {'$PARAMETER_VALUE': …}   CORRECT payload nested inside
    {'response': '{"…"}'}     the whole payload wrapped in one key, as a STRING

`llm/prompts.py::output_contract` establishes that these are an invocation-layer
failure rather than a comprehension one -- the payload inside is right -- and
that no prompt wording touches them. This report exists so that claim, and any
attempt to fix it, is judged against a number produced the same way every time
rather than against a memory of last week's run.

**Ship this before the fix.** A baseline computed by different code than the
result is not a comparison.

Read-only. It opens the database `mode=ro` and never writes.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from venona.db import core as db  # noqa: E402
from venona.db.repositories import diagnostics as diag_repo  # noqa: E402
from venona.settings import get_settings  # noqa: E402

# Matched against the raw `input` text rather than parsed, deliberately: the
# whole point is that these payloads are MALFORMED, so anything that insists on
# parsing them first will miss the ones that are malformed in a new way.
SHAPES = {
    "$PARAMETER": "%$PARAMETER%",
    "response-wrap": "{'response'%",
}


def connect() -> db.Conn:
    conn = db.connect(get_settings().database_url)
    conn.row_factory = db.Row
    return conn


def by_task(conn: db.Conn, run_id: str | None) -> list[db.Row]:
    return diag_repo.tool_use_shapes(conn, run_id)


def main() -> int:
    run_id = sys.argv[1] if len(sys.argv) > 1 else None
    conn = connect()
    rows = by_task(conn, run_id)
    if not rows:
        print("no structured-output calls recorded" + (f" for {run_id}" if run_id else ""))
        return 0

    scope = run_id or "all runs"
    print("─" * 72)
    print(f"  structured-output envelopes · {scope}")
    print("─" * 72)
    print(f"  {'task':<16}{'calls':>7}{'$PARAM':>8}{'wrap':>7}{'artifact':>10}")

    total_calls = total_bad = 0
    for row in rows:
        bad = (row["param"] or 0) + (row["wrapped"] or 0)
        calls = row["calls"] or 0
        total_calls += calls
        total_bad += bad
        rate = f"{bad / calls:.1%}" if calls else "—"
        print(
            f"  {(row['task'] or '?'):<16}{calls:>7}{row['param'] or 0:>8}"
            f"{row['wrapped'] or 0:>7}{rate:>10}"
        )

    print("─" * 72)
    overall = f"{total_bad / total_calls:.1%}" if total_calls else "—"
    print(f"  {'TOTAL':<16}{total_calls:>7}{'':>8}{'':>7}{overall:>10}")
    # Stated as a share of the whole rather than left for the reader to compute:
    # a fix scoped to the wrong task is the failure mode this line prevents.
    worst = max(rows, key=lambda r: (r["param"] or 0) + (r["wrapped"] or 0))
    worst_bad = (worst["param"] or 0) + (worst["wrapped"] or 0)
    if total_bad:
        print(f"  {worst['task']} carries {worst_bad / total_bad:.0%} of it")
    print("─" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
