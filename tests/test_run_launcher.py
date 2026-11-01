"""What `run_launcher` promises the row it writes.

`tests/test_orchestrator_smoke.py` drives a whole run through it; this holds the
contract between a unit of work and the run row, which is the seam a stage never
touches and therefore nothing else asserts.
"""

from __future__ import annotations


def test_a_failing_unit_of_work_records_why() -> None:
    """A run that failed and says nothing is the one state that helps nobody.

    `LaunchOutcome` carried no `error`, so the launcher wrote NULL to
    `runs.error` for every failure a unit of work reported itself. The reason
    reached the `stage_finished` event and stopped there. A reader's personal
    brief exhausted five structured-output retries and the run listed as
    `failed` with an empty error, which is what sent someone to the event log to
    find out what a column was for.
    """
    from venona.services.run_launcher import LaunchOutcome

    assert LaunchOutcome(status="failed", error="boom").error == "boom"
    # Default stays None: a success has nothing to say, and the launcher's own
    # pessimistic "interrupted" must not be overwritten by an empty string.
    assert LaunchOutcome().error is None


def test_detail_says_nothing_the_stage_count_does_not() -> None:
    """`detail` is an artifact of `len(stages)`, and drifting is how it lies.

    `LiveRunPanel` draws a spine for `pipeline` and a status line for `single`,
    and argues that a one-segment spine states something false: "a one-stage run
    gets a status line, not a spine". `rewrite` declared one stage and
    `pipeline`, so it drew exactly that.

    Held as an assertion rather than derived outright because `detail` is on the
    wire (contract C11) and the client reads it. This is what stops the next
    kind disagreeing with itself.
    """
    from venona.services.run_types import RUN_TYPES

    disagreeing = {
        kind: (len(rt.stages), rt.detail)
        for kind, rt in RUN_TYPES.items()
        if (len(rt.stages) > 1) != (rt.detail == "pipeline")
    }
    assert not disagreeing, (
        f"{disagreeing} declare a `detail` their stage count contradicts. "
        "More than one stage is `pipeline`; exactly one is `single`."
    )
