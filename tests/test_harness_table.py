"""The harness table is the interface, so the table is what is checked.

Adding a model service is meant to be a module, a row, an install line and a
fixture directory -- and nothing else. These assert the properties that make
that true, so a driver that quietly needs a change somewhere else fails here
rather than at whichever call site notices first.
"""

from __future__ import annotations

import pytest
from venona.llm.harness import (
    WEB_FETCH,
    WEB_SEARCH,
    HarnessError,
    known,
    missing_capabilities,
    model_for,
    resolve,
)


@pytest.mark.parametrize("key", known())
def test_every_row_resolves_to_a_driver(key: str) -> None:
    harness = resolve(key)
    assert harness.key == key
    assert harness.label, "a service needs a name a reader can be shown"


@pytest.mark.parametrize("key", known())
def test_the_binary_is_a_name_and_never_a_path(key: str) -> None:
    """This is what lets `llm.harness` be editable while `cli_path` is frozen.

    A selector that resolved to a path would put an operator's typing on the
    front of an exec. The table holds a program NAME, which PATH resolves --
    so the set of things that can run is the set of things installed, not the
    set of strings somebody can save.
    """
    harness = resolve(key)
    assert "/" not in harness.binary
    assert "\\" not in harness.binary
    assert not harness.binary.startswith(".")


@pytest.mark.parametrize("key", known())
def test_a_service_declares_how_it_can_be_paid_for(key: str) -> None:
    harness = resolve(key)
    assert harness.billing_modes
    assert harness.billing_modes <= {"subscription", "api"}


@pytest.mark.parametrize("key", known())
def test_a_service_declares_the_names_it_reads_on_its_own(key: str) -> None:
    """`secret_env` drives two separate refusals, so an empty one breaks both.

    It is what the child environment strips when a credential connected in the
    app supersedes the deployment's, AND what the metered-billing guard looks
    for. A service that forgets to declare its variables silently opts out of
    both -- an operator connects an account that is never used, on a bill
    nobody warned them about.
    """
    harness = resolve(key)
    assert harness.secret_env, "a service that reads no environment cannot be authenticated"
    assert all(name.isupper() for name in harness.secret_env)


@pytest.mark.parametrize("key", known())
def test_tiers_resolve_and_anything_else_passes_through(key: str) -> None:
    """Pass-through is the point, not a fallback.

    A code-side list of legal models would have to track a vendor's release
    calendar and fails in the worst direction when it lags: refusing the model
    an operator is adopting, on the day they adopt it, from the surface they
    would fix it in. `config_service._check_model_names` makes the same
    argument about the same values.
    """
    harness = resolve(key)
    for tier in ("fast", "standard", "deep"):
        assert model_for(harness, tier), f"{key} has no {tier} model"
    assert model_for(harness, "some-model-released-this-morning") == (
        "some-model-released-this-morning"
    )


@pytest.mark.parametrize("key", known())
def test_capabilities_are_drawn_from_the_declared_vocabulary(key: str) -> None:
    harness = resolve(key)
    assert harness.capabilities <= {WEB_SEARCH, WEB_FETCH}


def test_an_unknown_service_is_refused_by_name() -> None:
    with pytest.raises(HarnessError) as caught:
        resolve("not-a-service")
    assert "not-a-service" in str(caught.value)


def test_a_missing_capability_is_graded_rather_than_just_missing() -> None:
    """Required and preferred are different answers, and the caller decides.

    A required gap refuses before anything is spent; a preferred one narrows
    the call and says so in the run's messages. Collapsing them would either
    refuse work that could have run, or run it degraded with nothing recorded.
    """

    class _NoWeb:
        capabilities = frozenset()

    blocked, degraded = missing_capabilities(
        _NoWeb(), {WEB_SEARCH: "required", WEB_FETCH: "preferred"}
    )
    assert blocked == (WEB_SEARCH,)
    assert degraded == (WEB_FETCH,)


def test_a_service_that_has_everything_blocks_nothing() -> None:
    harness = resolve("claude_code")
    blocked, degraded = missing_capabilities(
        harness, {WEB_SEARCH: "required", WEB_FETCH: "required"}
    )
    assert blocked == ()
    assert degraded == ()


@pytest.mark.parametrize("key", known())
def test_every_service_answers_the_whole_protocol(key: str) -> None:
    """The protocol is STRUCTURAL, so a default in its body reaches nobody.

    `Harness` is a `Protocol` and the drivers are plain dataclasses that
    satisfy it by shape, not by inheritance -- so a method given a default
    implementation in the protocol body is not inherited by anything, and the
    first call site to use it raises `AttributeError` at run time on whichever
    driver was written before the method existed. That shipped once, on
    `quota`, and the tests that caught it were about call timeouts.
    """
    harness = resolve(key)
    for name in (
        "argv",
        "env_for",
        "child_env",
        "adapt_schema",
        "parse",
        "classify",
        "is_transcript_event",
        "summarize",
        "probe",
        "parse_probe",
        "login",
        "quota",
        "parse_quota",
        "cost_for",
    ):
        assert hasattr(harness, name), f"{key} does not implement {name}()"

    for name in (
        "key",
        "label",
        "binary",
        "aliases",
        "capabilities",
        "billing_modes",
        "secret_env",
        "metered_env",
        "home_env",
        "home_dirname",
        "reports_usage_windows",
        "fixture_root",
        "wants_confinement",
    ):
        assert hasattr(harness, name), f"{key} does not declare {name}"
