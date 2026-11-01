"""The drawn lockup, inlined three times, must still be the kit's drawing.

The app, a separate front site and the hub's server-rendered pages all INLINE the
horizontal lockup rather than pointing an `<img>` at it, because the wordmark
has to be white on the dark theme and near-black on the light one and the brand
kit ships no file that pairs the aqua plate with a white wordmark. Inlining
buys that at the cost of three hand copies of ~4KB of path data, which is
exactly the kind of duplication that goes quietly wrong.

So `web/src/assets/logos/venona-logo-primary.svg` stays in the tree as the
SOURCE OF TRUTH -- it is imported by nothing and that is deliberate -- and this
holds both copies against it. Without these checks the file reads as the dead
asset that started this whole change.

The failure these prevent is not an error. A stale path renders a slightly wrong
letterform, and nobody notices for months.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KIT = REPO / "web" / "src" / "assets" / "logos" / "venona-logo-primary.svg"
WORDMARK_VUE = REPO / "web" / "src" / "components" / "brand" / "Wordmark.vue"
HUB_PAGES = REPO / "hub" / "hub" / "pages.py"
HUB_LOCKUP = REPO / "hub" / "web" / "src" / "components" / "brand" / "Lockup.vue"

# The kit's own group ids. `SvgjsG1080` is the wordmark -- the one group that
# takes `currentColor` -- and the other two are the plate and the monogram.
PLATE, MONOGRAM, WORDMARK = "SvgjsG1078", "SvgjsG1079", "SvgjsG1080"

# The pale aqua the wordmark is drawn in. It is the off-white this change exists
# to remove, so its presence in either surface means a copy went stale.
KIT_WORDMARK_FILL = 'fill="#d2ecf9"'


def _kit_group(group_id: str) -> tuple[str, str]:
    """The transform and path data of one group of the kit lockup."""
    line = next(ln for ln in KIT.read_text().splitlines() if group_id in ln)
    transform = re.search(r'transform="([^"]+)"', line)
    assert transform, f"{group_id}: no transform in the kit file"
    # Anchored on `<path `, because `id="SvgjsG1080"` ends in `d="` and an
    # unanchored `d="..."` match silently returns the id instead. That is not
    # hypothetical: it is what the generator for these two copies did first.
    path = re.search(r'<path d="([^"]+)"', line)
    return transform.group(1), (path.group(1) if path else "")


def _sources() -> dict[str, str]:
    return {
        "web/src/components/brand/Wordmark.vue": WORDMARK_VUE.read_text(),
        "hub/hub/pages.py": HUB_PAGES.read_text(),
        # The hub is two surfaces and they cannot share this: the public pages
        # are server-rendered Python with no bundle, the operator app is Vue.
        "hub/web/src/components/brand/Lockup.vue": HUB_LOCKUP.read_text(),
    }


def test_both_surfaces_inline_the_kit_paths() -> None:
    monogram_transform, monogram_d = _kit_group(MONOGRAM)
    wordmark_transform, wordmark_d = _kit_group(WORDMARK)
    assert len(wordmark_d) > 3000, "the kit wordmark path looks truncated"

    for name, source in _sources().items():
        for label, needle in (
            ("monogram transform", monogram_transform),
            ("monogram path", monogram_d),
            ("wordmark transform", wordmark_transform),
            ("wordmark path", wordmark_d),
            # The plate is a rect, not a path, and its geometry is the whole of
            # the mark's shape.
            ("plate rect", '<rect width="104" height="104" rx="10" ry="10">'),
            ("the outer 10-unit clear space", 'transform="translate(10, 10)"'),
        ):
            assert needle in source, f"{name}: {label} does not match the kit file"


def test_neither_surface_ships_the_kit_wordmark_fill() -> None:
    """The wordmark takes `currentColor`, so the pale aqua must be gone.

    A copy that still carries `#d2ecf9` is one that was pasted from the kit and
    never wired to the token -- it would render off-white on both themes and
    look deliberate.
    """
    for name, source in _sources().items():
        assert KIT_WORDMARK_FILL not in source, (
            f"{name}: still carries the kit's {KIT_WORDMARK_FILL} on a group"
        )
        assert 'fill="currentColor"' in source, (
            f"{name}: the wordmark group is not taking currentColor"
        )


def test_the_app_colours_the_wordmark_through_the_token() -> None:
    """`text-brand-wordmark` sets `color`; the group reads it as `currentColor`.

    Both halves have to be present. The class alone was there for months while
    `--brand-wordmark` did not exist -- an unknown utility in a closed Tailwind
    theme generates nothing, so the word simply had no brand colour and no build
    step said so.
    """
    source = WORDMARK_VUE.read_text()
    assert "text-brand-wordmark" in source
    tokens = (REPO / "web" / "src" / "styles" / "tokens.css").read_text()
    theme = (REPO / "web" / "src" / "styles" / "theme.css").read_text()
    assert "--brand-wordmark:" in tokens, "the token the class needs is not declared"
    assert "--color-brand-wordmark:" in theme, (
        "declared but unmapped: without a `--color-*` entry the closed theme "
        "does not generate `text-brand-wordmark` and the class is inert"
    )


# ── the mark's icon sets ─────────────────────────────────────────────────
#
# The 104x104 mark is rasterised by `make icons` (scripts/brand_icons.py)
# into one set per surface -- the app and the hub each serve it at the same
# fixed URLs -- plus the extension's toolbar PNGs.
# docs/adr/0146 replaced a "change both" comment with a check; docs/adr/0156
# widened the set from one SVG to everything a phone or Safari asks for. The
# SVG copies are compared as drawings (dead `id=`/`featureKey=` attributes
# stripped, whitespace collapsed); the rasters must be byte-identical.

MARK_SOURCE = REPO / "web" / "src" / "assets" / "icons" / "venona-mark-aqua.svg"
ICON_SURFACES = (
    REPO / "web" / "public",
    REPO / "hub" / "hub" / "static",
)
#: File -> pixel size, for everything a surface serves beside the SVG and the
#: manifest. The .ico holds three entries.
RASTERS = {
    "apple-touch-icon.png": 180,
    "icon-192.png": 192,
    "icon-512.png": 512,
    "icon-maskable-512.png": 512,
}
ICO_SIZES = (16, 32, 48)
ICON_SET = {"venona-mark.svg", "favicon.ico", "manifest.json", *RASTERS}
EXTENSION_ICONS = {REPO / "extension" / "icons" / f"venona-{n}.png": n for n in (16, 32, 48, 128)}

#: Every document that paints a tab: the app shell, the hub's pages and the
#: instance's consent screen (which the api renders on the app's origin, where
#: the SPA tier answers the URLs).
HEADS = (
    REPO / "web" / "index.html",
    HUB_PAGES,
    REPO / "backend" / "venona" / "api" / "routers" / "oauth.py",
)
LINKS = (
    ("icon", "/favicon.ico"),
    ("icon", "/venona-mark.svg"),
    ("apple-touch-icon", "/apple-touch-icon.png"),
    ("manifest", "/manifest.json"),
)


def _drawing(path: Path) -> str:
    svg = path.read_text()
    svg = re.sub(r'\s+(?:id|featureKey)="[^"]*"', "", svg)
    return re.sub(r"\s+", " ", svg).strip()


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _ico_sizes(data: bytes) -> list[int]:
    assert data[:4] == b"\x00\x00\x01\x00", "not an ICO"
    count = int.from_bytes(data[4:6], "little")
    return [data[6 + 16 * i] for i in range(count)]


def test_every_mark_copy_is_the_source_drawing() -> None:
    source = _drawing(MARK_SOURCE)
    assert '<path d="' in source, "the mark source lost its path data"
    for surface in ICON_SURFACES:
        copy = surface / "venona-mark.svg"
        assert _drawing(copy) == source, (
            f"{copy.relative_to(REPO)} is not the drawing in "
            f"{MARK_SOURCE.relative_to(REPO)} -- run `make icons`"
        )


def test_every_surface_ships_the_same_icon_set() -> None:
    """A surface missing a file answers its fixed URL with an HTML fallback or
    a JSON 404, and the browser shows a blank tab with no error anywhere."""
    reference = ICON_SURFACES[0]
    for surface in ICON_SURFACES:
        present = {p.name for p in surface.iterdir() if p.name in ICON_SET}
        assert present == ICON_SET, f"{surface.relative_to(REPO)} is missing {ICON_SET - present}"
        for name in ICON_SET - {"manifest.json"}:
            assert (surface / name).read_bytes() == (reference / name).read_bytes(), (
                f"{surface.relative_to(REPO)}/{name} differs from "
                f"{reference.relative_to(REPO)} -- run `make icons`"
            )


def test_the_rasters_are_the_sizes_the_devices_ask_for() -> None:
    for name, size in RASTERS.items():
        data = (ICON_SURFACES[0] / name).read_bytes()
        assert _png_size(data) == (size, size), f"{name} is not {size}x{size}"
    assert _ico_sizes((ICON_SURFACES[0] / "favicon.ico").read_bytes()) == list(ICO_SIZES)
    for path, size in EXTENSION_ICONS.items():
        assert _png_size(path.read_bytes()) == (size, size), f"{path.name} is not {size}x{size}"


def test_the_manifests_agree_on_icons_and_colour() -> None:
    """The manifests differ only in name and display mode; the icons they
    list exist, and their colour is the plate's own fill -- the same hex the
    documents' `theme-color` meta carries, which is the one raw colour the
    design system permits outside tokens.css."""
    plate = re.search(r'<rect[^>]*fill="(#[0-9a-f]{6})"', MARK_SOURCE.read_text())
    assert plate, "the mark has no filled plate"
    for surface in ICON_SURFACES:
        manifest = json.loads((surface / "manifest.json").read_text())
        assert manifest["theme_color"] == plate.group(1)
        for icon in manifest["icons"]:
            assert (surface / icon["src"].lstrip("/")).is_file(), f"{icon['src']} is not shipped"
    for document in (REPO / "web" / "index.html",):
        assert f'<meta name="theme-color" content="{plate.group(1)}"' in document.read_text(), (
            f"{document.relative_to(REPO)}'s theme-color is not the plate"
        )


def test_every_document_links_the_whole_set() -> None:
    for document in HEADS:
        text = document.read_text()
        for rel, href in LINKS:
            pattern = rf'<link rel="{rel}"[^>]*href="{re.escape(href)}"'
            assert re.search(pattern, text), f"{document.relative_to(REPO)} lacks {rel} {href}"
