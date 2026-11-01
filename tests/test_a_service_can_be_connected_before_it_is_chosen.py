"""Connecting a service you are not already using, which is the only way in.

`/admin/model` lists three services and lets you switch between them, and the
switch is refused for one that has nothing to sign in with. So connecting is how
a service becomes selectable, and the route has taken a named `service` from the
start -- its own comment says why: "requiring the switch first is a loop with no
entrance".

The page never sent the field. It posted the secret and the CURRENT service's
billing mode, so Connect always landed on the service already in use. That
closes the loop the route was written to avoid: Grok is installed, offers an API
key, and could not be connected from the one page that offers to connect
things -- not greyed out by a rule, but unreachable by construction.

These pin the contract the page depends on. The dialog cannot be mounted in this
suite (vitest here runs without the Vue plugin), so the last test reads the one
line that carries the fix.
"""

from __future__ import annotations

import pathlib

from venona.llm.harness import known, resolve


def test_every_service_says_what_each_of_its_plans_wants() -> None:
    """A page cannot explain a credential it was told nothing about.

    Three services and two plans each: one subscription is a token minted by a
    command elsewhere, two are files the service writes itself, and the three
    metered keys come from three different consoles. Six different answers
    behind one button reading "Connect".
    """
    for key in known():
        harness = resolve(key)
        assert harness.billing_modes, f"{key} offers no way to pay for it"
        for mode in harness.billing_modes:
            help_ = harness.plan_help(mode)
            assert help_.credential, f"{key}/{mode} does not say what to hand it"
            assert help_.how, f"{key}/{mode} does not say where to get one"
            assert help_.pasteable == (harness.env_for(mode) is not None), (
                f"{key}/{mode} offers a box for a credential it cannot hold, or "
                f"withholds one it can"
            )


def test_a_plan_venona_cannot_hold_is_not_offered_a_box() -> None:
    """The failure a box would cause is silent and lands somewhere else.

    A service that keeps its credential in its own files has nothing for a
    pasted value to go into. Accepting one anyway stores something no call ever
    reads, reports success to the administrator, and fails on the next run as an
    authentication error against a credential the page says is connected.
    """
    unpasteable = [
        (key, mode)
        for key in known()
        for mode in resolve(key).billing_modes
        if not resolve(key).plan_help(mode).pasteable
    ]
    assert unpasteable, "the case this guards is meant to exist on some service"
    for key, mode in unpasteable:
        how = resolve(key).plan_help(mode).how
        assert "login" in how or "sign in" in how, (
            f"{key}/{mode} cannot take a pasted value, so its instruction has to "
            f"name what to do instead"
        )


def test_a_plan_that_needs_a_command_gives_the_command_itself() -> None:
    """Copyable, not described.

    Every plan Venona cannot hold is completed by running something on the
    machine where Venona is deployed, which means the administrator is already
    opening a shell somewhere else. A command retyped out of a paragraph is the
    step that goes wrong, so the exact string is on the wire and the page offers
    it to be copied.
    """
    for key in known():
        harness = resolve(key)
        for mode in harness.billing_modes:
            help_ = harness.plan_help(mode)
            if help_.pasteable:
                continue
            assert help_.command, (
                f"{key}/{mode} cannot take a pasted value and names no command, "
                f"so the page can only tell somebody to go and find one"
            )
            assert help_.command == help_.command.strip()
            assert "\n" not in help_.command, "one line, or it cannot be a copy button"


def test_a_metered_plan_says_that_it_is_metered() -> None:
    """The two plans bill in completely different ways, and this page is where
    somebody chooses. An API key silently charging per token beside a
    subscription that does not is the one confusion worth ruling out."""
    for key in known():
        harness = resolve(key)
        for mode in harness.billing_modes:
            help_ = harness.plan_help(mode)
            assert help_.metered == (mode == "api")
            if help_.metered:
                assert "per token" in help_.how, (
                    f"{key}/{mode} is charged per token and does not say so"
                )


def test_the_page_names_the_service_it_is_connecting() -> None:
    """The regression, at the one line that carried it.

    Without `service` the route falls back to the one in use, which is exactly
    the loop its comment refuses.
    """
    source = pathlib.Path("web/src/views/ModelView.vue").read_text()
    put = source.split("'/admin/model/credential'")[1][:400]
    assert "service:" in put, (
        "the credential submit does not name a service, so it can only ever "
        "connect the one already in use"
    )
    assert "billing_mode: connectPlan" in put, (
        "the credential submit sends the current service's plan rather than the "
        "one chosen, so a plan can never be changed"
    )
