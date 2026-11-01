"""Propagate the release in `VERSION` into every manifest that declares one.

ONE FILE SOMEONE EDITS, and four that follow it. The alternative is what this
repo had: `pyproject.toml` said 0.1.0, `hub/pyproject.toml` said 0.1.0,
`extension/manifest.json` said 1.0.0 and `extension/package.json` said 0.2.0,
none of them agreed and nothing noticed, so the version the hub showed for
every domain was a number no one had ever updated.

`--check` is the ratchet, wired into `make lint-version`. It compares in memory
and never writes, so a stale manifest FAILS rather than being quietly fixed by
the linter that found it -- the same rule `lint-generated` holds for generated
artifacts, which these are not: each of these files is authored, with one field
that follows `VERSION`.

`uv.lock` is the fifth declaration and the easiest to miss, because nothing
edits it by hand. Both images install `--frozen`, and any bare `uv run` after a
bump rewrites it -- so `make version` runs `uv lock` and `make lint-version`
runs `uv lock --check`, which is where that half is enforced.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERSION_FILE = REPO / "VERSION"

#: Anchored on the line start and on `[project]`'s own `version`, because a bare
#: `version = "..."` also matches `target-version = "py312"` in `[tool.ruff]`.
_PYPROJECT = re.compile(r'(?m)^(?P<head>version = ")(?P<value>[^"]*)(?P<tail>")')

#: The JSON key, quote-anchored so it cannot match `"manifest_version"` or
#: `"minimum_chrome_version"` -- both of which sit beside it in the manifest and
#: neither of which is a release.
_JSON = re.compile(r'(?P<head>"version":\s*")(?P<value>[^"]*)(?P<tail>")')


def release() -> str:
    """The one authored version. Trailing newline, nothing else in the file."""
    raw = VERSION_FILE.read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", raw):
        raise SystemExit(
            f"VERSION must be three dot-separated integers, not {raw!r}."
            " Chrome refuses anything else in an extension manifest."
        )
    return raw


def _splice(path: Path, pattern: re.Pattern[str], value: str) -> tuple[Path, str]:
    text = path.read_text()
    match = pattern.search(text)
    if match is None:
        raise SystemExit(f"{path.relative_to(REPO)}: no version field found to update")
    start, end = match.span("value")
    return path, text[:start] + value + text[end:]


#: (path, pattern) per manifest. `extension/` is here although nothing deploys
#: it: the Web Store submission is still ahead of us, and a version invented on
#: the day of that upload is one more number that means nothing.
TARGETS = (
    (REPO / "pyproject.toml", _PYPROJECT),
    (REPO / "hub" / "pyproject.toml", _PYPROJECT),
    (REPO / "extension" / "manifest.json", _JSON),
    (REPO / "extension" / "package.json", _JSON),
)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    value = release()
    stale: list[str] = []
    for path, pattern in TARGETS:
        target, source = _splice(path, pattern, value)
        rel = target.relative_to(REPO)
        if target.read_text() == source:
            continue
        if check:
            stale.append(str(rel))
            continue
        target.write_text(source)
        print(f"wrote {rel}")
    if stale:
        print(
            f"version: {value} in VERSION, stale elsewhere. Run `make version` and commit:",
            ", ".join(stale),
            file=sys.stderr,
        )
        return 1
    if check:
        print(f"version: every manifest declares {value}")
        return 0
    # Only reached when nothing was stale AND nothing was written, or after a
    # write; either way the lockfile is the caller's job -- `make version` runs
    # `uv lock` next, because this script must not need a resolver to run.
    print(f"version: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
