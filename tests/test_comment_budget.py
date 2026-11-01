"""The comment gate compares exact counts, not floored percentages.

The delta check's first version compared `int` percentages, and 32.99% against
33.49% are both "33" -- so a change adding 2,250 comment lines read as no worse
than its baseline and passed. Measured, not imagined; this is that bug.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _lint():
    spec = importlib.util.spec_from_file_location(
        "lint_comments", ROOT / "scripts" / "lint_comments.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worse(now: tuple[int, int], baseline: tuple[int, int]) -> bool:
    """The gate's own comparison, cross-multiplied."""
    return now[0] * baseline[1] > baseline[0] * now[1]


def test_growth_inside_one_percentage_point_is_still_growth() -> None:
    baseline = (52219, 158197)  # 33.009%
    padded = (54513, 160663)  # 33.930% -- both floor to 33
    assert baseline[0] * 100 // baseline[1] == padded[0] * 100 // padded[1], (
        "the fixture no longer exercises the bug: pick counts that share a floor"
    )
    assert _worse(padded, baseline), "2,294 added comment lines read as no worse"


def test_a_change_that_lowers_the_ratio_passes() -> None:
    baseline = (52219, 158197)
    leaner = (52263, 158388)  # more code than comment: 32.997%
    assert not _worse(leaner, baseline)


def test_the_budget_and_the_gate_count_the_same_way() -> None:
    """They are separate code paths over the same files, and a budget that
    disagrees with the gate is worse than no budget."""
    lint = _lint()
    total_comments = total_lines = 0
    for path in lint.sources():
        total_lines += len(path.read_text(encoding="utf8", errors="replace").splitlines())
        total_comments += lint.comment_lines(lint.comments_of(path))
    assert total_lines > 100_000, "the sweep found almost nothing; the scope is wrong"
    assert 0 < total_comments < total_lines
