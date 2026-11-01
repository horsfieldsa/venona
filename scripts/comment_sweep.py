"""The worklist and the ledger for a whole-tree comment sweep.

Sweeping 695 files is not one sitting, so the state lives in the repo: a file
is swept AT A CONTENT HASH, and editing it puts it back in the queue. After the
first full pass the standing worklist is only what has changed since.

    uv run python scripts/comment_sweep.py --state
    uv run python scripts/comment_sweep.py --next 12
    uv run python scripts/comment_sweep.py --done backend/venona/db/core.py

Ranked by unanchored PROSE rather than by comment volume: a long block that
cites nothing is both the bloat and the claim nobody can check, so the two jobs
have one queue. `.tsv` costs nothing against the density ceiling.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_comment_claims as claims  # noqa: E402
from lint_comments import comment_lines, comments_of, sources  # noqa: E402

LEDGER = REPO_ROOT / "docs" / "pm" / "comment-sweep.tsv"
ANCHOR = re.compile(r"\btests?/[\w./-]+\.py\b|\btest_\w+\b|\.spec\.ts\b|\bdocs/[\w./-]+\.md\b")


def _blob(path: Path) -> str:
    out = subprocess.run(["git", "hash-object", str(path)], capture_output=True, text=True)
    return out.stdout.strip()


def _ledger() -> dict[str, str]:
    if not LEDGER.exists():
        return {}
    rows = {}
    for line in LEDGER.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        path, blob, _ = (line.split("\t") + ["", ""])[:3]
        rows[path] = blob
    return rows


def _unanchored(path: Path) -> tuple[int, int]:
    """Lines of unanchored prose in blocks of 6+, and how many such blocks."""
    lines = blocks = 0
    for start, end, body in claims.blocks(path):
        n = end - start + 1
        if n < 6 or len(body.split()) < 12:
            continue
        flat = claims.FLAT.sub(" ", body)
        if ANCHOR.search(flat) or claims.ADR.search(flat) or claims.DATE.search(flat):
            continue
        lines += n
        blocks += 1
    return lines, blocks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", action="store_true")
    ap.add_argument("--next", type=int, metavar="N")
    ap.add_argument("--done", nargs="*", default=None)
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    if args.done is not None:
        done = _ledger()
        for rel in args.done:
            done[rel] = _blob(REPO_ROOT / rel)
        today = date.today().isoformat()
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        head = "# path\tblob\tswept\tSee the comments skill. A file re-enters the queue when it changes."
        body = "\n".join(f"{p}\t{b}\t{today}" for p, b in sorted(done.items()))
        LEDGER.write_text(f"{head}\n{body}\n")
        print(f"{len(args.done)} file(s) recorded swept {today}{' -- ' + args.note if args.note else ''}")
        return 0

    done = _ledger()
    rows = []
    for path in sources():
        rel = str(path.relative_to(REPO_ROOT))
        stale = rel in done and done[rel] != _blob(path)
        if rel in done and not stale:
            continue
        prose, blocks = _unanchored(path)
        rows.append((prose, blocks, comment_lines(comments_of(path)), rel, stale))
    rows.sort(reverse=True)

    if args.state:
        swept = sum(1 for path in sources() if str(path.relative_to(REPO_ROOT)) in done)
        stale = sum(1 for r in rows if r[4])
        print(f"swept   : {swept - stale} file(s) at their current content")
        print(f"stale   : {stale} swept then edited")
        print(f"unswept : {len(rows) - stale}")
        print(f"unanchored prose still in the queue: {sum(r[0] for r in rows)} lines")
        return 0

    for prose, blocks, counted, rel, stale in rows[: args.next or 20]:
        mark = "stale" if stale else "new"
        print(f"{prose:>5} lines / {blocks:>3} blocks   {counted:>5} counted   {mark:<5}  {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
