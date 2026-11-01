"""Computed-style census: what the app actually renders, as data.

The Tailwind migration is supposed to change how styles are *authored* without
changing what they *resolve to*. Nothing in the existing tooling can tell those
apart -- typecheck, ruff and the offline suite never open a page, and
`ui_smoke.py` asserts behaviour, not geometry. So a wave that silently shifted
every gap by 4px would pass everything.

This is the oracle for that. It walks every element on every route, records the
computed value of the properties a restyle can move, and diffs two runs.

    uv run python scripts/style_audit.py --out before.json     # needs `make dev`
    ...
    uv run python scripts/style_audit.py --out after.json
    uv run python scripts/style_audit.py --diff before.json after.json

Waves 1-4 must diff EMPTY. Wave 5 is the only one allowed a non-empty diff, and
every entry in it gets read.

It is also the discovery tool -- `--report` clusters the distinct values per
property, which is how you find four paddings doing one job:

    uv run python scripts/style_audit.py --report before.json

Precedent: the Button consolidation in docs/adr/ was verified with a
computed-style diff rather than a screenshot, for the same reason. A screenshot
tells you something moved; this tells you what, where, and by how much.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

# The CDP harness is `ui_smoke.py`'s and stays there -- one browser driver, one
# place where the profile-leak and the port-reuse bugs are already fixed.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_smoke import RUN_DIR, Browser, launch  # noqa: E402

# The properties a restyle can move. Longhands, never shorthands: Chrome
# resolves `padding` to a shorthand string only when all four agree, so a
# shorthand column would go null exactly on the asymmetric paddings that are
# most worth watching.
PROPS = [
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "row-gap",
    "column-gap",
    "width",
    "height",
    "min-width",
    "min-height",
    "max-width",
    "max-height",
    "font-size",
    "font-weight",
    "font-family",
    "line-height",
    "letter-spacing",
    "text-transform",
    "text-align",
    "color",
    "background-color",
    "border-top-width",
    "border-right-width",
    "border-bottom-width",
    "border-left-width",
    "border-top-color",
    "border-right-color",
    "border-bottom-color",
    "border-left-color",
    "border-radius",
    "display",
    "flex-direction",
    "flex-wrap",
    "align-items",
    "justify-content",
    "flex-grow",
    "flex-shrink",
    "flex-basis",
    "grid-template-columns",
    "grid-template-rows",
    "position",
    "top",
    "right",
    "bottom",
    "left",
    "z-index",
    "overflow-x",
    "overflow-y",
    "opacity",
]

# Routes, and the four overlay states the app can only be seen in by driving it.
#
# Every selector here is an ARIA role, an aria-label or a data attribute -- NOT
# a class. Class names are exactly what this migration rewrites, so an oracle
# keyed on them would break in the wave it exists to check. `.card` is the one
# exception the app gives no attribute hook for; it is asserted, not assumed.
ROUTES = [
    "/",
    "/briefs",
    "/graph",
    "/admin/curation",
    "/admin/runs",
    "/settings/account",
    "/admin/config/sources",
]

# (name, route, opener, confirm-selector)
OVERLAYS = [
    ("palette", "/", ("key", "k", 2), '[aria-label="Search and jump"]'),
    ("keyboard-help", "/", ("key", "?", 0), '[aria-label="Keyboard shortcuts"]'),
    ("dialog", "/admin/runs", ("text", "Clear history"), "[role=dialog]"),
    ("hover-card", "/", ("click", "[data-entity-id]"), ".card"),
]

# Walk every element, key it by a path that survives a class rewrite.
#
# The key is the tag chain with sibling ordinals -- `body>div:0>header:0>nav:1`.
# It deliberately ignores class names and text: those are what changes. It does
# assume the DOM SHAPE is stable, which is true for waves 1-4 (moving a rule
# from a style block to a class attribute adds no elements) and deliberately
# false for wave 5, where new elements are the point.
CENSUS_JS = """
const PROPS = %s;
const out = {};
const path = (el) => {
  const parts = [];
  for (let n = el; n && n.nodeType === 1 && n.tagName !== 'HTML'; n = n.parentElement) {
    const i = n.parentElement ? [...n.parentElement.children].indexOf(n) : 0;
    parts.unshift(n.tagName.toLowerCase() + ':' + i);
  }
  return parts.join('>');
};
for (const el of document.querySelectorAll('body *')) {
  // Invisible elements have no geometry worth diffing, and `display:none`
  // subtrees would otherwise dominate the diff with meaningless zeroes.
  const cs = getComputedStyle(el);
  if (cs.display === 'none' || cs.visibility === 'hidden') continue;
  const rec = {};
  for (const p of PROPS) rec[p] = cs.getPropertyValue(p);
  // Kept for humans reading the diff, never for keying.
  rec['@class'] = el.getAttribute('class') || '';
  rec['@text'] = (el.childElementCount === 0 ? (el.textContent || '') : '').trim().slice(0, 40);
  out[path(el)] = rec;
}
return out;
"""


async def click_text(b: Browser, label: str) -> bool:
    """Click a control by its visible text.

    `ui_smoke.py` opens this dialog with `button.btn--outline`, which is a
    styling class and therefore something this migration is entitled to move.
    Text is the stable handle.
    """
    return await b.js(
        f"const want = {json.dumps(label)}.toLowerCase();"
        "const el = [...document.querySelectorAll('button, a')]"
        "  .find(e => (e.textContent || '').trim().toLowerCase().includes(want));"
        "if (!el) return false; el.click(); return true;"
    )


async def census(b: Browser) -> dict:
    return await b.js(CENSUS_JS % json.dumps(PROPS))


async def capture() -> dict:
    """One full pass over every route and overlay."""
    snap: dict[str, dict] = {}
    missing: list[str] = []
    b = await launch()
    try:
        for route in ROUTES:
            await b.goto(route, settle=3)
            snap[route] = await census(b)
            print(f"  {route:24} {len(snap[route])} elements")

        for name, route, opener, confirm in OVERLAYS:
            await b.goto(route, settle=3)
            kind = opener[0]
            if kind == "key":
                await b.key(opener[1], mods=opener[2])
            elif kind == "text":
                await click_text(b, opener[1])
            else:
                await b.click(opener[1])
            # Poll rather than sleep a fixed amount. A single 0.8s wait was
            # enough until the dev server got busy, and then the confirm dialog
            # started missing it -- which reads exactly like a broken dialog.
            for _ in range(20):
                await asyncio.sleep(0.25)
                if await b.js(f"return !!document.querySelector({json.dumps(confirm)})"):
                    break

            # An overlay that never opened would be recorded as an empty
            # snapshot, and an empty snapshot diffs clean against another empty
            # snapshot -- two silent passes rather than one loud failure.
            if not await b.js(f"return !!document.querySelector({json.dumps(confirm)})"):
                missing.append(name)
                print(f"  {name:24} DID NOT OPEN")
                continue
            snap[f"overlay:{name}"] = await census(b)
            print(f"  overlay:{name:16} {len(snap[f'overlay:{name}'])} elements")
            await b.key("Escape")
    finally:
        await b.close()
        for stale in RUN_DIR.glob("profile.*"):
            shutil.rmtree(stale, ignore_errors=True)

    if missing:
        raise SystemExit(f"style-audit: overlays never opened: {', '.join(missing)}")
    return snap


def diff(before: dict, after: dict) -> int:
    changes: list[str] = []
    for surface in sorted(set(before) | set(after)):
        b_els, a_els = before.get(surface, {}), after.get(surface, {})
        for key in sorted(set(b_els) | set(a_els)):
            b_rec, a_rec = b_els.get(key), a_els.get(key)
            if b_rec is None:
                changes.append(f"{surface} + {key}  [{a_rec.get('@class', '')}]")
                continue
            if a_rec is None:
                changes.append(f"{surface} - {key}  [{b_rec.get('@class', '')}]")
                continue
            moved = [
                f"{p}: {b_rec[p]} -> {a_rec[p]}" for p in PROPS if b_rec.get(p) != a_rec.get(p)
            ]
            if moved:
                hint = a_rec.get("@text") or a_rec.get("@class", "")
                changes.append(f"{surface} ~ {key}  [{hint}]")
                changes += [f"      {m}" for m in moved]

    if not changes:
        print("style-audit: no computed-style change")
        return 0
    print("\n".join(changes))
    n = sum(1 for c in changes if not c.startswith("      "))
    print(f"\nstyle-audit: {n} elements changed")
    return 1


def report(snap: dict) -> int:
    """Distinct values per property, across everything. The inconsistency census."""
    seen: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for els in snap.values():
        for rec in els.values():
            for p in PROPS:
                v = rec.get(p, "")
                if v and v not in ("none", "auto", "normal", "0px", "rgba(0, 0, 0, 0)"):
                    seen[p][v] += 1

    for p in PROPS:
        vals = seen.get(p)
        if not vals or len(vals) < 2:
            continue
        ordered = sorted(vals.items(), key=lambda kv: -kv[1])
        print(f"\n{p}  ({len(vals)} distinct)")
        for v, n in ordered[:14]:
            print(f"    {n:5}  {v}")
        if len(ordered) > 14:
            print(f"    ...   {len(ordered) - 14} more")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, help="capture a snapshot to this path (needs `make dev`)")
    ap.add_argument("--diff", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    ap.add_argument("--report", type=Path, metavar="SNAPSHOT")
    args = ap.parse_args()

    if args.diff:
        return diff(json.loads(args.diff[0].read_text()), json.loads(args.diff[1].read_text()))
    if args.report:
        return report(json.loads(args.report.read_text()))
    if not args.out:
        ap.error("one of --out, --diff or --report is required")

    snap = asyncio.run(capture())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snap, indent=1, sort_keys=True))
    total = sum(len(v) for v in snap.values())
    print(f"style-audit: {total} elements across {len(snap)} surfaces -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
