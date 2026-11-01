"""Personal relevance: derived, never re-scored.

The model scores each cluster ONCE, against the instance profile; everything
personal is pure Python over those stored axis scores. A reader's input is
emphasis WORDS per axis (more / normal / less), never raw weights: weights
are derived here (instance default x factor, renormalized to 1.0), so no
stored row can put `overall` off the 0-100 scale, and the tier cuts scale
with what a story can actually reach under the reader's weights -- the
unreachable-cut bug (ADR 0047) prevented structurally instead of validated
away.

Everything here is deterministic and side-effect free: rows in, numbers out.
The database work lives in `db/repositories/relevance.py`; the routes own
identity.
"""

from __future__ import annotations

import hashlib

from venona.db import core as db
from venona.llm.schemas import axis_ids, axis_weights, compute_overall, tier_for, tiers

#: The three words a reader may say about an axis. Factors are deliberately
#: coarse -- 2x and half -- because the input is a word, and a word that
#: quietly meant 1.07x would make the control feel dead.
EMPHASIS_FACTORS = {"less": 0.5, "normal": 1.0, "more": 2.0}


def emphasis_problem(emphasis: dict[str, str]) -> str | None:
    """The reason this emphasis cannot be saved, or None. Reader-worded: the
    router returns it verbatim in a 422."""
    known = set(axis_ids())
    for axis_id, level in emphasis.items():
        if axis_id not in known:
            return f"'{axis_id}' is not something Venona scores here"
        if level not in EMPHASIS_FACTORS:
            return f"'{level}' is not an option; use more, normal or less"
    return None


def is_drifted(emphasis: dict[str, str]) -> bool:
    """A stored emphasis referencing axis ids absent from the current rubric.

    The admin edited how stories are scored since this reader tuned theirs;
    silently renormalizing the survivors would produce an emphasis the reader
    never chose, so the caller falls back to instance defaults and says so.
    """
    return not set(emphasis).issubset(set(axis_ids()))


def derive_weights(emphasis: dict[str, str]) -> dict[str, float]:
    """Instance weights x the reader's factors, renormalized to sum 1.0.

    Iterates the rubric's own axis order so the result is deterministic for a
    given (rubric, emphasis) pair. An axis the reader never mentioned keeps
    its instance weight before renormalization.
    """
    base = axis_weights()
    raw = {
        axis_id: weight * EMPHASIS_FACTORS.get(emphasis.get(axis_id, "normal"), 1.0)
        for axis_id, weight in base.items()
    }
    total = sum(raw.values())
    if total <= 0:
        return base
    return {axis_id: value / total for axis_id, value in raw.items()}


def singular_reach(weights: dict[str, float]) -> float:
    """What a SINGULAR story can reach under these weights: the three largest
    axes at full score. The instance cuts were set against exactly this number
    (the rubric's own comment: three core axes at 5/5 top out at 62), so it is
    the honest scale factor for personal cuts.
    """
    return 100.0 * sum(sorted(weights.values(), reverse=True)[:3])


def scaled_cuts(weights: dict[str, float]) -> dict[str, int]:
    """The instance tier cuts, scaled to the reader's reachable maximum.

    A reader who spreads emphasis evenly lowers what one story can reach;
    keeping the instance cuts would then mark nothing must-read, forever and
    silently. Scaling by reach keeps each cut at the same RELATIVE height it
    has for the instance.
    """
    instance_reach = singular_reach(axis_weights())
    reach = singular_reach(weights)
    if instance_reach <= 0:
        return tiers()
    factor = reach / instance_reach
    return {name: max(1, round(cut * factor)) for name, cut in tiers().items()}


def personal_score(
    axes_json: str | None,
    *,
    weights: dict[str, float],
    cuts: dict[str, int],
) -> tuple[int, str]:
    """One cluster's (overall, tier) under the reader's weights.

    The same rollup `POST /api/rescore` replays for the instance, pointed at
    caller-supplied weights -- zero model calls is the whole feature.
    """
    stored = db.jload(axes_json, {}) or {}
    scores = {k: float((v or {}).get("score", 0)) for k, v in stored.items()}
    overall = compute_overall(scores, weights)
    return overall, tier_for(overall, cuts)


def inputs_hash(
    emphasis: dict[str, str],
    follow_ids: list[str],
    profile_hash: str,
) -> str:
    """Identifies what a personal brief was generated FROM: the reader's
    emphasis, their follows, and the profile the corpus was scored under.
    Same inputs, same hash -- a revisit is a free read; an emphasis edit
    regenerates at most once.
    """
    payload = "\0".join(
        [
            ";".join(f"{k}={emphasis[k]}" for k in sorted(emphasis)),
            ";".join(sorted(follow_ids)),
            profile_hash,
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
