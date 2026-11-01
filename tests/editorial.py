"""The editorial verdict table, loaded and validated.

`fixtures/curation/editorial.yaml` records what a person ruled about what a run
found. Unlike `decisions.py`, this loader plants nothing: there is no
deterministic consumer to measure a model's editorial judgement against, so
what the suite can assert is that the table is WELL FORMED and that every row
still names something real.

That is not a small guarantee. The table's job is to keep a ruled row out of
the next sample, and a row whose key is misspelt, whose verdict is invented, or
whose subject no longer exists suppresses nothing -- silently, because a
suppression that does not fire looks exactly like a row nobody has judged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "fixtures/curation/editorial.yaml"

#: What each kind of subject may be ruled. Per kind, because a vocabulary wide
#: enough for all of them is one where `real` is a legal verdict on a
#: relationship -- and the whole point of the table is that a reader can tell
#: what was decided without re-reading the row's reason.
VERDICTS: dict[str, frozenset[str]] = {
    "relation": frozenset({"supported", "unsupported", "true_but_unevidenced"}),
    "entity": frozenset({"real", "not_a_thing"}),
    "cluster": frozenset({"rightly_untold", "should_have_been_told"}),
    "source": frozenset({"healthy", "dark"}),
}

#: The deterministic consequence a verdict implies, where it has one.
CHECKS = frozenset({"retracted", "ambiguous", "merged", "none"})


@dataclass(frozen=True)
class Verdict:
    key: str
    kind: str
    subject: str
    verdict: str
    reason: str
    check: str = "none"


def load() -> list[Verdict]:
    raw = yaml.safe_load(TABLE.read_text()) or {}
    return [_case(row) for row in (raw.get("cases") or [])]


def _case(row: dict[str, Any]) -> Verdict:
    kind = row.get("kind")
    if kind not in VERDICTS:
        raise ValueError(f"{row.get('key')!r}: unknown kind {kind!r}")
    # Stripped before the emptiness test, so a reason of three spaces is
    # refused the way a missing one is.
    reason = (row.get("reason") or "").strip()
    if not reason:
        raise ValueError(f"{row.get('key')!r}: every row needs a written reason")
    if row.get("verdict") not in VERDICTS[kind]:
        raise ValueError(f"{row.get('key')!r}: {row.get('verdict')!r} is not a {kind} verdict")
    check = row.get("check", "none")
    if check not in CHECKS:
        raise ValueError(f"{row.get('key')!r}: unknown check {check!r}")
    return Verdict(
        key=row["key"],
        kind=kind,
        subject=row["subject"],
        verdict=row["verdict"],
        reason=reason,
        check=check,
    )
