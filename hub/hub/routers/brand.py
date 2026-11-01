"""The brand files the pages link: favicon, touch icon, manifest.

Served by the app because the hub has no static tier, and as one explicit
route per file rather than a catch-all
mount, so an unknown path still gets the hub's own 404. The bytes are the set
`make icons` writes into every surface; `tests/test_brand_lockup.py` holds
this copy equal to the app's.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from hub.assets import FONT_FILES, PUBLIC_CSS_URL
from hub.field import SCRIPT_URL

STATIC = Path(__file__).resolve().parents[1] / "static"

#: Fixed URLs, each answered by the file of the same name under `static/`.
FILES = (
    "favicon.ico",
    "venona-mark.svg",
    "apple-touch-icon.png",
    "icon-192.png",
    "icon-512.png",
    "icon-maskable-512.png",
    "manifest.json",
)

#: The graph field's canvas. Not brand, but the same shape of thing: one file,
#: one fixed URL, no catch-all mount. It is generated (`make field`) from
#: `hub/canvas/constellation.ts` -- and the URL carries a digest of the bundle,
#: emitted beside it into `hub/field.py` so the tag and the route read one
#: spelling.
#:
#: A YEAR, on a service whose every other answer is `no-store`. That default is
#: right for the pages: each one is rendered for whoever asked, in their palette,
#: and one of them is a chooser that must not survive a sign-out. It is wrong
#: for these bytes, and expensively so -- a hub page is a handful of requests and
#: this was the largest of them, refetched on every load of every page. A URL
#: that names its own content cannot go stale: change the bundle and the tag
#: points somewhere else.
SCRIPT = STATIC / "constellation.js"
SCRIPT_CACHE = "public, max-age=31536000, immutable"

MEDIA_TYPES = {
    ".ico": "image/vnd.microsoft.icon",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/manifest+json",
    ".js": "text/javascript",
    ".css": "text/css",
    ".woff2": "font/woff2",
    ".map": "application/json",
}

#: The operator app's bundle, built by `make hub-web` into `static/app/`.
#:
#: SERVED FROM `/app/`, NOT FROM `/admin/`. `AdminBearerMiddleware` default-
#: denies everything under `/admin` except an exact set of operator paths, so a
#: hashed chunk under that prefix answers 401 and the page arrives unstyled with
#: nothing in the console to explain it. Keeping the assets out here leaves the
#: gate an exact set rather than something widened to let a stylesheet through.
BUNDLE = STATIC / "app"
BUNDLE_URL = "/app"

#: A year, like the field's canvas and for the same reason: every filename here
#: carries a digest of its own contents, so it cannot go stale. `index.html`
#: does NOT -- it names the current hashes, so it is the one file that must be
#: re-read every time.
ASSET_CACHE = "public, max-age=31536000, immutable"

#: The front door's stylesheet and its four type files.
#:
#: A FILE RATHER THAN A `<style>` BLOCK, and the arithmetic is the argument:
#: every rendered hub page is `no-store`, so an inlined stylesheet re-ships on
#: every view forever. As one immutable file it is fetched once, and the pages
#: got smaller than they were before carrying any of this.
#:
#: The fonts are NOT the operator bundle's copies, deliberately. Those are
#: hashed build output, and pointing the sign-in page at one would make an auth
#: page depend on a bundle -- the exact thing epic D forbids, arriving by the
#: back door as an asset reference.
PUBLIC_CSS = STATIC / "public.css"
FONT_DIR = STATIC / "fonts"

router = APIRouter(tags=["brand"])


def _serve(path: Path, cache: str | None = None):
    headers = {"cache-control": cache} if cache else None

    def brand_file() -> FileResponse:
        return FileResponse(path, media_type=MEDIA_TYPES[path.suffix], headers=headers)

    return brand_file


for _name in FILES:
    router.add_api_route(
        f"/{_name}",
        _serve(STATIC / _name),
        methods=["GET"],
        include_in_schema=False,
        name=f"brand_{_name}",
    )

router.add_api_route(
    SCRIPT_URL,
    _serve(SCRIPT, SCRIPT_CACHE),
    methods=["GET"],
    include_in_schema=False,
    name="brand_constellation",
)


router.add_api_route(
    PUBLIC_CSS_URL,
    _serve(PUBLIC_CSS, ASSET_CACHE),
    methods=["GET"],
    include_in_schema=False,
    name="brand_public_css",
)

for _font in FONT_FILES:
    router.add_api_route(
        f"/fonts/{_font}",
        _serve(FONT_DIR / _font, ASSET_CACHE),
        methods=["GET"],
        include_in_schema=False,
        name=f"brand_font_{_font}",
    )


def bundle_files() -> list[str]:
    """Every built asset, as URL paths under `/app`.

    ENUMERATED, NOT MOUNTED. `StaticFiles` would answer its own 404 for an
    unknown path under this prefix, and the hub's whole routing posture is that
    a URL it does not know reaches the hub's own refusal. It also hides the
    routes from `tests/hub/test_ops.py`'s walk, which reads `.routes` and finds
    a mount empty.

    Read once at import. The filenames are content-hashed, so the set changes
    only when the bundle is rebuilt -- which restarts the process anyway.
    """
    if not BUNDLE.is_dir():
        return []
    return [
        f"{BUNDLE_URL}/{path.relative_to(BUNDLE).as_posix()}"
        for path in sorted(BUNDLE.rglob("*"))
        if path.is_file() and path.name != "index.html" and path.suffix in MEDIA_TYPES
    ]


for _url in bundle_files():
    router.add_api_route(
        _url,
        _serve(BUNDLE / _url[len(BUNDLE_URL) + 1 :], ASSET_CACHE),
        methods=["GET"],
        include_in_schema=False,
        name=f"bundle_{_url}",
    )
