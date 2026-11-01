"""A model credential is never served, and a connected one actually wins.

Two separate properties, and the second is the one that would have been missed.
`CLAUDE_CODE_OAUTH_TOKEN` is set on every instance that exists, and both
services read a variable ahead of their own credential store -- so an operator
can connect an account, watch the page report success, and have every call keep
using the deployment's key. The suppression in `child_env` is what stops that,
and this is what stops the suppression being dropped.
"""

from __future__ import annotations

import pytest
from venona.llm.harness import Credential, resolve
from venona.services import harness_service
from venona.settings import get_settings


@pytest.fixture(autouse=True)
def _no_stored_secret():
    """Each test decides what is stored; none inherits the last one's answer."""
    harness_service.invalidate()
    yield
    harness_service.invalidate()


def test_a_stored_credential_suppresses_the_inherited_one(monkeypatch, tmp_path) -> None:
    harness = resolve("claude_code")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "the-deployments-token")
    harness_service._SECRETS[harness.key] = "the-one-an-admin-connected"

    class _Settings:
        data_dir = tmp_path

    cred = harness_service.credential_for(_Settings(), harness)
    env = harness.child_env({"CLAUDE_CODE_OAUTH_TOKEN": "the-deployments-token"}, cred)

    assert "the-deployments-token" not in env.values(), (
        "the deployment's token still reaches the child, so connecting an account "
        "changes nothing a call can see"
    )


def test_nothing_is_suppressed_when_nothing_supersedes_it(monkeypatch, tmp_path) -> None:
    """The other direction, and it is not symmetry for its own sake.

    Every instance today authenticates by environment variable. Stripping those
    names unconditionally would disconnect the entire fleet in the name of
    preferring a credential that does not exist.
    """
    harness = resolve("claude_code")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "the-deployments-token")

    class _Settings:
        data_dir = tmp_path

    cred = harness_service.credential_for(_Settings(), harness)
    env = harness.child_env({"CLAUDE_CODE_OAUTH_TOKEN": "the-deployments-token"}, cred)

    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "the-deployments-token"


def test_the_child_keeps_what_the_parent_was_given(tmp_path) -> None:
    """Inherited, minus, plus -- never an allowlist.

    `DISABLE_UPDATES` keeps a pinned binary pinned and `HOME` is where a
    rotating refresh token lives. Both arrive by inheritance, and a curated
    list drops one of them the first time somebody adds a third.
    """
    harness = resolve("claude_code")
    env = harness.child_env(
        {"DISABLE_UPDATES": "1", "HOME": "/var/data/home", "LANG": "en_US.UTF-8"},
        Credential(),
    )
    assert env["DISABLE_UPDATES"] == "1"
    assert env["HOME"] == "/var/data/home"
    assert env["LANG"] == "en_US.UTF-8"


def test_the_source_names_which_credential_is_in_force(monkeypatch, tmp_path) -> None:
    """The page has to be able to say this, because the probe cannot.

    A sign-in check reports a session as live whichever credential answered it,
    so "connected" and "still using the deployment's" look identical from the
    outside. This is the only thing that tells them apart.
    """
    harness = resolve("claude_code")

    class _Settings:
        data_dir = tmp_path

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert harness_service.source(_Settings(), harness) == "none"

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "from-the-deploy")
    assert harness_service.source(_Settings(), harness) == "environment"

    harness_service._SECRETS[harness.key] = "connected-here"
    assert harness_service.source(_Settings(), harness) == "stored"


def test_a_hint_is_derived_and_is_not_the_credential() -> None:
    hint = harness_service.hint("sk-ant-oat01-abcdefghijklmnop")
    assert "abcdefghijkl" not in hint
    assert hint.endswith("mnop")


async def test_no_response_anywhere_carries_the_stored_secret(api_client, make_database) -> None:
    """The sweep, in the shape `test_notifications_api` uses.

    A credential is stored so it can be REPLAYED, which is why it is plaintext
    -- and that is exactly why no route may hand it back. Greping every
    response is cheaper than trusting each schema to keep omitting it.
    """
    secret = "sk-ant-oat01-a-very-distinctive-value"
    harness_service._SECRETS["claude_code"] = secret

    for path in ("/api/health", "/api/admin/model", "/api/admin/model/connection"):
        response = await api_client.get(path)
        assert secret not in response.text, f"{path} served the stored credential"


async def test_a_credential_the_service_will_not_use_is_refused(monkeypatch, tmp_path) -> None:
    """A sign-in check is not a credential check, and cannot be made into one.

    `claude auth status` reports a live session for whatever token it is given,
    including a typo -- `executor.py` records that as a measured fact. So a
    check built on the probe accepts anything pasted, stores it, reports
    success, and the next run 401s. Pointing the probe at a scratch home fixed
    which store it read and not that it could not fail. The only thing that
    distinguishes a credential that works is asking the service to use it.
    """
    from venona.llm import executor as executor_module
    from venona.llm.types import HarnessHealth, LlmResult, Usage

    class _SignedInButRefused:
        def __init__(self, *a, **kw) -> None:
            pass

        async def health(self):
            return HarnessHealth(ok=True, logged_in=True)

        async def call(self, *a, **kw):
            return LlmResult(data=None, usage=Usage(), error="401 Invalid bearer token")

    monkeypatch.setattr(executor_module, "CliExecutor", _SignedInButRefused)

    probe = await harness_service.verify(
        get_settings(), resolve("claude_code"), secret="a-typo", billing_mode="subscription"
    )
    assert not probe.ok, "the check reported success on a credential the service refused"
    assert "401" in (probe.error or "")
