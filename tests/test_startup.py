"""Startup guards.

Things the process must refuse to start with, rather than discover later. Each
one here is a failure that is invisible while it is happening: the app runs, the
runs succeed, and the evidence arrives somewhere else entirely -- a bill, a log
nobody reads, a request from the internet.
"""

from __future__ import annotations

import pytest
from venona.main import _refuse_metered_billing
from venona.settings import get_settings


def _on(harness: str, mode: str):
    """A settings object for one pairing, without touching the real one."""
    settings = get_settings().model_copy(deep=True)
    settings.llm.harness = harness
    settings.llm.billing_mode = mode
    return settings


@pytest.mark.negative
def test_an_api_key_in_the_environment_refuses_to_start(monkeypatch) -> None:
    """Decision 1: venona runs on the claude CLI's subscription OAuth. An
    `ANTHROPIC_API_KEY` silently moves every model call to metered billing and
    nothing looks different -- the runs succeed and the ledger fills in.

    Prose in `.env.example` was enough while the environment was one laptop's
    `.env`. A container inherits its environment from a file someone else
    wrote, and this is the most conventional variable name in the space.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        _refuse_metered_billing(_on("claude_code", "subscription"))


def test_no_api_key_is_the_normal_case(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _refuse_metered_billing(_on("claude_code", "subscription"))


def test_an_empty_api_key_is_not_a_key(monkeypatch) -> None:
    """`ANTHROPIC_API_KEY=` in a `.env` file is how someone turns one off. It
    would be unkind to refuse to boot over an empty string."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    _refuse_metered_billing(_on("claude_code", "subscription"))


def test_a_subscription_token_is_not_a_metered_key(monkeypatch) -> None:
    """The token every deployment sets is the SUBSCRIPTION credential.

    Treating it as metered refused to boot the entire fleet: the deployment config sets
    it on every instance, so the refusal fired before anything could serve a
    request -- including the page its own message told the operator to open.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "the-deployments-token")
    _refuse_metered_billing(_on("claude_code", "subscription"))


def test_another_services_key_does_not_refuse_this_one(monkeypatch) -> None:
    """A key that cannot move THIS instance's spend is not this guard's business.

    `OPENAI_API_KEY` set for a sidecar, on an instance running Claude Code,
    stopped the app from starting -- for a variable the configured service
    never reads.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "for-something-else")
    _refuse_metered_billing(_on("claude_code", "subscription"))


@pytest.mark.negative
def test_the_configured_services_key_still_refuses(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-whatever")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        _refuse_metered_billing(_on("codex", "subscription"))


def test_an_api_plan_is_a_decision_rather_than_an_accident(monkeypatch) -> None:
    """The whole point of recording the plan: a key is only an accident when
    the instance says it is paying a different way."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-deliberate")
    _refuse_metered_billing(_on("claude_code", "api"))
