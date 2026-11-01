"""Every icon every surface ships, rasterised from the one mark.

`web/src/assets/icons/venona-mark-aqua.svg` is the mark's source of truth
(docs/design/BRAND.md). Browsers want it in shapes an SVG cannot take: Safari
and Windows pinned sites read `favicon.ico`; iOS reads a 180px
`apple-touch-icon.png` and paints its own corners, so that one is full-bleed on
the plate colour; Android reads the manifest's 192/512 PNGs and, for an
adaptive launcher icon, a maskable 512 with the mark inside the safe zone;
Chrome's extension manifest takes 16/32/48/128 PNGs. One generator writes all
of them into every surface, so the app and the hub carry
byte-identical sets and `tests/test_brand_lockup.py` can hold them equal.

COMMITTED, not built: these change when the logo does, which is approximately
never, and building them per deploy would make three builds depend on librsvg.
Run `make icons` after editing the mark.

    uv run python scripts/brand_icons.py
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MARK = REPO / "web" / "src" / "assets" / "icons" / "venona-mark-aqua.svg"

#: Where each surface serves its set from, and what its manifest says about
#: it. The app is installable; the hub is a site you visit.
SURFACES: dict[Path, dict[str, str]] = {
    REPO / "web" / "public": {"name": "venona", "display": "standalone"},
    REPO / "hub" / "hub" / "static": {"name": "venona hub", "display": "browser"},
}
EXTENSION_ICONS = REPO / "extension" / "icons"
EXTENSION_SIZES = (16, 32, 48, 128)
ICO_SIZES = (16, 32, 48)
TOUCH_SIZE = 180
MANIFEST_SIZES = (192, 512)
MASKABLE_SIZE = 512
#: The W3C safe zone is the inner 80% of a maskable icon; 70% keeps the
#: monogram's corners clear of a circular mask with room to spare.
MASKABLE_SCALE = 0.7


def plate_fill(svg: str) -> str:
    """The plate colour, read from the mark's own rect rather than restated."""
    match = re.search(r'<rect[^>]*fill="(#[0-9a-fA-F]{6})"', svg)
    if not match:
        sys.exit(f"{MARK}: no filled <rect> plate to take the background colour from")
    return match.group(1).lower()


def rasterise(size: int, *, background: str | None = None, canvas: int | None = None) -> bytes:
    """The mark at `size` px; on a `background` the rounded corners fill in,
    and a larger `canvas` centres the mark on it."""
    args = ["rsvg-convert", "-w", str(size), "-h", str(size)]
    if background:
        args += ["-b", background]
    if canvas:
        inset = (canvas - size) // 2
        args += [
            "--page-width", str(canvas), "--page-height", str(canvas),
            "--left", str(inset), "--top", str(inset),
        ]  # fmt: skip
    return subprocess.run([*args, str(MARK)], check=True, capture_output=True).stdout


def ico(entries: dict[int, bytes]) -> bytes:
    """An ICO container around PNG entries, the form every browser reads."""
    header = struct.pack("<HHH", 0, 1, len(entries))
    directory = b""
    blobs = b""
    offset = len(header) + 16 * len(entries)
    for size, png in entries.items():
        directory += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset + len(blobs)
        )
        blobs += png
    return header + directory + blobs


def manifest(meta: dict[str, str], plate: str) -> str:
    return (
        json.dumps(
            {
                "name": meta["name"],
                "short_name": meta["name"],
                "start_url": "/",
                "scope": "/",
                "display": meta["display"],
                "background_color": plate,
                "theme_color": plate,
                "icons": [
                    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                    {
                        "src": "/icon-maskable-512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "maskable",
                    },
                ],
            },
            indent=2,
        )
        + "\n"
    )


def icon_set(plate: str) -> dict[str, bytes]:
    """Every file a surface serves, keyed by the fixed URL it answers at."""
    files = {
        "venona-mark.svg": MARK.read_bytes(),
        "favicon.ico": ico({size: rasterise(size) for size in ICO_SIZES}),
        "apple-touch-icon.png": rasterise(TOUCH_SIZE, background=plate),
        "icon-maskable-512.png": rasterise(
            round(MASKABLE_SIZE * MASKABLE_SCALE), background=plate, canvas=MASKABLE_SIZE
        ),
    }
    for size in MANIFEST_SIZES:
        files[f"icon-{size}.png"] = rasterise(size)
    return files


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"{MARK.relative_to(REPO)} -> {path.relative_to(REPO)}")


def main() -> int:
    if shutil.which("rsvg-convert") is None:
        print("brand_icons needs rsvg-convert (librsvg).", file=sys.stderr)
        return 1
    plate = plate_fill(MARK.read_text())
    files = icon_set(plate)
    for directory, meta in SURFACES.items():
        for name, data in files.items():
            write(directory / name, data)
        write(directory / "manifest.json", manifest(meta, plate).encode())
    for size in EXTENSION_SIZES:
        write(EXTENSION_ICONS / f"venona-{size}.png", rasterise(size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
