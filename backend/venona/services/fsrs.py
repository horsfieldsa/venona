"""FSRS-6 scheduling, pure and injected (docs/adr/0157).

The math is the published FSRS-6 model with its default weights -- per-user
weight optimisation needs on the order of a thousand reviews before it beats
the defaults, so v1 deliberately ships without it. `review` is a pure
function from (previous state, rating, now) to the next state: `now` is a
caller-supplied ISO string because this package may not ask what time it is,
and the interval fuzz is a hash over `(card_key, reps)` rather than a random
draw, so a replayed review lands on the identical due date.

Ratings are the classic four: 1 Again, 2 Hard, 3 Good, 4 Easy.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from venona import timeutil

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4

#: FSRS-6 default parameters w0..w20, as shipped by the reference
#: implementation (py-fsrs). w0..w3 are the initial stabilities per rating;
#: w20 sets the forgetting-curve decay.
WEIGHTS: tuple[float, ...] = (
    0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722,
    0.1666, 0.796, 1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425,
    0.0912, 0.0658, 0.1542,
)  # fmt: skip

_DECAY = -WEIGHTS[20]
_FACTOR = 0.9 ** (1.0 / _DECAY) - 1.0

#: Ten minutes, in days -- the floor under an Again interval, so a lapsed card
#: comes back within the sitting instead of tomorrow.
_MIN_INTERVAL_DAYS = 10.0 / (60.0 * 24.0)
_MAX_INTERVAL_DAYS = 36500.0
_MIN_STABILITY = 0.001


@dataclass(frozen=True)
class CardState:
    state: str  # learning | review | relearning
    stability: float
    difficulty: float
    due: str
    last_review: str
    reps: int
    lapses: int
    interval_days: float  # what was scheduled, pre-fuzz semantics included


def retrievability(stability: float, elapsed_days: float) -> float:
    """P(recall) after `elapsed_days` at stability S -- the forgetting curve."""
    if stability <= 0:
        return 0.0
    return (1.0 + _FACTOR * max(elapsed_days, 0.0) / stability) ** _DECAY


def interval_days(stability: float, retention_target: float) -> float:
    """Days until retrievability decays to the target."""
    raw = (stability / _FACTOR) * (retention_target ** (1.0 / _DECAY) - 1.0)
    return _clamp(raw, _MIN_INTERVAL_DAYS, _MAX_INTERVAL_DAYS)


def elapsed_between(earlier: str, later: str) -> float:
    """Days between two ISO timestamps, 0.0 when either fails to parse --
    a malformed stored timestamp should degrade a schedule, not a request."""
    a, b = timeutil.parse(earlier), timeutil.parse(later)
    if a is None or b is None:
        return 0.0
    return max((b - a).total_seconds() / 86400.0, 0.0)


def review(
    prev: CardState | None,
    rating: int,
    *,
    now: str,
    retention_target: float,
    card_key: str,
) -> CardState:
    """The next memory state after one graded review."""
    if rating not in (AGAIN, HARD, GOOD, EASY):
        raise ValueError(f"rating must be 1..4, got {rating!r}")

    if prev is None:
        stability = WEIGHTS[rating - 1]
        difficulty = _initial_difficulty(rating)
        state = "learning" if rating == AGAIN else "review"
        reps, lapses = 1, 0
    else:
        elapsed = elapsed_between(prev.last_review, now)
        r = retrievability(prev.stability, elapsed)
        difficulty = _next_difficulty(prev.difficulty, rating)
        if elapsed < 1.0:
            stability = _same_day_stability(prev.stability, rating)
        elif rating == AGAIN:
            stability = _forget_stability(prev.stability, prev.difficulty, r)
        else:
            stability = _recall_stability(prev.stability, prev.difficulty, r, rating)
        if rating == AGAIN:
            state = "relearning"
            lapses = prev.lapses + (1 if prev.state == "review" else 0)
        else:
            state = "review"
            lapses = prev.lapses
        reps = prev.reps + 1

    stability = max(stability, _MIN_STABILITY)
    if rating == AGAIN:
        interval = _MIN_INTERVAL_DAYS
    else:
        interval = _fuzz(interval_days(stability, retention_target), card_key, reps)

    moment = timeutil.parse(now)
    if moment is None:
        raise ValueError(f"unparseable timestamp: {now!r}")
    from datetime import timedelta

    due = timeutil.format_millis(moment + timedelta(days=interval))
    return CardState(
        state=state,
        stability=stability,
        difficulty=difficulty,
        due=due,
        last_review=now,
        reps=reps,
        lapses=lapses,
        interval_days=interval,
    )


# ── the model's pieces ───────────────────────────────────────────────────


def _initial_difficulty(rating: int) -> float:
    return _clamp(WEIGHTS[4] - (math.exp(WEIGHTS[5] * (rating - 1)) - 1.0), 1.0, 10.0)


def _next_difficulty(difficulty: float, rating: int) -> float:
    """Linear-damped step toward easy/hard, mean-reverted toward the
    difficulty an Easy first rating would set -- FSRS-6's guard against
    difficulty saturating at the rails."""
    step = difficulty + (10.0 - difficulty) * (-(WEIGHTS[6] * (rating - 3))) / 9.0
    easy_anchor = WEIGHTS[4] - (math.exp(WEIGHTS[5] * 3.0) - 1.0)
    return _clamp(WEIGHTS[7] * easy_anchor + (1.0 - WEIGHTS[7]) * step, 1.0, 10.0)


def _recall_stability(stability: float, difficulty: float, r: float, rating: int) -> float:
    penalty = WEIGHTS[15] if rating == HARD else 1.0
    bonus = WEIGHTS[16] if rating == EASY else 1.0
    growth = (
        math.exp(WEIGHTS[8])
        * (11.0 - difficulty)
        * stability ** (-WEIGHTS[9])
        * (math.exp((1.0 - r) * WEIGHTS[10]) - 1.0)
        * penalty
        * bonus
    )
    return stability * (1.0 + growth)


def _forget_stability(stability: float, difficulty: float, r: float) -> float:
    long_term = (
        WEIGHTS[11]
        * difficulty ** (-WEIGHTS[12])
        * ((stability + 1.0) ** WEIGHTS[13] - 1.0)
        * math.exp((1.0 - r) * WEIGHTS[14])
    )
    return min(long_term, stability / math.exp(WEIGHTS[17] * WEIGHTS[18]))


def _same_day_stability(stability: float, rating: int) -> float:
    multiplier = math.exp(WEIGHTS[17] * (rating - 3.0 + WEIGHTS[18])) * stability ** (-WEIGHTS[19])
    if rating >= GOOD:
        multiplier = max(multiplier, 1.0)
    return stability * multiplier


def _fuzz(interval: float, card_key: str, reps: int) -> float:
    """±5%, from a hash instead of a die: fuzz exists to de-synchronise cards
    graded together, and the hash does that without making replays diverge."""
    if interval < 2.5:
        return interval
    digest = hashlib.sha256(f"{card_key}:{reps}".encode()).digest()
    unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return _clamp(interval * (0.95 + 0.10 * unit), _MIN_INTERVAL_DAYS, _MAX_INTERVAL_DAYS)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))
