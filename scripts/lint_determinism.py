#!/usr/bin/env python3
"""Decision 7 enforcement: offline determinism.

`VENONA_OFFLINE=1` must produce byte-identical output across runs. That only
holds if nothing in the pipeline reaches for wall-clock time, randomness, or the
network directly -- those all come from RunContext (ctx.clock, ctx.ids,
ctx.http, ctx.llm) so a single switch can replace them.

This is a grep, not a type system. It is deliberately dumb and deliberately
noisy: a false positive costs one `# determinism: ok` comment, a false
negative costs a day of debugging why two identical runs disagree.

Run: make lint-determinism
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# `export` is here because the vault writer's whole correctness story is that
# the same corpus produces the same bytes. Without it, a wall-clock call in
# that package would pass `make lint` and only show up as a golden test that
# fails once a day.
GUARDED_DIRS = ["pipeline", "services", "llm", "graph", "export"]
PACKAGE = REPO_ROOT / "backend" / "venona"

FORBIDDEN = [
    (re.compile(r"\bdatetime\.now\s*\("), "datetime.now()", "ctx.clock.now()"),
    (re.compile(r"\bdatetime\.utcnow\s*\("), "datetime.utcnow()", "ctx.clock.now()"),
    (re.compile(r"\btime\.time\s*\("), "time.time()", "ctx.clock.now()"),
    (re.compile(r"\buuid\.uuid[14]\s*\("), "uuid.uuid1/uuid4()", "ctx.ids.uuid7()"),
    (re.compile(r"(?<!\.)\buuid4\s*\("), "uuid4()", "ctx.ids.uuid7()"),
    (re.compile(r"\brandom\.\w+\s*\("), "random.*", "ctx.rng"),
    (re.compile(r"\bhttpx\.(AsyncClient|Client)\s*\("), "httpx client", "ctx.http"),
    (re.compile(r"\brequests\.\w+\s*\("), "requests.*", "ctx.http"),
    # A WRAPPER NAME MATCHES NONE OF THE ABOVE. `utc_now_iso` reads the wall
    # clock and is spelled nothing like `datetime.now`, so six live fallbacks
    # of the shape `now or utc_now_iso()` sailed through this guard -- each one
    # a replay that retrieves a different window than the recording did.
    (re.compile(r"\butc_now_iso\s*\("), "utc_now_iso()", "ctx.clock.now()"),
]

ALLOW = re.compile(r"#\s*determinism:\s*ok")

# The module that DEFINES the injectable clock/id/rng must obviously construct
# them. Exempting the definition site is not a loophole -- everything else in
# the guarded packages still has to go through RunContext.
EXEMPT = {"pipeline/types.py"}


def main() -> int:
    violations: list[str] = []

    for directory in GUARDED_DIRS:
        root = PACKAGE / directory
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT)
            if any(str(rel).endswith(e) for e in EXEMPT):
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                if ALLOW.search(line) or line.strip().startswith("#"):
                    continue
                for pattern, what, instead in FORBIDDEN:
                    if pattern.search(line):
                        violations.append(f"{rel}:{lineno}: {what} -- use {instead}")

    if violations:
        print("lint-determinism FAILED\n", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            f"\n{len(violations)} violation(s). These break VENONA_OFFLINE=1 "
            f"reproducibility.\nIf a use is genuinely safe, append "
            f"'# determinism: ok' with a reason.",
            file=sys.stderr,
        )
        return 1

    print(f"lint-determinism ok ({len(GUARDED_DIRS)} guarded packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
