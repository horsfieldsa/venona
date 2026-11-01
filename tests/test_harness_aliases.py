"""Switching services keeps a tuned instance working.

`llm/harness/__init__.py::model_for` is a pass-through: an unknown value goes to
the provider unchanged, so that an operator adopting a model on its release day
is not blocked by this table being a week old. That is right for a name the
provider will recognise and wrong for one belonging to somebody else -- and
`codex.py` records what the wrong half costs. Its cheap tier pointed at a model
a ChatGPT account cannot call, so every `fast` call failed against a service
that was working, until the breaker gave up on it.

An instance tuned to `grok-4.6` that switched to Codex hit exactly that, and
one tuned to `gpt-5.6-sol` that switched to Claude Code asked Anthropic for an
OpenAI model. Both are the same defect from different directions, which is why
this is a table test rather than three assertions.
"""

from __future__ import annotations

import pytest
from venona.llm.harness import known, model_for, resolve

HARNESSES = sorted(known())


def _emitted() -> set[str]:
    """Every model name any harness resolves a tier word to."""
    return {model for key in HARNESSES for model in resolve(key).aliases.values()}


@pytest.mark.parametrize("key", HARNESSES)
def test_every_tier_word_resolves(key: str) -> None:
    for tier in ("fast", "standard", "deep"):
        assert model_for(resolve(key), tier) != tier, f"{key}: {tier} is not mapped"


@pytest.mark.parametrize("key", HARNESSES)
def test_no_foreign_model_name_passes_through(key: str) -> None:
    """The ratchet. A new harness fails here until it maps the others' names.

    Its own names are exempt: `claude_code` deliberately omits `sonnet` and its
    siblings because pass-through already returns them unchanged, and listing
    them would read as the allowlist `config_service._check_model_names` argues
    against.
    """
    harness = resolve(key)
    mine = set(harness.aliases.values())
    for model in sorted(_emitted() - mine):
        assert model_for(harness, model) in mine, (
            f"{key} passes {model!r} straight through to its provider -- "
            f"map it onto one of {sorted(mine)}"
        )


@pytest.mark.parametrize("key", HARNESSES)
def test_an_unpinned_service_reports_no_mismatch(key: str) -> None:
    """An empty pin is NO pin, not a pin on the empty string.

    `grok_pinned_version` ships empty because nobody has run a version of it, and
    `settings.py` argues an invented number "would report every install as
    wrong". A bare `version == pinned` gave the empty value that same behaviour:
    `/admin/model` reporting a mismatch against nothing, on every install.
    """
    from venona.llm.harness import matches_pin

    assert matches_pin("9.9.9", "") is True
    assert matches_pin("9.9.9", "9.9.9") is True
    assert matches_pin("9.9.9", "1.0.0") is False
