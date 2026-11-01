"""A denied tool is a failure, never a degraded result.

`--permission-mode dontAsk` denies WebSearch/WebFetch while exiting 0 with
`is_error: false`. A caller that trusts the exit code gets "found nothing",
forever, silently. Contract C2 and `CLAUDE.md` both single this out; these are
the tests that keep it true.

The second test is the one that matters. `PermissionDenied` subclasses
`LlmError` subclasses `RuntimeError`, so a bare `except Exception` anywhere on
the path re-buries it — and one did, in `acronyms.py`, for the whole of S8.
"""

from __future__ import annotations

import inspect
import json

import pytest
from venona.llm.executor import PermissionDenied, parse_envelope

ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "stop_reason": "tool_use",
    "result": '{"found": false}',
    "total_cost_usd": 0.01,
    "usage": {},
    "permission_denials": [{"tool_name": "WebSearch"}, {"tool_name": "WebFetch"}],
}


@pytest.mark.negative
def test_a_denied_call_raises_rather_than_returning_a_result() -> None:
    with pytest.raises(PermissionDenied) as exc:
        parse_envelope(json.dumps(ENVELOPE), duration_ms=10)

    # The message must name the tools, or the operator cannot fix it.
    assert "WebFetch" in str(exc.value)
    assert "WebSearch" in str(exc.value)


@pytest.mark.negative
def test_a_successful_looking_envelope_is_not_enough() -> None:
    """Every field a naive caller would check says success. Only
    `permission_denials` disagrees, and it wins."""
    assert ENVELOPE["is_error"] is False
    assert ENVELOPE["subtype"] == "success"
    assert ENVELOPE["stop_reason"] == "tool_use"  # `stop_reason` is never a signal
    with pytest.raises(PermissionDenied):
        parse_envelope(json.dumps(ENVELOPE), duration_ms=10)


@pytest.mark.negative
def test_the_acronym_lookup_loop_re_raises_instead_of_warning() -> None:
    """Regression guard for the swallow.

    `run_acronyms` catches broad `Exception` around `lookup_web` on purpose —
    one dead third-party page must not fail a run. `PermissionDenied` has to be
    re-raised *before* that handler, and since the swallow is only observable
    through a live model call, the source is what we can assert on offline.
    """
    from venona.pipeline import acronyms

    source = inspect.getsource(acronyms.run_acronyms)
    denied = source.index("except PermissionDenied:")
    broad = source.index("except Exception")

    assert denied < broad, "PermissionDenied must be caught before the broad handler"
    assert "raise" in source[denied : source.index("\n", source.index("raise", denied))]
