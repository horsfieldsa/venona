"""Prove a diff changed COMMENTS ONLY, for the `comments` skill.

A sweep that rewrites prose across hundreds of files cannot be reviewed line by
line, and the one thing that must not happen is a statement changing inside all
that noise. This blanks every comment on both sides using `lint_comments`'s own
per-language spans and compares what is left.

    uv run python scripts/check_comments_only.py            # working tree vs HEAD
    uv run python scripts/check_comments_only.py --rev main  # vs another ref

Exits non-zero when code moved, which is what makes it usable as a gate.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lint_comments import SUFFIXES, comments_of  # noqa: E402


def _strip(path: Path, text: str) -> str:
    """The file with its comments cut out, for comparison only.

    Cut by OFFSET. Cutting a line at a time cannot work here: a JSDoc block
    whose blank line is a bare `*` has that character taken out of every other
    line in the block, and the prose then stops matching itself.
    """
    tmp = REPO_ROOT / ".comment_strip_tmp" / path.name
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(text, encoding="utf8")
    try:
        spans = comments_of(tmp)
    finally:
        tmp.unlink(missing_ok=True)

    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))

    cuts: list[tuple[int, int]] = []
    for start, end, body in spans:
        head = starts[min(start - 1, len(starts) - 1)]
        at = text.find(body, head)
        if at == -1:
            # Body not verbatim in the source (a docstring the tokenizer
            # unescaped). Take the whole line range, which is symmetric.
            cuts.append((head, starts[min(end, len(starts) - 1)]))
        else:
            cuts.append((at, at + len(body)))

    out = text
    for a, b in sorted(cuts, reverse=True):
        out = out[:a] + out[b:]
    return "\n".join(line.rstrip() for line in out.splitlines() if line.strip())


def _at_rev(rev: str, rel: str) -> str | None:
    out = subprocess.run(["git", "show", f"{rev}:{rel}"], capture_output=True, cwd=REPO_ROOT)
    return out.stdout.decode("utf8", "replace") if out.returncode == 0 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("paths", nargs="*")
    args = ap.parse_args()

    changed = args.paths or subprocess.run(
        ["git", "diff", "--name-only", args.rev],
        capture_output=True, text=True, cwd=REPO_ROOT,
    ).stdout.split()

    moved, checked, skipped = [], 0, 0
    for rel in changed:
        path = REPO_ROOT / rel
        if path.suffix not in SUFFIXES or not path.exists():
            skipped += 1
            continue
        before = _at_rev(args.rev, rel)
        if before is None:
            skipped += 1
            continue
        checked += 1
        if _strip(path, before) != _strip(path, path.read_text(encoding="utf8")):
            moved.append(rel)

    for rel in moved:
        print(f"{rel}: code changed, not just comments")
    verdict = "CODE MOVED" if moved else "comments only"
    print(f"\n{verdict} -- {checked} file(s) compared, {skipped} outside the comment suffixes.")
    return 1 if moved else 0


if __name__ == "__main__":
    raise SystemExit(main())
