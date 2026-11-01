"""The hub serves the brand files its pages link, from its own process.

A page that links `/favicon.ico` on a host that answers it with a JSON 404 is
how a tab ends up with no icon and nothing in any log -- which is what the
sign-in page did for as long as it existed.
"""

from __future__ import annotations

import hashlib

import pytest
from hub.field import SCRIPT_URL
from hub.middleware import SecurityHeadersMiddleware
from hub.routers.brand import FILES, MEDIA_TYPES, SCRIPT, SCRIPT_CACHE, STATIC

from hub import pages


@pytest.mark.parametrize("name", FILES)
async def test_every_brand_file_answers_with_its_bytes(hub_client, name) -> None:
    response = await hub_client.get(f"/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(MEDIA_TYPES[(STATIC / name).suffix])
    assert response.content == (STATIC / name).read_bytes()


async def test_the_sign_in_page_links_the_set(hub_client) -> None:
    page = (await hub_client.get("/")).text
    for name in FILES:
        if name.startswith("icon-"):
            continue  # reached through the manifest, not the head
        assert f'href="/{name}"' in page, f"sign-in does not link /{name}"


async def test_a_page_with_a_field_loads_the_canvas(hub_client) -> None:
    """The living graph. `defer` so it never blocks the parse, and it is only
    on pages that HAVE a field to draw on -- a script tag on a page with
    nothing to draw on is a request that can only fail to do anything."""
    page = (await hub_client.get("/")).text
    assert f'<script src="{SCRIPT_URL}" defer></script>' in page
    assert "data-graph-field" in page


def test_a_page_without_a_field_loads_no_script() -> None:
    """The canvas rides with the artwork and never on its own.

    `field=False` is the whole of the condition, so it is asserted at that seam
    rather than through a page that happens to pass it today -- every public
    page carries the field now, and picking one of them would test nothing.

    The operator surface loads a bundle, which is a different script under a
    different argument (ADR 0223). What is held here is that a page which asked
    for no artwork does not quietly fetch the artwork's canvas anyway.
    """
    page = pages._page("Plain", "<h1>Plain</h1>")
    assert "constellation.js" not in page
    assert "graph-field" not in page


async def test_the_manifest_lists_only_files_the_hub_serves(hub_client) -> None:
    manifest = (await hub_client.get("/manifest.json")).json()
    for icon in manifest["icons"]:
        assert icon["src"].lstrip("/") in FILES, f"{icon['src']} has no route"


async def test_the_canvas_url_names_the_bytes_it_answers_with(hub_client) -> None:
    """⭐ A year in a cache is only safe because the URL cannot outlive its
    content: rebuild the bundle and the digest moves, so the tag points
    somewhere else rather than at a stale file somebody is still holding."""
    digest = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()[:12]
    assert SCRIPT_URL == f"/constellation.{digest}.js"
    response = await hub_client.get(SCRIPT_URL)
    assert response.status_code == 200
    assert response.content == SCRIPT.read_bytes()
    assert response.headers["content-type"].startswith("text/javascript")
    # One policy, not two: the middleware appends, so a default stamped over
    # this would ship beside it and `no-store` would be the one that wins.
    assert response.headers.get_list("cache-control") == [SCRIPT_CACHE]
    assert "vary" not in response.headers


async def test_the_canvas_still_carries_the_whole_posture(hub_client) -> None:
    """Answering for its own caching buys a route nothing else. Every security
    header is stamped whatever the response said."""
    response = await hub_client.get(SCRIPT_URL)
    for key, value in SecurityHeadersMiddleware.HEADERS.items():
        assert response.headers[key.decode()] == value.decode()


async def test_a_rendered_page_is_never_stored(hub_client) -> None:
    """The default the canvas is the single exception to. A page is rendered
    for whoever asked, in their palette, and the chooser must not survive a
    sign-out."""
    response = await hub_client.get("/")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["vary"] == "Cookie"
