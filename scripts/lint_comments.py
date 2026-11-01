#!/usr/bin/env python3
"""Comments explain why the code is what it is, and nothing else.

Three things go wrong, and only the third is visible without looking:

  1. CHANGE NARRATION. A comment that says what the code used to be is writing
     the changelog in the wrong file. It is true on the day it lands and rots
     from then on, it is invisible to `git log` where the same sentence would
     have been useful, and it costs every future reader the paragraph.
  2. VOLUME. This repo reached 21.7% comment lines, with single files past 60%.
     At that density the load-bearing sentence -- the timezone note on `isoDay`,
     the `cssTarget` pin -- is buried in the prose around it, and the reader
     skims all of it.
  3. LEAKAGE. Six files reach the browser without passing a minifier. See
     `tooling/vite-strip-comments.mjs`, which strips them at build time; this
     is the check that notices when a new one appears.

It works on EXTRACTED COMMENTS, not raw text, for the reason `lint_readonly.py`
gives: a grep over raw source fires on its own explanation, and that teaches the
next person to delete the explanation. So the tokenizers below skip strings,
templates and regex literals, and this file exempts itself from (1) -- its
`NARRATION` table is code, but the docstring you are reading is not.

Escape hatch: `comment-history: ok` on the offending line, with the reason.

Run: make lint-comments
     uv run python scripts/lint_comments.py --report      # numbers, no verdict
     uv run python scripts/lint_comments.py --top 25     # the densest files
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# ── what gets read ──────────────────────────────────────────────────

ROOTS = [
    "backend",
    "hub",
    "notices",
    "web/src",
    "extension/src",
    "extension/scripts",
    "scripts",
    "tooling",
]

#: Build configs sit at a project root rather than under `src/`, and they were
#: among the densest files in the repo.
EXTRA_FILES = [
    "web/vite.config.ts",
    "web/vitest.config.ts",
    "extension/vite.main.config.ts",
    "extension/vite.content.config.ts",
    "extension/vite.theme.config.ts",
    "hub/web/vite.config.ts",
]

SUFFIXES = {".py", ".ts", ".tsx", ".vue", ".css", ".mjs", ".js"}

EXCLUDE = {"node_modules", "dist", "generated", "__pycache__", ".venv", "versions"}

#: A generated file's comments belong to whatever emitted it. `schema.d.ts` alone
#: carries 3,419 of them, and `make lint-generated` fails if a byte of it moves.
#: Detected by header rather than listed by name, so a new emitter is covered.
GENERATED = re.compile(r"auto-generated|do not edit|do not make direct changes", re.I)


def is_generated(text: str) -> bool:
    return bool(GENERATED.search("\n".join(text.splitlines()[:6])))


#: BUILT artifacts that no minifier touches, checked rather than their sources.
#: `tooling/vite-strip-comments.mjs` is what empties them; this notices when it
#: stops working, or when a new unminified path appears that it does not cover.
#: Checking the sources instead would forbid the CSP reasoning in
#: `public/theme-boot.js`, which is exactly the kind of comment worth keeping.
SHIPPED_ARTIFACTS = [
    "web/dist/index.html",
    "web/dist/theme-boot.js",
    "extension/dist/options.html",
    "extension/dist/mentions.css",
    "hub/hub/static/app/index.html",
    "hub/hub/static/app/theme-boot.js",
]

# ── the bar ─────────────────────────────────────────────────────────

#: Ceiling on comment lines as a share of all lines, per file and repo-wide.
#:
#: These are a RATCHET, set at what the tree measures today, not an ideal. They
#: exist to stop the number climbing back; lower them when a real cut lands,
#: never raise them to make a commit pass.
#:
#: 32% is high for a codebase and is not, on inspection, mostly slack: what
#: survived the narration sweep is largely measurements the tests assert
#: (`tokens.css`), timing tables (`useCytoscape.ts`) and footgun warnings
#: (`middleware.py`, `sources/http.py`). Cutting to a conventional 8-10% would
#: mean deleting those. Whether to is a judgement for a person, so this file
#: measures rather than decides.
MAX_FILE_RATIO = 70
MAX_TOTAL_RATIO = 32

#: Below this a ratio says nothing -- a 12-line module with a 6-line header is
#: fine, and flagging it would only teach people to pad the file.
MIN_LINES = 40

ALLOW = "comment-history: ok"

#: Phrases that describe an EARLIER STATE of the code. A reader who has never
#: seen that state gains nothing, and `git log` is where the sentence belongs.
NARRATION = [
    (r"\bno longer\b", "says what the code stopped doing"),
    (r"\bused to\b", "says what the code used to be"),
    (r"\bpreviously\b", "dates the comment against an earlier version"),
    (
        r"\binstead of (?:a |an |the )?(?:one|single|three|two|hand|separate)",
        "compares against a version that is gone",
    ),
    (r"\bit (?:now|currently)\b", "contrasts with a state the reader never saw"),
    (r"\bwe now\b", "contrasts with a state the reader never saw"),
    (
        r"\bnow (?:uses|lives|returns|carries|reads|does|happens|holds)\b",
        "contrasts with a state the reader never saw",
    ),
    (r"\b(?:was|were) renamed\b", "renames belong in the commit"),
    (r"\brenamed from\b", "renames belong in the commit"),
    (r"\bmoved (?:from|here from)\b", "moves belong in the commit"),
    (r"\bused to (?:be|live|carry|have)\b", "says what the code used to be"),
    (r"\bthe old\b", "describes a version that is gone"),
]

#: Files whose comments are ABOUT narration, and would otherwise report
#: themselves. Each needs a reason, in the idiom of `test_route_contracts.py`.
EXEMPT_NARRATION = {
    "scripts/lint_comments.py": "its subject is the phrases it forbids",
}

# ── extraction ──────────────────────────────────────────────────────

Span = tuple[int, int, str]  # start line, end line, text


def _python_comments(source: str) -> list[Span]:
    """COMMENT tokens plus every docstring. Docstrings carry most of the prose."""
    spans: list[Span] = []
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(source.encode()).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return spans
    for token in tokens:
        if token.type is tokenize.COMMENT:
            spans.append((token.start[0], token.start[0], token.string))

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except SyntaxError:
        return spans
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                spans.append((first.lineno, first.end_lineno or first.lineno, first.value.value))
    return spans


def _c_style_comments(code: str, start_line: int = 1) -> list[Span]:
    """`//` and `/* */`, skipping strings, templates and regex literals.

    The same walk as `tooling/vite-strip-comments.mjs`. Two implementations of
    one idea is a cost; the alternative is this linter shelling out to node.
    """
    spans: list[Span] = []
    i, n = 0, len(code)
    line = start_line

    def bump(text: str) -> None:
        nonlocal line
        line += text.count("\n")

    while i < n:
        c = code[i]

        if c in "\"'":
            start = i
            i += 1
            while i < n and code[i] != c:
                i += 2 if code[i] == "\\" else 1
            i += 1
            bump(code[start:i])
            continue

        if c == "`":
            start = i
            i += 1
            depth = 0
            while i < n:
                if code[i] == "\\":
                    i += 2
                    continue
                if depth == 0 and code[i] == "`":
                    i += 1
                    break
                if code[i] == "$" and i + 1 < n and code[i + 1] == "{":
                    depth += 1
                    i += 2
                    continue
                if depth > 0 and code[i] == "}":
                    depth -= 1
                    i += 1
                    continue
                i += 1
            bump(code[start:i])
            continue

        if c == "/" and i + 1 < n and code[i + 1] == "/":
            end = code.find("\n", i)
            end = n if end == -1 else end
            spans.append((line, line, code[i:end]))
            i = end
            continue

        if c == "/" and i + 1 < n and code[i + 1] == "*":
            end = code.find("*/", i + 2)
            stop = n if end == -1 else end + 2
            text = code[i:stop]
            spans.append((line, line + text.count("\n"), text))
            bump(text)
            i = stop
            continue

        if c == "/" and _opens_regex(code, i):
            start = i
            i += 1
            in_class = False
            while i < n:
                if code[i] == "\\":
                    i += 2
                    continue
                if code[i] == "[":
                    in_class = True
                elif code[i] == "]":
                    in_class = False
                elif code[i] == "/" and not in_class:
                    i += 1
                    break
                elif code[i] == "\n":
                    break
                i += 1
            bump(code[start:i])
            continue

        if c == "\n":
            line += 1
        i += 1

    return spans


_REGEX_PRECEDING = {
    "return",
    "typeof",
    "instanceof",
    "in",
    "of",
    "new",
    "delete",
    "void",
    "throw",
    "case",
    "do",
    "else",
    "yield",
    "await",
}


def _opens_regex(code: str, i: int) -> bool:
    j = i - 1
    while j >= 0 and code[j].isspace():
        j -= 1
    if j < 0:
        return True
    c = code[j]
    if c in ")]}":
        return False
    if c.isalnum() or c in "_$":
        k = j
        while k >= 0 and (code[k].isalnum() or code[k] in "_$"):
            k -= 1
        return code[k + 1 : j + 1] in _REGEX_PRECEDING
    return True


_HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")
_BLOCK = re.compile(r"<(script|style)\b[^>]*>([\s\S]*?)</\1>", re.I)


def _html_comments(text: str, start_line: int = 1) -> list[Span]:
    spans = []
    for match in _HTML_COMMENT.finditer(text):
        line = start_line + text[: match.start()].count("\n")
        spans.append((line, line + match.group().count("\n"), match.group()))
    return spans


def _vue_comments(source: str) -> list[Span]:
    """`<script>` and `<style>` get the C walk; what is left is template."""
    spans: list[Span] = []
    remainder = source
    for match in _BLOCK.finditer(source):
        start_line = 1 + source[: match.start(2)].count("\n")
        spans.extend(_c_style_comments(match.group(2), start_line))
        remainder = remainder.replace(match.group(2), "\n" * match.group(2).count("\n"), 1)
    spans.extend(_html_comments(remainder))
    return spans


def comments_of(path: Path) -> list[Span]:
    source = path.read_text(encoding="utf8", errors="replace")
    if path.suffix == ".py":
        return _python_comments(source)
    if path.suffix == ".vue":
        return _vue_comments(source)
    if path.suffix in {".html", ".htm"}:
        return _html_comments(source)
    return _c_style_comments(source)


# ── walking ─────────────────────────────────────────────────────────


def sources() -> list[Path]:
    found: list[Path] = []
    for root in ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            if EXCLUDE & set(path.relative_to(REPO_ROOT).parts):
                continue
            if is_generated(path.read_text(encoding="utf8", errors="replace")):
                continue
            found.append(path)
    for name in EXTRA_FILES:
        path = REPO_ROOT / name
        if path.exists():
            found.append(path)
    return sorted(set(found))


def shipped_files() -> tuple[list[Path], list[str]]:
    """Built artifacts, and the names of the ones that have not been built."""
    found: list[Path] = []
    missing: list[str] = []
    for name in SHIPPED_ARTIFACTS:
        path = REPO_ROOT / name
        if path.exists():
            found.append(path)
        else:
            missing.append(name)
    return found, missing


def comment_lines(spans: list[Span]) -> int:
    covered: set[int] = set()
    for start, end, _ in spans:
        covered.update(range(start, end + 1))
    return len(covered)


def _budget() -> int:
    """How many counted comment lines this change may still add. Costs one run.

    THE QUESTION TO ASK BEFORE WRITING, not after. The repo has sat within a
    line of the ceiling for months, so a slice that writes freely and checks at
    the end unpicks itself backwards -- which is expensive and lands on whatever
    prose is easiest to cut rather than whatever is least load-bearing.
    """
    # Counted exactly as `main` counts, every file and no minimum, or the
    # budget would disagree with the gate it exists to predict.
    total_comments = total_lines = 0
    for path in sources():
        total_lines += len(path.read_text(encoding="utf8", errors="replace").splitlines())
        total_comments += comment_lines(comments_of(path))

    ratio = total_comments * 100 // max(total_lines, 1)
    baseline = _baseline_ratio()
    print(
        f"counted now      : {total_comments}/{total_lines} ({total_comments * 100 / total_lines:.3f}%)"
    )
    if baseline is None:
        print("baseline (merge) : unknown")
    else:
        print(
            f"baseline (merge) : {baseline[0]}/{baseline[1]} ({baseline[0] * 100 / baseline[1]:.3f}%)"
        )
    print(f"ceiling          : {MAX_TOTAL_RATIO}%")

    # Lines addable before the floor-compared ratio ticks over. Both sides grow
    # with the comment, which is why this is not simply `limit - comments`.
    limit = (MAX_TOTAL_RATIO + 1) / 100
    slack = int((limit * total_lines - total_comments) / (1 - limit))
    print(f"\nroom for about {max(slack, 0)} more counted comment line(s).")
    print(f"counted suffixes : {' '.join(sorted(SUFFIXES))}")
    print("free             : tests/, .sql, .yaml, Dockerfile, .md")
    return 0


def _baseline_ratio() -> tuple[int, int] | None:
    """The (comments, lines) at the merge base, or None when git cannot answer.

    COUNTS, NOT A PERCENTAGE. A floored integer is far too coarse to compare:
    32.99% and 33.49% are both "33", so a change adding two thousand comment
    lines reads as no worse than the baseline. Measured -- that is exactly what
    the first version of this check did.

    Read from the merge-base rather than the working tree, so a long-lived
    branch is measured against what it will merge into. None on any git failure
    -- a shallow clone, a detached worktree, no remote -- and the caller falls
    back to the absolute ceiling, which is the conservative direction.
    """
    import subprocess

    for ref in ("origin/master", "master"):
        try:
            head = subprocess.run(
                ["git", "merge-base", "HEAD", ref],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout.strip()
            counted = subprocess.run(
                ["git", "show", f"{head}:scripts/lint_comments.py"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except Exception:  # noqa: BLE001 - any git failure means "cannot say"
            continue
        if counted.returncode == 0:
            return _ratio_at(head)
    return None


def _ratio_at(rev: str) -> tuple[int, int] | None:
    """Count one revision's (comments, lines) in a throwaway worktree.

    Expensive enough to be worth saying why it is not cached: this runs once per
    `make lint`, and a stale cache would report a baseline that no longer exists
    -- which fails a clean change or passes a bad one, both silently.
    """
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="venona-comment-baseline-") as scratch:
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", "--quiet", scratch, rev],
                cwd=REPO_ROOT,
                capture_output=True,
                timeout=60,
                check=True,
            )
            out = subprocess.run(
                [sys.executable, "scripts/lint_comments.py", "--report"],
                cwd=scratch,
                capture_output=True,
                text=True,
                timeout=180,
                check=True,
            ).stdout
        except Exception:  # noqa: BLE001
            return None
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", scratch],
                cwd=REPO_ROOT,
                capture_output=True,
                timeout=30,
            )
    match = re.search(r"comment lines\s*:\s*(\d+)/(\d+)", out)
    return (int(match.group(1)), int(match.group(2))) if match else None


def main() -> int:
    if "--budget" in sys.argv:
        return _budget()

    report = "--report" in sys.argv
    top = 0
    if "--top" in sys.argv:
        index = sys.argv.index("--top")
        top = int(sys.argv[index + 1]) if index + 1 < len(sys.argv) else 25
    narration: list[str] = []
    dense: list[tuple[int, str]] = []
    leaked: list[str] = []
    total_lines = 0
    total_comments = 0
    per_file: list[tuple[int, int, int, str]] = []

    for path in sources():
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf8", errors="replace")
        lines = text.splitlines()
        spans = comments_of(path)
        counted = comment_lines(spans)
        total_lines += len(lines)
        total_comments += counted

        if rel not in EXEMPT_NARRATION:
            for start, _, body in spans:
                for pattern, why in NARRATION:
                    if re.search(pattern, body, re.I):
                        source_line = lines[start - 1] if start <= len(lines) else ""
                        if ALLOW in source_line:
                            continue
                        narration.append(f"{rel}:{start}: {why}")
                        break

        if len(lines) >= MIN_LINES:
            ratio = counted * 100 // len(lines)
            per_file.append((ratio, counted, len(lines), rel))
            if ratio > MAX_FILE_RATIO:
                dense.append((ratio, f"{rel}: {ratio}% ({counted}/{len(lines)} lines)"))

    built, unbuilt = shipped_files()
    for path in built:
        spans = [s for s in comments_of(path) if not s[2].lstrip("/*").startswith("!")]
        if spans:
            rel = str(path.relative_to(REPO_ROOT))
            leaked.append(f"{rel}: {len(spans)} comment(s), first at line {spans[0][0]}")

    total_ratio = total_comments * 100 // max(total_lines, 1)

    if top:
        ranked = sorted(per_file, reverse=True)[:top]
        print(f"densest {len(ranked)} of {len(per_file)} files:\n")
        for ratio, counted, count_lines, rel in ranked:
            print(f"  {ratio:3d}%  {counted:5d}/{count_lines:5d}  {rel}")
        print(f"\ntotal {total_comments}/{total_lines} ({total_ratio}%)")
        return 0

    if report:
        print(f"comment lines : {total_comments}/{total_lines} ({total_ratio}%)")
        print(f"narration     : {len(narration)}")
        print(f"over {MAX_FILE_RATIO}%      : {len(dense)} files")
        print(f"shipped raw   : {len(leaked)} files ({len(unbuilt)} not built)")
        return 0

    failed = False

    if narration:
        failed = True
        print(f"change narration in comments ({len(narration)}):\n")
        for item in narration[:40]:
            print(f"  {item}")
        if len(narration) > 40:
            print(f"  ... and {len(narration) - 40} more")
        print(
            "\nA comment describes the code as it stands, to a reader who has never\n"
            f"seen an earlier version. Put the history in the commit message. If the\n"
            f"phrase is describing current behaviour, append `{ALLOW}` with the reason."
        )

    if dense:
        failed = True
        print(f"\nfiles over {MAX_FILE_RATIO}% comment lines ({len(dense)}):\n")
        for _, item in sorted(dense, reverse=True):
            print(f"  {item}")
        print(
            "\nAt this density the sentence that matters is buried in the prose around\n"
            "it. Keep the comment to what the code cannot say; the rest is documentation\n"
            "and belongs in docs/."
        )

    if leaked:
        failed = True
        print(f"\ncomments in files that ship without a minifier ({len(leaked)}):\n")
        for item in leaked:
            print(f"  {item}")
        print(
            "\nThese reach a reader's devtools. `tooling/vite-strip-comments.mjs` strips\n"
            "them at build time -- this is the backstop, not the mechanism. Something\n"
            "has stopped it working, or a new unminified path has appeared."
        )

    # A DELTA, NOT A CLIFF. The absolute ceiling was a ratchet: the repo has sat
    # within a line of it for months, so whichever slice added the next comment
    # failed regardless of whether that comment was any good, and commits
    # existed only to pay the tax back ("hold the comment ceiling across the
    # merge"). What the ceiling is actually for is density not GROWING, and that
    # is a question about a change rather than about a total.
    #
    # Over the ceiling, a change must not make the ratio worse. Under it, there
    # is room and the absolute limit is the bound again.
    # LAZY, because the baseline costs a throwaway worktree and a second full
    # scan. Under the ceiling nothing needs asking, which is the common case.
    baseline = _baseline_ratio() if total_ratio > MAX_TOTAL_RATIO else None
    # CROSS-MULTIPLIED, so the comparison is exact and needs no float. `worse`
    # is `now/now_lines > was/was_lines` with both denominators positive.
    worse = baseline is not None and total_comments * baseline[1] > baseline[0] * total_lines
    if total_ratio > MAX_TOTAL_RATIO and worse:
        was = f"{baseline[0]}/{baseline[1]}"
        failed = True
        print(
            f"\nthis change raises the comment ratio ({was} -> "
            f"{total_comments}/{total_lines}), and it is already over the "
            f"{MAX_TOTAL_RATIO}% ceiling.\n"
            f"Trim what this change ADDED rather than what was already there, and "
            f"DELETE lines -- rewording a paragraph shorter usually nets zero.\n"
            f"Only {' '.join(sorted(SUFFIXES))} count: tests/, .sql, .yaml and "
            f"Dockerfiles are free."
        )
    elif total_ratio > MAX_TOTAL_RATIO and baseline is None:
        failed = True
        print(
            f"\nrepo-wide comment ratio {total_ratio}% is over the {MAX_TOTAL_RATIO}% ceiling "
            f"({total_comments}/{total_lines} lines), and the baseline could not be read."
        )

    if failed:
        return 1

    print(
        f"comments ok ({total_comments}/{total_lines} lines, {total_ratio}%, "
        f"{len(sources())} files)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
