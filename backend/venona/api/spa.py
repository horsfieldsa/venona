"""Serve the built SPA from the API process.

Installed only when `VENONA_SPA_DIR` names a directory: the production image
bakes the web build in and sets it; development never does, because vite
serves the SPA there and proxies `/api` here. The api being the single origin
is a hosting constraint, not a preference -- the host's static-site rewrite
proxy buffers server-sent events (docs/pm/epics/i-render.md), so a proxy of
ours between the browser and the run stream breaks contract C6.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

# Hashed asset filenames change when their content does, so a year of
# immutable is safe; everything else (index.html, theme-boot.js) must
# revalidate so a deploy is visible without a hard refresh.
_IMMUTABLE = "public, max-age=31536000, immutable"
_REVALIDATE = "no-cache"


def install(app: FastAPI, directory: Path) -> None:
    """Register the catch-all SPA route. Call AFTER every router: Starlette
    matches in registration order, so anything added later would be shadowed.
    """
    root = directory.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa(path: str) -> FileResponse:
        # An unmatched /api path must stay a JSON 404 rather than become a
        # 200 text/html page -- that failure is silent on both sides.
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        try:
            candidate = (root / path).resolve()
            is_file = candidate.is_relative_to(root) and candidate.is_file()
        except (ValueError, OSError):
            is_file = False
        if is_file:
            cache = _IMMUTABLE if path.startswith("assets/") else _REVALIDATE
            return FileResponse(candidate, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": _REVALIDATE})
