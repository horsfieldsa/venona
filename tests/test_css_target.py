"""Every surface that ships `light-dark()` must lower CSS to the same floor.

`light-dark()` is newer than Vite's default `cssTarget`, so on build Lightning
CSS rewrites it into

    var(--lightningcss-light, A) var(--lightningcss-dark, B)

and stamps the two custom properties onto whichever rule declared
`color-scheme` -- resolved STATICALLY, at build time. The dev server does no
lowering at all and serves the function untouched. Measured, not assumed: with
Vite's default target the app's built stylesheet carried the polyfill while
`vite dev` served `light-dark(var(--grey-950), var(--grey-000))`.

Both forms render correctly today. They stop agreeing the moment the theme is
switched at RUNTIME, which is the entire design: the polyfill answers to the
`color-scheme` Lightning CSS saw in the source, not to the one that won the
cascade in the browser. A surface that quietly falls back to it would look
right in dev and be stuck on one palette in production.

So the pin is load-bearing, it is duplicated across configs that share no code,
and this is what stops the copies drifting.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

CONFIGS = (
    "web/vite.config.ts",
    "extension/vite.content.config.ts",
    "extension/vite.main.config.ts",
    "extension/vite.theme.config.ts",
    "hub/web/vite.config.ts",
)

# `light-dark()`'s baseline. Chrome 123 / Safari 17.5 / Firefox 120.
EXPECTED = ["chrome123", "safari17.5", "firefox120"]


def _css_target(path: Path) -> list[str]:
    source = path.read_text()
    match = re.search(r"cssTarget:\s*\[([^\]]+)\]", source)
    assert match, f"{path.name}: no cssTarget -- Lightning CSS will polyfill light-dark()"
    return re.findall(r"'([^']+)'", match.group(1))


def test_every_config_pins_the_same_css_target() -> None:
    found = {name: _css_target(REPO / name) for name in CONFIGS}
    for name, target in found.items():
        assert target == EXPECTED, (
            f"{name} lowers CSS to {target}, the rest to {EXPECTED}. "
            "One surface polyfilling light-dark() while the others do not is a "
            "theme switch that works in dev and is frozen in production."
        )
