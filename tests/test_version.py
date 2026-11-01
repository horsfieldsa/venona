"""One release, and every manifest that declares one following it.

THE RATCHET. Before this existed the four manifests said 0.1.0, 0.1.0, 1.0.0
and 0.2.0, nothing compared them, and the version the hub displayed for every
domain was a number no one had ever updated. A test rather than only a lint
target because `make lint` is a habit and this is a correctness claim: the
version a surface reports has to be the version the repo is on.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _release() -> str:
    return (REPO / "VERSION").read_text().strip()


def test_version_file_is_three_integers() -> None:
    """Chrome refuses anything else in an extension manifest, and the manifest
    is the one place a version is externally mandatory."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", _release())


def test_every_python_manifest_declares_the_release() -> None:
    release = _release()
    for rel in ("pyproject.toml", "hub/pyproject.toml"):
        text = (REPO / rel).read_text()
        match = re.search(r'(?m)^version = "([^"]*)"', text)
        assert match is not None, f"{rel} declares no version"
        assert match.group(1) == release, f"{rel} says {match.group(1)}, VERSION says {release}"


def test_every_extension_manifest_declares_the_release() -> None:
    release = _release()
    for rel in ("extension/manifest.json", "extension/package.json"):
        assert json.loads((REPO / rel).read_text())["version"] == release, rel


def test_lockfile_declares_the_release() -> None:
    """The declaration nothing edits by hand, and the easiest to leave behind.

    Both images install `--frozen`, so a lockfile a release behind is what
    actually ships -- and any bare `uv run` after a bump rewrites it, turning a
    forgotten `uv lock` into an uncommitted diff rather than a failure.
    """
    text = (REPO / "uv.lock").read_text()
    release = _release()
    for name in ("venona", "venona-hub"):
        match = re.search(rf'(?m)^name = "{name}"\nversion = "([^"]*)"', text)
        assert match is not None, f"uv.lock holds no {name} entry"
        assert match.group(1) == release, f"uv.lock says {name} {match.group(1)}, not {release}"


def test_check_mode_passes_on_the_tree() -> None:
    """What `make lint-version` runs. Green here means a bump was propagated."""
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "version.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_check_mode_refuses_a_manifest_left_behind(tmp_path: Path) -> None:
    """The ratchet has to FAIL, not fix.

    Exercised over a copy of the tree rather than by editing it: a linter that
    repaired what it found would let a bump land half-applied and report
    success, which is the failure this whole file exists to make impossible.
    """
    import shutil

    for rel in ("VERSION", "scripts/version.py", "pyproject.toml", "hub/pyproject.toml"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)
    for rel in ("extension/manifest.json", "extension/package.json"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)

    (tmp_path / "VERSION").write_text("9.9.9\n")
    done = subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / "version.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 1
    assert "pyproject.toml" in done.stderr
    # Nothing was written: the copy still holds the old number.
    assert '\nversion = "9.9.9"' not in (tmp_path / "pyproject.toml").read_text()


def test_the_pyproject_splice_cannot_hit_ruffs_target_version() -> None:
    """`target-version = "py312"` sits in `[tool.ruff]` and also ends in
    `version = "..."`. Rewriting it would pin ruff to the release number."""
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import version as version_script
    finally:
        sys.path.pop(0)

    text = (REPO / "pyproject.toml").read_text()
    match = version_script._PYPROJECT.search(text)
    assert match is not None
    # The FIRST match is `[project]`'s own, on line 3, not ruff's further down.
    assert text[: match.start()].count("\n") < 5
    assert "target-version" in text, "the hazard this guards is gone; drop the test"


# ── what each surface reports at runtime ─────────────────────────────


def test_commit_is_shortened_and_absent_off_a_build_host(monkeypatch) -> None:
    """Seven characters, because the hub renders this in a table column, and
    None rather than an empty string so a surface can say `local` instead of
    leaving a cell blank."""
    sys.path.insert(0, str(REPO / "backend"))
    try:
        from venona.release import _commit
    finally:
        sys.path.pop(0)

    monkeypatch.setenv("VENONA_BUILD_COMMIT", "0123456789abcdef")
    assert _commit() == "0123456"

    # The override wins, so the production path can be exercised locally.
    monkeypatch.setenv("RENDER_GIT_COMMIT", "fedcba9876543210")
    assert _commit() == "0123456"

    monkeypatch.delenv("VENONA_BUILD_COMMIT")
    assert _commit() == "fedcba9"

    # Whitespace-only is not a commit.
    monkeypatch.setenv("RENDER_GIT_COMMIT", "   ")
    assert _commit() is None

    monkeypatch.delenv("RENDER_GIT_COMMIT")
    assert _commit() is None


def test_both_surfaces_report_the_release_they_are_installed_as() -> None:
    """The instance and the hub each read their OWN package, so a deploy of one
    cannot make the other claim a version it is not running."""
    from hub.release import RELEASE as HUB_RELEASE
    from venona.release import RELEASE as INSTANCE_RELEASE

    assert INSTANCE_RELEASE == _release()
    assert HUB_RELEASE == _release()
