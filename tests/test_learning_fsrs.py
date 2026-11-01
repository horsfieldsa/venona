"""The FSRS engine, pinned where it matters.

Weight VALUES are the reference implementation's and are not restated here --
what these tests hold is the model's shape: the orderings and monotonicities
a scheduler must have for the feature to make sense, and the determinism the
whole learning surface leans on. A wrong weight makes reviews mildly less
efficient; a broken ordering makes Easy come back sooner than Again.
"""

from __future__ import annotations

import pytest
from venona.services import fsrs

NOW = "2026-08-23T12:00:00.000Z"
LATER_10D = "2026-09-02T12:00:00.000Z"
KEY = "card:def:prog:example"


def _first(rating: int) -> fsrs.CardState:
    return fsrs.review(None, rating, now=NOW, retention_target=0.9, card_key=KEY)


def test_the_weights_are_fsrs6_shaped() -> None:
    assert len(fsrs.WEIGHTS) == 21


def test_first_intervals_order_by_rating() -> None:
    intervals = [_first(r).interval_days for r in (fsrs.HARD, fsrs.GOOD, fsrs.EASY)]
    assert intervals == sorted(intervals), intervals
    assert _first(fsrs.AGAIN).interval_days < _first(fsrs.HARD).interval_days


def test_again_comes_back_within_the_sitting() -> None:
    state = _first(fsrs.AGAIN)
    assert state.state == "learning"
    assert state.due < "2026-08-23T13:00:00.000Z", "an Again must return in minutes, not days"


def test_a_lapse_from_review_counts_and_relearns() -> None:
    good = _first(fsrs.GOOD)
    lapsed = fsrs.review(good, fsrs.AGAIN, now=LATER_10D, retention_target=0.9, card_key=KEY)
    assert lapsed.state == "relearning"
    assert lapsed.lapses == good.lapses + 1
    assert lapsed.stability < good.stability


def test_recall_grades_order_the_next_interval() -> None:
    good = _first(fsrs.GOOD)
    nexts = {
        rating: fsrs.review(good, rating, now=LATER_10D, retention_target=0.9, card_key=KEY)
        for rating in (fsrs.HARD, fsrs.GOOD, fsrs.EASY)
    }
    assert (
        nexts[fsrs.HARD].interval_days
        < nexts[fsrs.GOOD].interval_days
        < nexts[fsrs.EASY].interval_days
    )
    for state in nexts.values():
        assert state.reps == good.reps + 1
        assert state.lapses == good.lapses


def test_a_lower_retention_target_schedules_further_out() -> None:
    relaxed = fsrs.review(None, fsrs.GOOD, now=NOW, retention_target=0.8, card_key=KEY)
    strict = fsrs.review(None, fsrs.GOOD, now=NOW, retention_target=0.95, card_key=KEY)
    assert relaxed.interval_days > strict.interval_days


def test_retrievability_decays_with_time() -> None:
    stability = 10.0
    curve = [fsrs.retrievability(stability, t) for t in (0.0, 1.0, 10.0, 100.0)]
    assert curve[0] == pytest.approx(1.0)
    assert curve == sorted(curve, reverse=True)


def test_the_schedule_is_deterministic() -> None:
    """Fuzz comes from a hash, not a die: the same grade at the same moment
    lands on the identical due date, which is what makes replay and offline
    tests possible at all."""
    a = fsrs.review(_first(fsrs.GOOD), fsrs.EASY, now=LATER_10D, retention_target=0.9, card_key=KEY)
    b = fsrs.review(_first(fsrs.GOOD), fsrs.EASY, now=LATER_10D, retention_target=0.9, card_key=KEY)
    assert a == b


def test_fuzz_is_bounded() -> None:
    stability = 50.0
    raw = fsrs.interval_days(stability, 0.9)
    fuzzed = fsrs._fuzz(raw, KEY, 3)
    assert 0.94 * raw <= fuzzed <= 1.06 * raw


def test_same_day_review_never_shrinks_a_good_memory() -> None:
    good = _first(fsrs.GOOD)
    hour_later = "2026-08-23T13:00:00.000Z"
    again_state = fsrs.review(good, fsrs.GOOD, now=hour_later, retention_target=0.9, card_key=KEY)
    assert again_state.stability >= good.stability


def test_difficulty_stays_on_its_rails() -> None:
    state = _first(fsrs.AGAIN)
    for _ in range(20):
        state = fsrs.review(state, fsrs.AGAIN, now=LATER_10D, retention_target=0.9, card_key=KEY)
    assert 1.0 <= state.difficulty <= 10.0
