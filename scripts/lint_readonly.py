#!/usr/bin/env python3
"""Contract C12 enforcement: the MCP surface never writes.

`backend/venona/api/mcp/` is handed to autonomous agents reading
adversary-controllable trade press. Everything there is a read, and that is a
property worth checking mechanically rather than remembering -- the failure mode
is a tool that quietly spends a Claude subscription or deletes a row, and it does
not announce itself.

This is a grep, in `lint_determinism.py`'s spirit: deliberately dumb, and
deliberately noisy. A false positive costs one rename or one `# read-only: ok`;
a false negative costs the corpus.

It works on CODE, not raw source: this package's prose NAMES the things it must
not do, because that is where the reasons live. A grep over raw text fires on its
own explanation, which teaches the next person to delete the explanation.

Run: make lint-readonly
"""

from __future__ import annotations

import io
import sys
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "backend" / "venona" / "api" / "mcp"

# Identifiers that have no business in a read-only layer. SQL verbs are here too:
# they only reach the wire inside a string literal, which the tokenizer drops, so
# a hit means someone built a statement out of identifiers.
FORBIDDEN = {
    "run_launcher": "starts a run; the MCP surface must never spend",
    "config_service": "writes user-authored config",
    "delete_brief": "deletes rows -- it lives in brief_service beside the reads",
    "purge_corpus": "deletes the corpus",
    "GraphWriter": "writes Neo4j",
    "project": "rebuilds the projection",
    "build_executor": "constructs an LLM executor",
    "commit": "commits a transaction",
    "write_text": "writes a file",
    "mint": "issues a credential",
    "revoke": "changes a credential",
}

ALLOW = "read-only: ok"


def main() -> int:
    violations: list[str] = []

    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_bytes()
        allowed_lines = {
            number for number, line in enumerate(source.decode().splitlines(), 1) if ALLOW in line
        }
        try:
            tokens = list(tokenize.tokenize(io.BytesIO(source).readline))
        except tokenize.TokenError as exc:  # pragma: no cover - a syntax error fails elsewhere
            violations.append(f"{path.relative_to(REPO_ROOT)}: could not tokenize ({exc})")
            continue

        for token in tokens:
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            if token.type is not tokenize.NAME:
                continue
            reason = FORBIDDEN.get(token.string)
            if reason and token.start[0] not in allowed_lines:
                violations.append(
                    f"{path.relative_to(REPO_ROOT)}:{token.start[0]}: `{token.string}` -- {reason}"
                )

    if violations:
        print("read-only violations under api/mcp/ (contract C12):\n")
        for violation in violations:
            print(f"  {violation}")
        print(
            "\nThe MCP surface is read-only. An agent that can call a write will call it\n"
            "in a loop, and there is no price quote in front of it. If this is a false\n"
            "positive, append `# read-only: ok` to the line with the reason."
        )
        return 1

    print(f"read-only ok ({len(list(PACKAGE.rglob('*.py')))} modules under api/mcp/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
