"""The SPA-serving seam (`api/spa.py`) and the edge header mode.

Exercised against a minimal app rather than `venona.main`: the real app only
installs the route when `VENONA_SPA_DIR` is set at import, which the offline
suite never does -- this seam is how the behavior stays testable anyway.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from venona.api.middleware import SecurityHeadersMiddleware
from venona.api.spa import install

INDEX_BODY = "<title>spa-index</title>"


@pytest.fixture
def spa_app(tmp_path) -> FastAPI:
    (tmp_path / "index.html").write_text(INDEX_BODY)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-abc123.js").write_text("export {}")

    app = FastAPI()

    @app.get("/api/ping")
    async def ping() -> dict:
        return {"ok": True}

    app.add_middleware(SecurityHeadersMiddleware, edge=True)
    install(app, tmp_path)
    return app


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


async def test_a_deep_link_falls_back_to_index(spa_app) -> None:
    resp = await _get(spa_app, "/briefs/some-id")
    assert resp.status_code == 200
    assert resp.text == INDEX_BODY
    assert resp.headers["cache-control"] == "no-cache"


async def test_hashed_assets_are_immutable(spa_app) -> None:
    resp = await _get(spa_app, "/assets/app-abc123.js")
    assert resp.status_code == 200
    assert "immutable" in resp.headers["cache-control"]


async def test_an_unmatched_api_path_stays_a_json_404(spa_app) -> None:
    resp = await _get(spa_app, "/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert "text/html" not in resp.headers.get("content-type", "")


async def test_traversal_cannot_escape_the_spa_root(spa_app) -> None:
    resp = await _get(spa_app, "/assets/%2e%2e/%2e%2e/%2e%2e/etc/passwd")
    assert resp.status_code == 200
    assert resp.text == INDEX_BODY


async def test_edge_mode_stamps_the_spa_with_edge_headers(spa_app) -> None:
    resp = await _get(spa_app, "/")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["strict-transport-security"].startswith("max-age=")
    assert "default-src 'self'" in resp.headers["content-security-policy"]
    # the SPA route owns its cache policy; the /api-only `no-store` must not
    # override it
    assert resp.headers["cache-control"] == "no-cache"


async def test_edge_mode_keeps_no_store_on_api_responses(spa_app) -> None:
    resp = await _get(spa_app, "/api/ping")
    assert resp.headers["cache-control"] == "no-store"
    assert "content-security-policy" in resp.headers


async def test_off_edge_leaves_non_api_paths_bare() -> None:
    app = FastAPI()

    @app.get("/outside")
    async def outside() -> dict:
        return {}

    app.add_middleware(SecurityHeadersMiddleware)
    resp = await _get(app, "/outside")
    assert "x-content-type-options" not in resp.headers
    assert "strict-transport-security" not in resp.headers
