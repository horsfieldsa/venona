"""The graph field, once, for two surfaces that cannot share a stylesheet.

The artwork is the ground under every hub error page, the hub's front door and
the instance's own not-found view: a still composition of
edges, seven coloured hubs and thirty-one minor nodes on a 1600x1000 field.
One drawing, so a wrong turn looks the same wherever a reader takes it.

WHY A GENERATOR AND NOT TWO COPIES. The surfaces name colours in two
vocabularies and one of them has no token pipeline at all:

    web app    var(--border-strong), var(--entity-slot-N)
    hub        light-dark(#8794a3, #3b4756)             an inline stylesheet

So the markup cannot simply be copied, and eighty hand-placed elements copied
twice with no guard is a drawing that drifts silently -- decoration is
exactly the kind of thing nobody re-reads. `make lint-generated` diffs every
copy against a fresh run, the way it already does for `docs/schema.sql`.

The app's copy names SLOTS, not entity types. `--entity-company` is the
defense ontology's word and this repo runs several domains; decoration must
not name a type. Slots 1/5/6/7/8 are the hues `company/prog/tech/plat/person`
alias today, so the surfaces still look alike.

The hub's hex pairs are RESOLVED OUT OF `web/src/styles/tokens.css` rather
than typed in. That file is the design system's system of record, and the
hub's inline stylesheet is a hand copy of it that nothing but
`tests/test_brand_lockup.py` holds in place -- so a field whose colours are
read from the source cannot drift from the app the way the rest of
`hub/hub/pages.py` can.

    make field      regenerate every copy
    make lint-generated   fail if any is stale
"""

from __future__ import annotations

import functools
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB_TOKENS = REPO / "web" / "src" / "styles" / "tokens.css"
HUB_FIELD_TOKENS = REPO / "hub" / "canvas" / "tokens.css"

#: The field, in its own coordinate space, hand-placed once. Edges carry a stroke width and a dashed flag; the long
#: dashed ones are the cross-field links between hubs.
EDGES: tuple[tuple[float, float, float, float, float, bool], ...] = (
    (1008, 236, 799, 129, 1.2, False),
    (1008, 236, 1023, 96, 1.7, False),
    (1008, 236, 1209, 243, 1.4, False),
    (1542, 192, 1624, 269, 1.8, False),
    (1542, 192, 1374, 214, 1.6, False),
    (1542, 192, 1539, 262, 2, False),
    (1542, 192, 1606, 327, 1.7, False),
    (1374, 214, 1606, 327, 1.1, False),
    (1542, 192, 1478, 432, 1.2, False),
    (584, 634, 434, 809, 1.2, False),
    (584, 634, 441, 458, 1.3, False),
    (584, 634, 607, 565, 2, False),
    (584, 634, 688, 619, 1.9, False),
    (584, 634, 357, 677, 1.2, False),
    (1369, 669, 1352, 884, 1.3, False),
    (1369, 669, 1153, 702, 1.3, False),
    (1369, 669, 1160, 709, 1.3, False),
    (1352, 884, 1160, 709, 1.1, False),
    (670, 315, 560, 504, 1.3, False),
    (670, 315, 659, 204, 1.8, False),
    (670, 315, 604, 114, 1.3, False),
    (670, 315, 843, 247, 1.5, False),
    (560, 504, 843, 247, 0.9, True),
    (670, 315, 737, 468, 1.6, False),
    (180, 824, 398, 775, 1.3, False),
    (180, 824, 262, 663, 1.5, False),
    (180, 824, -42, 748, 1.2, False),
    (180, 824, 104, 925, 1.8, False),
    (180, 824, 301, 839, 1.8, False),
    (180, 824, 400, 813, 1.3, False),
    (262, 663, 400, 813, 1.4, False),
    (180, 824, 143, 579, 1.2, False),
    (1020, 807, 949, 1034, 1.2, False),
    (1020, 807, 855, 846, 1.6, False),
    (1020, 807, 945, 756, 1.9, False),
    (1008, 236, 670, 315, 0.9, True),
    (1008, 236, 1542, 192, 0.9, True),
    (1542, 192, 1369, 669, 0.9, True),
    (584, 634, 670, 315, 0.9, True),
    (584, 634, 180, 824, 0.9, True),
    (1369, 669, 1020, 807, 0.9, True),
)

HUBS: tuple[tuple[float, float, float, str], ...] = (
    (1008, 236, 13.9, "org"),
    (1542, 192, 14.5, "prog"),
    (584, 634, 12.2, "tech"),
    (1369, 669, 13.3, "plat"),
    (670, 315, 11.6, "person"),
    (180, 824, 13.5, "org"),
    (1020, 807, 10.6, "prog"),
)

MINOR: tuple[tuple[float, float, float], ...] = (
    (799, 129, 4.7),
    (1023, 96, 7.8),
    (1209, 243, 4.1),
    (1624, 269, 6.9),
    (1374, 214, 7.6),
    (1539, 262, 4.4),
    (1606, 327, 6.9),
    (1478, 432, 5.9),
    (434, 809, 4.1),
    (441, 458, 7.1),
    (607, 565, 4.6),
    (688, 619, 4.8),
    (357, 677, 6.8),
    (1352, 884, 4.6),
    (1153, 702, 3.7),
    (1160, 709, 5.5),
    (560, 504, 5.3),
    (659, 204, 7.8),
    (604, 114, 7.3),
    (843, 247, 6.3),
    (737, 468, 7.6),
    (398, 775, 4.1),
    (262, 663, 4.7),
    (-42, 748, 4.4),
    (104, 925, 3.7),
    (301, 839, 5.4),
    (400, 813, 6.7),
    (143, 579, 7.7),
    (949, 1034, 4.9),
    (855, 846, 5.3),
    (945, 756, 7),
)

#: Edge and minor-node ink, then the seven hub roles in order, per surface.
#: The roles are positional: the same hub is the same hue on every surface.
ROLES = ("org", "prog", "tech", "plat", "person", "org", "prog")

WEB_TOKENS_MAP = {
    "ink": "var(--border-strong)",
    "org": "var(--entity-slot-1)",
    "prog": "var(--entity-slot-5)",
    "tech": "var(--entity-slot-6)",
    "plat": "var(--entity-slot-7)",
    "person": "var(--entity-slot-8)",
}
#: What the hub's hex pairs are resolved FROM. The app's names, read out of
#: `web/src/styles/tokens.css` and flattened to `light-dark(#a, #b)`.
HUB_SOURCE = {
    "ink": "--border-strong",
    "org": "--entity-slot-1",
    "prog": "--entity-slot-5",
    "tech": "--entity-slot-6",
    "plat": "--entity-slot-7",
    "person": "--entity-slot-8",
}

EDGE_OPACITY = "0.55"
MINOR_OPACITY = "0.7"

#: The tokens `hub/canvas/constellation.ts` probes for, and where the hub's
#: value for each comes from. That script names no colour at all -- it reads
#: these off a probe element so the graph follows the theme -- so the hub only
#: has to DEFINE them. `--accent-bright` has no counterpart in the app's tokens,
#: so it is read from the file that declares it.
CANVAS_SOURCE: dict[str, tuple[Path, str]] = {
    "--grey-500": (WEB_TOKENS, "--border-strong"),
    "--entity-org": (WEB_TOKENS, "--entity-slot-1"),
    "--entity-prog": (WEB_TOKENS, "--entity-slot-5"),
    "--entity-tech": (WEB_TOKENS, "--entity-slot-6"),
    "--entity-plat": (WEB_TOKENS, "--entity-slot-7"),
    "--entity-person": (WEB_TOKENS, "--entity-slot-8"),
    "--accent-primary": (WEB_TOKENS, "--accent-primary"),
    "--accent-bright": (HUB_FIELD_TOKENS, "--accent-bright"),
}


def _declarations(source: Path = WEB_TOKENS) -> dict[str, str]:
    """Every `--name: value` in a token file, unresolved."""
    text = source.read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", text)}


def resolve_pair(name: str, source: Path = WEB_TOKENS) -> str:
    """A token name to the `light-dark(#light, #dark)` the hub can inline.

    Walks `var()` aliases (`--entity-slot-1` -> a hue rung) and both halves of
    a `light-dark()` independently, because the two sides reach different
    primitives. Raises rather than guessing: a token that stops resolving is a
    rename in tokens.css, and emitting a grey in its place would look like a
    design choice.
    """
    decls = _declarations(source)
    seen: set[str] = set()

    def unwrap(value: str) -> str:
        while True:
            var = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
            if not var:
                return value.strip()
            key = var.group(1)
            if key in seen or key not in decls:
                raise SystemExit(f"graph_field: {name} does not resolve ({key} is a dead end)")
            seen.add(key)
            value = decls[key]

    value = unwrap(f"var({name})")
    pair = re.fullmatch(r"light-dark\(\s*(.+?)\s*,\s*(.+?)\s*\)", value)
    if not pair:
        raise SystemExit(f"graph_field: {name} is not a light-dark() pair: {value!r}")
    halves = []
    for half in pair.groups():
        seen.clear()
        hexed = unwrap(half)
        if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", hexed):
            raise SystemExit(f"graph_field: {name} resolves to {hexed!r}, which is not a hex")
        halves.append(hexed)
    return f"light-dark({halves[0]}, {halves[1]})"


def scalar(name: str, source: Path) -> str:
    """A token name to the plain duration, easing or number behind it.

    The same `var()` walk `resolve_pair` does, stopping at whatever is not
    another alias. Raises for the same reason: a duration that stops resolving
    is a rename, and a literal emitted in its place is a second opinion.
    """
    decls = _declarations(source)
    seen: set[str] = set()
    value = f"var({name})"
    while True:
        var = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
        if not var:
            return value.strip()
        key = var.group(1)
        if key in seen or key not in decls:
            raise SystemExit(f"graph_field: {name} does not resolve ({key} is a dead end)")
        seen.add(key)
        value = decls[key]


def _num(value: float) -> str:
    return str(int(value)) if float(value) == int(value) else str(value)


def svg(tokens: dict[str, str], *, prefix: str, indent: str = "  ") -> str:
    """The drawing itself. `prefix` names the classes each surface styles."""
    pad = indent
    out = [
        f'{pad}<svg class="{prefix}__still" viewBox="0 0 1600 1000" fill="none"'
        ' preserveAspectRatio="xMidYMid slice" focusable="false">',
        f'{pad}  <g stroke="{tokens["ink"]}" stroke-linecap="round"'
        f' stroke-opacity="{EDGE_OPACITY}">',
    ]
    for x1, y1, x2, y2, width, dashed in EDGES:
        dash = ' stroke-dasharray="4 4"' if dashed else ""
        out.append(
            f'{pad}    <line x1="{_num(x1)}" y1="{_num(y1)}" x2="{_num(x2)}" y2="{_num(y2)}"'
            f' stroke-width="{_num(width)}"{dash}/>'
        )
    out.append(f"{pad}  </g>")
    # The seven hubs get their OWN group so the stylesheet's `:nth-child` can
    # count them. Unwrapped they are `<g>` elements among the edge and
    # minor-node groups, so every stagger rule would miss and the field would
    # breathe as one blinking object -- silently, and only on a phone-sized
    # glance would anyone wonder why.
    out.append(f'{pad}  <g class="{prefix}-nodes">')
    for (cx, cy, r, _), role in zip(HUBS, ROLES, strict=True):
        out.append(
            f'{pad}    <g class="{prefix}-node"><circle cx="{_num(cx)}" cy="{_num(cy)}"'
            f' r="{_num(r)}" fill="{tokens[role]}"/></g>'
        )
    out.append(f"{pad}  </g>")
    out.append(f'{pad}  <g fill="{tokens["ink"]}" fill-opacity="{MINOR_OPACITY}">')
    for cx, cy, r in MINOR:
        out.append(f'{pad}    <circle cx="{_num(cx)}" cy="{_num(cy)}" r="{_num(r)}"/>')
    out.append(f"{pad}  </g>")
    out.append(f"{pad}</svg>")
    return "\n".join(out)


BANNER = "DO NOT EDIT. Generated by scripts/graph_field.py -- run `make field`."


def web() -> tuple[Path, str]:
    body = svg(WEB_TOKENS_MAP, prefix="graph-field", indent="    ")
    source = f"""<script setup lang="ts">
/**
 * {BANNER}
 *
 * The graph field, as the ground under a whole-page failure: the 404, the
 * login refusal, the offline notice. Placed BY THE PAGE rather than by
 * `StateNotice`, because the field is the page's ground and nesting it inside
 * that component's `Panel` would clip it to the panel.
 *
 * Positioned `absolute`, so the page it sits on owns the stacking context and
 * has to be `relative`. Its rules live in `web/src/styles/base.css` with the
 * other keyframes, because a component carries no CSS of its own here, and the
 * per-hub animation delays cannot be inline attributes for the same reason
 * `lint_tokens.py` refuses an arbitrary dimension in a template.
 *
 * Entity SLOTS, never entity types. `--entity-company` names the defense
 * ontology and this app serves several domains; decoration must not name a
 * type. See `scripts/graph_field.py`.
 */
</script>

<template>
  <div class="graph-field" aria-hidden="true">
{body}
  </div>
</template>
"""
    return REPO / "web" / "src" / "components" / "shared" / "GraphField.vue", source


def hub() -> tuple[Path, str]:
    tokens = {key: resolve_pair(name) for key, name in HUB_SOURCE.items()}
    body = svg(tokens, prefix="graph-field", indent="  ")
    # `data-graph-field` is what the canvas script queries for.
    field = f'<div class="graph-field" data-graph-field aria-hidden="true">\n{body}\n</div>'
    palette = "\n".join(
        f"    {name}: {resolve_pair(source_name, source)};"
        for name, (source, source_name) in CANVAS_SOURCE.items()
    )
    motion = {key: scalar(name, HUB_FIELD_TOKENS) for key, name in MOTION_SOURCE.items()}
    script_path, script_source = hub_script()
    digest = hashlib.sha256(script_source.encode()).hexdigest()[:12]
    script_url = f"/{script_path.stem}.{digest}{script_path.suffix}"
    delays = "\n".join(
        f"  .graph-field-nodes .graph-field-node:nth-child({n + 1})"
        f" {{ animation-delay: calc({motion['step']} * {n}); }}"
        for n in range(len(HUBS))
    )
    css = f"""  /* The field sits behind the panel and is untouchable: the copy stays
     selectable and any link clickable straight through it. */
  .graph-field {{ position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; }}
  /* What the canvas reads, plus the field's own opacity. `constellation.js`
     names no colour: it resolves each token off a probe element, so the graph
     follows the theme and a token change reaches it without anyone
     remembering it exists. The rest of this stylesheet inlines hexes because
     the hub has no token pipeline; these have to be custom properties because
     that is the interface the script was written against.

     THE OPACITY IS A CUSTOM PROPERTY FOR A SECOND REASON, and it is the one
     that bites. Written as a per-theme rule it needs a selector
     (`:root:not([data-theme='light']) .graph-field__still`) that outranks the
     one-class `--live` rule below, so the still never faded and both layers
     drew at once. Varying the VALUE per theme and leaving one rule for the
     element lets source order decide.

     Paper is the base and night is re-pointed. Four cases, two covered by the
     base:
       light system, no choice   -> base (paper)
       light system, chose dark  -> first block
       dark system, no choice    -> second block
       dark system, chose light  -> base, excluded by the `:not()` */
  :root {{
    --graph-field-opacity: {motion["opacity_paper"]};
{palette}
  }}
  @media (prefers-color-scheme: light) {{
    :root[data-theme='dark'] {{ --graph-field-opacity: {motion["opacity_night"]}; }}
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme='light']) {{ --graph-field-opacity: {motion["opacity_night"]}; }}
  }}
  /* The still WAITS, and the wait is the whole point. `backwards` fill holds
     it at zero through the delay, so on an ordinary load the canvas is already
     drawing before the still has shown itself and there is no swap to watch --
     and the swap is what a reader notices, because the still is a far larger
     composition than the canvas mesh and reads as a zoomed-in graph arriving
     and leaving. `hub/canvas/tokens.css` carries the timing under
     `--cadence-field-arrive`. */
  .graph-field__still {{
    width: 100%; height: 100%; display: block;
    opacity: var(--graph-field-opacity);
    transition: opacity {motion["reveal"]} {motion["ease"]};
    animation: graph-field-arrive {motion["reveal"]} {motion["ease"]} {motion["arrive"]} backwards;
  }}
  @keyframes graph-field-arrive {{
    from {{ opacity: 0; }}
  }}
  /* Set by the script once the canvas is actually drawing, so a browser that
     never runs it keeps the artwork it was served rather than a blank
     rectangle. Scripts off and `prefers-reduced-motion` both land here.
     Cancelling the arrival is what makes the wait free: reach here first and
     the still never appears, because it is already at zero and the cascade
     agrees. Arrive first and this is a fade instead, so a slow script hands
     over rather than cutting. */
  .graph-field--live .graph-field__still {{ animation: none; opacity: 0; }}
  .graph-field__canvas {{
    position: absolute; inset: 0; width: 100%; height: 100%; display: block;
    opacity: var(--graph-field-opacity);
  }}
  /* The still field breathes on a keyframe. It is the whole animation with
     scripts off, and the ground the canvas fades in over otherwise. */
  @keyframes graph-field-breathe {{
    from {{ opacity: {motion["pulse_low"]}; }}
    to {{ opacity: 1; }}
  }}
  .graph-field-node {{
    animation: graph-field-breathe {motion["cadence"]} {motion["ease"]} infinite alternate;
  }}
{delays}
  /* The transition and the arrival go with the keyframe. A reader who asked for
     stillness and gets a crossfade, or a field that keeps them waiting before it
     appears, has been given the motion they said no to. */
  @media (prefers-reduced-motion: reduce) {{
    .graph-field-node {{ animation: none; }}
    .graph-field__still {{ animation: none; transition: none; }}
  }}
"""
    source = f'''"""{BANNER}

The graph field for the hub's error pages: the same drawing the app carries, with its colours resolved out of `web/src/styles/tokens.css`
rather than hand-copied. The rest of `hub/hub/pages.py` copies the app's
palette by hand and only `tests/test_brand_lockup.py` holds it in place; this
half cannot drift at all.

The still SVG breathing on a keyframe is what a visitor keeps with scripts
off or `prefers-reduced-motion` set. `constellation.js` -- bundled from
`hub/canvas/` by `make field` -- lays a canvas
over it and fades the SVG back only once it is drawing.

`FIELD_CSS` goes through a `{{field_css}}` slot in `pages._SHELL`, so its
braces are NOT doubled: `str.format` does not recurse into what it
substitutes.

`SCRIPT_URL` names the bundle by a digest of its own bytes, which is what lets
`routers/brand.py` answer it as immutable: the URL cannot outlive the content
it names, so a year in a cache is a year of the right file. Both the route and
the `<script>` tag read it from here, so there is one spelling of it.
"""

# One `<line>` per edge, and the widest is 125 characters. Wrapping SVG inside
# a Python string to satisfy a line limit would put newlines in the markup to
# please a linter that is not reading the markup, so the rule is turned off for
# this file rather than the drawing bent around it.
# ruff: noqa: E501

from __future__ import annotations

SCRIPT_URL = "{script_url}"

FIELD = """{field}"""

FIELD_CSS = """{css}"""
'''
    return REPO / "hub" / "hub" / "field.py", source


#: The motion the hub's copy has to inline, and the token in
#: `hub/canvas/tokens.css` each value is read out of.
MOTION_SOURCE = {
    "step": "--cadence-graph-field-step",
    "cadence": "--cadence-graph-field",
    "pulse_low": "--graph-field-pulse-low",
    "reveal": "--motion-reveal",
    "ease": "--ease",
    "arrive": "--cadence-field-arrive",
    "opacity_paper": "--graph-field-opacity",
    "opacity_night": "--graph-field-opacity-night",
}

#: esbuild, from the hub operator app's node_modules. The hub's server-rendered
#: pages have no bundler, which is exactly why their copy is generated here.
ESBUILD = REPO / "hub" / "web" / "node_modules" / ".bin" / "esbuild"
HUB_SCRIPT_ENTRY = REPO / "hub" / "canvas" / "constellation.hub.ts"


@functools.cache
def hub_script() -> tuple[Path, str]:
    """The living graph, bundled for the hub.

    ONE SOURCE, TWO SURFACES. `constellation.ts` is 360 lines of canvas work
    that names no colour and now no class either; the hub differs by two class
    names and nothing else. A hand-written second copy would drift the first
    time either surface changed, and the hub cannot build TypeScript itself.

    Minified because nobody reads a generated file. Cached because `hub()` asks
    for these bytes too, to name the URL they are served at, and esbuild is a
    subprocess.
    """
    if not ESBUILD.exists():
        raise SystemExit(
            "graph_field: esbuild is missing. The hub's canvas is bundled with "
            "the hub app's toolchain: run `npm install` in hub/web/."
        )
    result = subprocess.run(
        [
            str(ESBUILD),
            str(HUB_SCRIPT_ENTRY),
            "--bundle",
            "--format=iife",
            "--target=es2022",
            "--minify",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"graph_field: esbuild failed\n{result.stderr}")
    banner = (
        "/* Generated by scripts/graph_field.py from "
        "hub/canvas/constellation.hub.ts. Do not edit; run `make field`. */\n"
    )
    return REPO / "hub" / "hub" / "static" / "constellation.js", banner + result.stdout


def export_palette() -> tuple[Path, str]:
    """The entity hues, as plain hexes the backend can read at runtime.

    GENERATED FOR THE SAME REASON THE HUB'S FIELD IS. `web/src/styles/tokens.css`
    is not in the API's runtime image -- the API image copies `backend/`,
    `fixtures/`, `config/`, `profile/` and the built `web/dist`, and no
    stylesheet source -- so an export writing Obsidian's graph colours cannot
    resolve a token when it runs. Generating them here means a token rename
    fails `make lint-generated` instead of quietly greying somebody's folder.

    THE LIGHT HALF of each pair. Obsidian's graph draws one colour per group
    whichever theme the reader is in, and the 700s carry on a light canvas and
    a dark one; the 500s wash out on white.

    Slots rather than type names, because which type holds which slot is in
    `config/ontology.yaml` and is editable at runtime. The mapping is resolved
    where it is used, never frozen here.
    """
    hexes = []
    for slot in range(1, 10):
        pair = resolve_pair(f"--entity-slot-{slot}")
        hexes.append(re.fullmatch(r"light-dark\((#[0-9a-fA-F]+), .+\)", pair).group(1))
    glossary = resolve_pair("--entity-glossary")
    grey = re.fullmatch(r"light-dark\((#[0-9a-fA-F]+), .+\)", glossary).group(1)
    body = "\n".join(f'    "{value}",' for value in hexes)
    source = f'''"""{BANNER}

The nine entity hues and the one for everything that is not an entity, as the
export writes them into a folder's graph settings. See `export_palette` in the
generator for why these are baked rather than read.
"""

from __future__ import annotations

#: Indexed by `color_slot - 1`. The ontology decides which type holds which.
SLOT_HEX: tuple[str, ...] = (
{body}
)

#: Terms, sources and briefs. Not an entity hue -- they are not entities.
NEUTRAL_HEX = "{grey}"
'''
    return REPO / "backend" / "venona" / "export" / "palette.py", source


EMITTERS = (web, hub, hub_script, export_palette)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale: list[str] = []
    for emitter in EMITTERS:
        path, source = emitter()
        rel = path.relative_to(REPO)
        current = path.read_text() if path.exists() else None
        if current == source:
            continue
        if check:
            stale.append(str(rel))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        print(f"wrote {rel}")
    if stale:
        print("graph_field: stale, run `make field` and commit:", ", ".join(stale), file=sys.stderr)
        return 1
    if check:
        print("graph_field: every generated copy is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
