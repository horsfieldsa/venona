"""The domain-blind wall: no domain-specific CODE anywhere.

The domain is configuration (`config/domain.yaml` + `profile/`); code splices
it in. This sweep holds that boundary the way `lint_repositories.py` holds the
SQL one, and for the same reason: a defense-flavoured string in a router looks
exactly like any other string at the call site, so the leak is invisible in
review and only a sweep notices. docs/adr/0144.

What is scanned: STRING LITERALS and template text -- the bytes that reach a
user or a model. Comments and docstrings are deliberately exempt: the repo's
comments narrate measured incidents from the defense corpus ("the queue header
read `Army` while the card read `U.S. Army`"), and that history is
documentation, not behaviour. Scrubbing it would destroy reasoning to satisfy
a grep.

Escape hatch, sparingly: `domain-lint: ok -- why` on the line (or the line
above, for multi-line strings). FILE_ALLOWLIST is for the one module whose
job is to BE the defense defaults.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SCAN_ROOTS = (
    REPO_ROOT / "backend" / "venona",
    REPO_ROOT / "web" / "src",
    REPO_ROOT / "extension" / "src",
    # The hub serves EVERY domain at once, so it is the one package where a
    # leaked domain term is not just a boundary violation but a wrong page.
    REPO_ROOT / "hub" / "hub",
    # Shared by both services, so a domain term here would leak into whichever
    # one nobody was thinking about.
    REPO_ROOT / "notices" / "notices",
)
EXCLUDED_PARTS = {"node_modules", "dist", "generated", "__pycache__"}

# Seeded from the de-defensing audit. Word-boundary, case-insensitive. These
# are terms whose presence in a string literal means the domain leaked back
# into code -- not every defense word (comments keep their examples), the ones
# the audit actually found hard-coded.
TERMS = (
    "defense",
    "defence",
    "military",
    "army",
    "navy",
    "air force",
    "pentagon",
    "warfighter",
    "darpa",
    "anduril",
    "raytheon",
    "northrop",
    "lockheed",
    "jadc2",
    "c4isr",
    "ngc2",
)
PATTERN = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in TERMS) + r")(?![a-z0-9])")

ALLOW = re.compile(r"domain-lint:\s*ok")

#: path (repo-relative) -> why its literals may name the domain.
FILE_ALLOWLIST = {
    "backend/venona/domain.py": (
        "DEFENSE_DEFAULTS -- the checked-in defense descriptor as code, held "
        "equal to config/domain.yaml by tests/test_domain_prompts.py"
    ),
}

SUFFIXES = {".py", ".ts", ".vue"}


def _py_literal_lines(source: str) -> set[int]:
    """Line numbers of string literals that are NOT docstrings."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))

    lines: set[int] = set()
    for node in ast.walk(tree):
        constant = (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_nodes
        )
        if constant or isinstance(node, ast.JoinedStr):
            for lineno in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                lines.add(lineno)
    return lines


BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _mask(match: re.Match[str]) -> str:
    return "".join(ch if ch == "\n" else " " for ch in match.group(0))


def _web_source(text: str) -> str:
    """TS/Vue with comments blanked; strings and template copy stay."""
    masked = BLOCK_COMMENT.sub(_mask, text)
    masked = HTML_COMMENT.sub(_mask, masked)
    kept = []
    for line in masked.splitlines():
        stripped = line.lstrip()
        kept.append("" if stripped.startswith("//") or stripped.startswith("*") else line)
    return "\n".join(kept)


def scan_file(path: Path) -> list[str]:
    rel = path.relative_to(REPO_ROOT)
    if str(rel) in FILE_ALLOWLIST:
        return []
    text = path.read_text()
    raw_lines = text.splitlines()
    violations: list[str] = []

    if path.suffix == ".py":
        literal_lines = _py_literal_lines(text)
        for lineno in sorted(literal_lines):
            line = raw_lines[lineno - 1] if lineno <= len(raw_lines) else ""
            hit = PATTERN.search(line.lower())
            if not hit:
                continue
            above = raw_lines[lineno - 2] if lineno >= 2 else ""
            if ALLOW.search(line) or ALLOW.search(above):
                continue
            violations.append(
                f"{rel}:{lineno}: {hit.group(1)!r} in a string literal -- the domain "
                f"is configuration; read it from `get_domain()` (config/domain.yaml)"
            )
    else:
        for lineno, line in enumerate(_web_source(text).splitlines(), start=1):
            hit = PATTERN.search(line.lower())
            if not hit:
                continue
            raw = raw_lines[lineno - 1] if lineno <= len(raw_lines) else ""
            above = raw_lines[lineno - 2] if lineno >= 2 else ""
            if ALLOW.search(raw) or ALLOW.search(above):
                continue
            violations.append(
                f"{rel}:{lineno}: {hit.group(1)!r} in code or template copy -- the "
                f"domain is configuration; take it from the API (/auth/mode, the "
                f"descriptor) or a prop"
            )
    return violations


def main() -> int:
    files = [
        p
        for root in SCAN_ROOTS
        if root.exists()
        for p in sorted(root.rglob("*"))
        if p.suffix in SUFFIXES
        and not set(p.parts) & EXCLUDED_PARTS
        and not p.name.endswith(".spec.ts")
    ]
    violations: list[str] = []
    for path in files:
        violations += scan_file(path)

    if violations:
        print("lint-domain FAILED\n", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        print(f"\n{len(violations)} violation(s)", file=sys.stderr)
        return 1
    print(f"lint-domain ok ({len(files)} files, {len(TERMS)} terms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
