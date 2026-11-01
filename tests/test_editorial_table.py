"""The verdict table has to be well formed, because nothing else will notice.

A row's whole job is to keep a settled finding out of the next sample. A key
that is duplicated, a verdict that does not exist for that kind, or a subject
written in the wrong shape all suppress nothing -- and a suppression that never
fires looks exactly like a row nobody has judged. There is no failing query and
no empty screen to notice it by.

Nothing here asserts that a VERDICT is right. A model scored the cluster and a
model read the quote, so there is no deterministic consumer to measure an
editorial judgement against; that is the difference between this table and
`decisions.yaml`, and it is why `reason` is mandatory rather than decorative.
"""

from __future__ import annotations

import editorial
import pytest
from editorial import VERDICTS


def test_every_row_loads() -> None:
    """The loader is the validation: kind, verdict, check and a written reason
    are all refused at parse time rather than asserted here one by one."""
    assert editorial.load()


def test_keys_are_unique() -> None:
    """A key is how a row is cited in a commit message or an ADR, and yaml
    keeps both of a duplicated pair, so the collision is silent on both sides."""
    keys = [case.key for case in editorial.load()]

    assert len(keys) == len(set(keys))


def test_every_reason_is_a_sentence_somebody_wrote() -> None:
    """The point of the table is that a future reader can audit a judgement
    without re-deriving it. A reason of `wrong` cannot do that, and the loader
    only refuses an EMPTY one."""
    thin = [case.key for case in editorial.load() if len(case.reason.split()) < 12]

    assert not thin, f"these reasons are too short to audit: {thin}"


def test_an_entity_verdict_names_a_type_prefixed_id() -> None:
    """Ids are type-prefixed by contract C4. A subject written as a bare name
    matches nothing in the sample and goes quietly inert."""
    loose = [
        case.key for case in editorial.load() if case.kind == "entity" and ":" not in case.subject
    ]

    assert not loose, f"these name no entity id: {loose}"


def test_retraction_is_only_claimed_where_it_means_something() -> None:
    """`check: retracted` says an assertion should carry `retracted_at`. On an
    entity or a source there is nothing to retract, so the check would be a
    promise about a column that does not apply."""
    misplaced = [
        case.key
        for case in editorial.load()
        if case.check == "retracted" and case.kind != "relation"
    ]

    assert not misplaced, f"nothing to retract on these: {misplaced}"


@pytest.mark.parametrize("kind", sorted(VERDICTS))
def test_each_kind_declares_at_least_two_verdicts(kind: str) -> None:
    """A vocabulary of one is not a judgement. Every kind here has to be able
    to come back either way, or recording the verdict says nothing."""
    assert len(VERDICTS[kind]) >= 2
