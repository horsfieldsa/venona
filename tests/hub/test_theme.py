"""The hub follows the reader's theme, server-side, with no script on the page.

Every other Venona surface resolves the shared choice in a render-blocking
classic script. The hub cannot: `middleware.py`'s CSP is `default-src 'none'`
with no `script-src` at all, and widening that on an identity provider which
interpolates attacker-influencable text is not a trade worth making for a
preference control. So the cookie is read on the server and `data-theme` is
printed into the shell (docs/adr/0177).

The reward is that this half works with scripts off, which the app's does not.
"""

from __future__ import annotations

import re

import pytest
from hub.repositories import instances as instances_repo

from hub import ids, pages

COOKIE = "venona.theme"


def _opening_tag(body: str) -> str:
    match = re.search(r"<html[^>]*>", body)
    assert match, "no <html> tag in the response"
    return match.group(0)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("light", ' data-theme="light"'),
        ("dark", ' data-theme="dark"'),
        # System is the ABSENCE of the attribute, not a third value for it: the
        # shell declares `color-scheme: light dark` on the bare `:root` and
        # overrides it only under `[data-theme]`, so there is no third rule for
        # `data-theme="system"` to match and the page would pin to whichever
        # side the browser happened to resolve.
        ("system", ""),
        ("", ""),
        ("purple", ""),
        ("light; color-scheme: dark", ""),
    ],
)
def test_only_the_two_explicit_choices_reach_the_shell(stored: str, expected: str) -> None:
    rendered = pages._page("t", "<h1>t</h1>", theme=stored if stored in ("light", "dark") else None)
    assert _opening_tag(rendered) == f"<html{expected}>"


def test_the_shell_carries_the_rules_the_attribute_needs() -> None:
    """Without them the attribute is inert and every `light-dark()` below keeps
    following the operating system, which looks exactly like the cookie not
    being read at all."""
    rendered = pages._page("t", "<h1>t</h1>", theme="dark")
    assert ":root[data-theme='light'] { color-scheme: light; }" in rendered
    assert ":root[data-theme='dark'] { color-scheme: dark; }" in rendered


async def test_every_html_page_the_hub_serves_follows_the_cookie(hub_client, hub_conn) -> None:
    """The sweep, and the reason it is a sweep rather than a list.

    The theme is threaded through fourteen `HTMLResponse` call sites across
    three routers. A missed one does not raise -- it renders a page in the
    operating system's palette while every other page follows the reader, which
    reads as a flicker between steps of a sign-in rather than as a bug.
    """
    instances_repo.create(
        hub_conn,
        instance_id=ids.instance_id(),
        slug="acme",
        display_name="Acme Watch",
        tagline="the acme watch",
        public_url="https://acme.example",
        oidc_client_id=ids.client_id(),
        oidc_client_secret_hash="x",
        mgmt_token_sealed="y",
    )
    hub_client.cookies.set(COOKIE, "light", domain="hub.test")

    # Every route that answers a GET with HTML and needs no session or operator
    # bearer. `/admin` answers 404 to a signed-out reader and that page is HTML
    # too, so it belongs in the sweep exactly as much as the portal does -- and
    # so does the page a WRONG address gets, which is now HTML rather than an
    # envelope and is threaded through its own call site in `hub/errors.py`.
    #
    # The browser `Accept` is sent because a browser sends it: the refusal
    # handlers negotiate on it, and without it the last path here answers JSON
    # and the loop below skips it rather than checking anything.
    browser = {"Accept": "text/html,application/xhtml+xml"}
    for path in ("/", "/request-access", "/admin", "/oauth/authorize", "/nonsense"):
        response = await hub_client.get(path, headers=browser)
        if "text/html" not in response.headers.get("content-type", ""):
            continue
        assert 'data-theme="light"' in _opening_tag(response.text), (
            f"{path} answered {response.status_code} without the reader's theme"
        )


async def test_the_request_access_reply_varies_with_the_theme_and_nothing_else(
    hub_client, hub_conn
) -> None:
    """ADR 0141: one reply for first, duplicate, known account and unknown
    instance, because any variation that tracks the submission is an
    account-existence oracle. The theme is a property of the browser asking,
    not of the address it asked about, so it may vary and nothing else may."""
    from hub.repositories import users as users_repo

    users_repo.create(hub_conn, user_id=ids.user_id(), email="known@example.com", display_name=None)

    async def submit(email: str, instance: str = "") -> bytes:
        response = await hub_client.post(
            "/request-access", data={"email": email, "note": "please", "instance": instance}
        )
        assert response.status_code == 200
        return response.content

    hub_client.cookies.set(COOKIE, "dark", domain="hub.test")
    outcomes = [
        await submit("new@example.com"),
        await submit("new@example.com"),
        await submit("known@example.com"),
        await submit("new2@example.com", instance="no-such-instance"),
    ]
    assert len(set(outcomes)) == 1, "the reply distinguishes an outcome"
    assert b'data-theme="dark"' in outcomes[0]

    hub_client.cookies.delete(COOKIE, domain="hub.test")
    system = await submit("new3@example.com")
    # The OPENING TAG, not the document: `data-theme` also names the two
    # `[data-theme]` rules in the shell's stylesheet, which are on every page
    # whichever way the cookie fell.
    assert b"<html>" in system
    assert system != outcomes[0], "the theme is the one thing that may differ, and it did not"
    assert system.replace(b"<html>", b'<html data-theme="dark">') == outcomes[0], (
        "the reply differs from the themed one by more than the attribute"
    )


async def test_the_hub_varies_on_cookie(hub_client) -> None:
    """`no-store` already makes a shared cache impossible, so this is not load
    bearing today. It is what keeps it from becoming load bearing silently the
    day anything is put in front of the hub."""
    response = await hub_client.get("/")
    assert response.headers["vary"] == "Cookie"
