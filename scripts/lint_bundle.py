"""The route splits get something that notices when they break.

WHAT IT GUARDS, in two layers:

  1. THE GRAPH ENGINE and the CONFIGURATION EDITOR stay out of the eager graph. Cytoscape, fcose and their
     layout bases are roughly 200KB gzipped, and `useCytoscape.ts` imports
     `cytoscape` at module scope and calls `cytoscape.use` there. Anything in
     the EAGER module graph that value-imports it drags the whole engine into
     the entry chunk, and every text-only view downloads and parses a graph
     library.
  2. THE EAGER/LAZY LINE in `router/index.ts` is pinned. The entry chunk
     carries the reader's path only (brief, archive, lookup, search, the two
     public pages); every operator view loads on first navigation. That split
     took the entry from 96KB gzipped to 55KB, and it regresses one static
     import at a time -- each looking exactly like the imports around it.

WHY THIS IS A GREP AND NOT A BUILD. Three reasons, in order of weight:

  1. The split currently holds by two `import type` keywords -- `stores/ui.ts`
     and `components/graph/GraphViewOptions.vue` both import `GraphSpacing` from
     `useCytoscape`, and `verbatimModuleSyntax` erases them. Deleting one `type`
     is a ONE-CHARACTER regression that looks like nothing in review. A grep can
     see exactly that distinction; a chunk-size threshold can only see the
     aftermath.
  2. `manualChunks` would HIDE this rather than catch it. With a manual chunk, a
     stray eager import still produces a separate cytoscape chunk -- the entry
     just imports it eagerly, so the bytes still land on first paint while any
     "is there a separate chunk?" assertion passes.
  3. Scraping `vite build`'s stdout would couple this to a formatting decision
     of the bundler's, and the bundler is about to move two majors.

So: no build, no node_modules, no network, milliseconds. In the idiom of
`lint_determinism.py` -- deliberately dumb, deliberately noisy.

MEASURED BASELINE, from an actual `npm run build` on 2026-09-03, so the numbers
above are observed rather than estimated:

    entry (index)    66,820 B gzipped    200,930 raw
    ConfigView      121,330 B gzipped    369,610 raw
    GraphView       188,010 B gzipped    609,650 raw

Neither `fcose` nor `@codemirror/*` appears in the entry chunk. That is the
state this guard exists to keep, and the two lazy chunks are what it costs:
each is larger than the entry, and each is downloaded only by someone who
navigated to the one route that needs it. Re-measure with `npm run build` if you
want a fresh number; do NOT put a size threshold in `make lint`, which would
fail for reasons that are not defects.

WHAT IT CANNOT SEE. Dynamic `import()` with a computed specifier, and re-exports
that launder a value import through another module. Both would need a real
module graph; neither has ever appeared in this codebase. If one does, this
target is the place to find out why it stopped catching things.
"""

from __future__ import annotations

import pathlib
import re
import sys

WEB_SRC = pathlib.Path("web/src")

#: Entry point of the eager graph.
ENTRY = WEB_SRC / "main.ts"

#: The views the entry chunk is ALLOWED to carry: the reader's path. Everything
#: else in `views/` must be reached through `import()` in the router. Adding a
#: name here is a deliberate decision about first-paint weight -- re-measure
#: with `npm run build` when you do.
EAGER_VIEWS = {
    "ArchiveView",
    "BriefView",
    "LoginView",
    "LookupView",
    "RequestAccessView",
    "SearchView",
}

#: The lazy route roots: every view not on the eager list. The walk stops here
#: -- everything below them is legitimately in a lazy chunk, which is the
#: entire point.
LAZY_ROOTS = {path for path in (WEB_SRC / "views").glob("*.vue") if path.stem not in EAGER_VIEWS}

#: Value-importing any of these from the eager graph un-splits the engine.
FORBIDDEN = {
    "cytoscape",
    "cytoscape-fcose",
    "@/composables/useCytoscape",
    "@/composables/useGraphEmphasis",
    "@/components/config/CodeEditor.vue",
}

#: The same rule for a FAMILY of packages. CodeMirror ships as nine `@codemirror/*`
#: packages plus `@lezer/*`, so no set of exact names can hold it: one more
#: `@codemirror/lang-...` added a year from now would be a new specifier nothing
#: listed here would notice.
#:
#: It belongs to `/admin/config/:key`, which is a lazy route -- an operator
#: surface, reached by an administrator who came to edit a file, and no part of
#: a reader's path. See docs/adr/0200.
FORBIDDEN_PREFIXES = ("@codemirror/", "@lezer/")

# `import ... from 'x'` and bare `import 'x'`, with the `type` forms excluded.
# `import type {...}` and `import type X` are erased by `verbatimModuleSyntax`
# and cost nothing at runtime -- distinguishing them is the whole job.
_IMPORT = re.compile(
    r"""^\s*import\s+(?!type\s)(?P<clause>[^'";]*?\s+from\s+)?['"](?P<spec>[^'"]+)['"]""",
    re.MULTILINE,
)


def _script_of(path: pathlib.Path) -> str:
    """The code of a module. For an SFC, only its `<script>` blocks.

    A `<template>` cannot import, and its text would otherwise match the regex
    inside a code sample or a comment.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".vue":
        return text
    return "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", text, re.DOTALL))


def _specifiers(path: pathlib.Path) -> list[str]:
    """Every runtime (non-type) import specifier in one module."""
    out: list[str] = []
    for match in _IMPORT.finditer(_script_of(path)):
        clause = match.group("clause") or ""
        # `import { type A, type B } from 'x'` is type-only in effect: every
        # named binding is marked. Anything else brings the module in.
        names = clause.strip().removesuffix("from").strip().strip("{}").split(",")
        named = [n.strip() for n in names if n.strip()]
        if named and all(n.startswith("type ") for n in named):
            continue
        out.append(match.group("spec"))
    return out


def _resolve(spec: str, importer: pathlib.Path) -> pathlib.Path | None:
    """`@/x` and `./x` to a file on disk. Bare packages resolve to None."""
    if spec.startswith("@/"):
        base = WEB_SRC / spec[2:]
    elif spec.startswith("."):
        base = (importer.parent / spec).resolve()
        try:
            base = base.relative_to(pathlib.Path.cwd())
        except ValueError:
            return None
    else:
        return None

    for candidate in (base, *(base.with_suffix(s) for s in (".ts", ".vue")), base / "index.ts"):
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    if not ENTRY.is_file():
        print(f"lint-bundle: {ENTRY} not found -- run from the repo root.")
        return 1

    violations: list[str] = []
    seen: set[pathlib.Path] = set()
    queue = [ENTRY]

    while queue:
        module = queue.pop()
        if module in seen:
            continue
        seen.add(module)

        for spec in _specifiers(module):
            # BEFORE `_resolve`, which returns None for a bare package and would
            # drop these on the floor.
            if spec.startswith(FORBIDDEN_PREFIXES):
                violations.append(
                    f"{module}: value-imports `{spec}` from the EAGER graph.\n"
                    f"    That pulls the CodeMirror editor into the entry chunk, which belongs\n"
                    f"    to the reader's path -- and no reader edits a configuration document.\n"
                    f"    It is reached through `router/index.ts`'s `import()` of ConfigView."
                )
                continue

            if spec in FORBIDDEN:
                violations.append(
                    f"{module}: value-imports `{spec}` from the EAGER graph.\n"
                    f"    That pulls cytoscape (~200KB gzipped) into the entry chunk, so every\n"
                    f"    text-only view downloads and parses a graph library. Use `import type`\n"
                    f"    if you only need a type -- that is what `stores/ui.ts` does."
                )
                continue

            target = _resolve(spec, module)
            if target is None:
                continue
            if target in LAZY_ROOTS:
                violations.append(
                    f"{module}: statically imports `{spec}`, which is a LAZY route root.\n"
                    f"    `router/index.ts` loads it with `import()` precisely so it stays out of\n"
                    f"    the entry chunk; one static import anywhere eager un-splits all of it."
                )
                continue
            queue.append(target)

    if violations:
        print("lint-bundle FAILED:\n")
        for v in violations:
            print(f"  {v}\n")
        return 1

    print(f"lint-bundle ok ({len(seen)} eager modules, graph engine not among them)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
