"""Three things CSS will not tell you, and all three shipped once.

CSS has no failure mode. A malformed declaration is dropped, an invalid value
falls back to the property's initial, and the page renders -- wrongly, and
confidently. None of the defects below produced a warning anywhere: not in
`vite build`, not in `vue-tsc`, not in `make lint`, not in the browser console.
They were found by looking at the page and not believing it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

STYLESHEETS = (
    "web/src/styles/tokens.css",
    "web/src/styles/theme.css",
    "web/src/styles/base.css",
    "web/src/styles/prose.css",
    "hub/canvas/tokens.css",
    "extension/src/content/card.css",
    "extension/src/options/options.css",
)

# Properties that can legally hold a colour, so a `light-dark()` in one is fine.
# Anything not matching is a property whose value is a number, a length, a
# keyword or a time -- where `light-dark()` is not a value at all.
#
# Deliberately generous, including every shorthand that can carry a colour
# (`border`, `background`, `outline`, `box-shadow`, `text-decoration`) and every
# custom property, since a token may legitimately hold a colour for someone else
# to use. A false NEGATIVE here is one rule going unchecked; a false positive is
# a test that fails on correct code, and this is the direction to err.
COLOUR_CONTEXT = re.compile(
    r"^(color|fill|stroke"
    r"|background|background-color|background-image"
    r"|border|border-[a-z-]+|outline|outline-[a-z-]+"
    r"|box-shadow|text-shadow|text-decoration|text-decoration-[a-z-]+"
    r"|caret-color|accent-color|column-rule|column-rule-[a-z-]+"
    r"|-webkit-text-fill-color|-webkit-mask-image|mask-image"
    r"|--[\w-]+)$"
)


def _strip_comments(css: str) -> str:
    """Comments out, newlines preserved so reported line numbers stay honest."""
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), css, flags=re.S)


def test_every_declaration_is_a_declaration() -> None:
    """Prose that escapes a comment is swallowed, silently, along with the
    declaration after it.

    THIS IS NOT HYPOTHETICAL. A block comment in `hub/canvas/tokens.css`
    was extended by pasting a paragraph AFTER its `*/`. The parser, inside a
    declaration block, read the paragraph as a property name -- there was a colon
    in it -- and then consumed everything up to the next `;` as its value, which
    swallowed `--accent-primary` whole. One token vanished. The hero's rotator
    lost its underline and its colour; the buttons kept working because they use
    `--accent-muted`; and nothing anywhere reported an error.

    Anything between `{` and `}` that is not `property: value` fails here.
    """
    failures: list[str] = []
    for name in STYLESHEETS:
        source = _strip_comments((REPO / name).read_text())
        depth, buffer, line, opened = 0, "", 1, 1
        for char in source:
            if char == "\n":
                line += 1
            if char == "{":
                depth += 1
                buffer, opened = "", line
                continue
            if char == "}":
                depth -= 1
                if buffer.strip():
                    failures.append(f"{name}:~{opened}: trailing {buffer.strip()[:80]!r}")
                buffer = ""
                continue
            if depth == 0:
                continue
            if char != ";":
                buffer += char
                continue
            statement = buffer.strip()
            # `--name-*: initial` is Tailwind's namespace-clearing syntax, legal
            # only inside `@theme`, which is why it appears in exactly one file.
            # It is a real declaration; the `*` is what a generic property-name
            # pattern does not expect.
            if statement and not re.fullmatch(
                r"(--[\w-]*\*?|[-a-zA-Z][\w-]*)\s*:\s*.+", statement, re.S
            ):
                failures.append(f"{name}:~{opened}: {statement[:80]!r}")
            buffer, opened = "", line
    assert not failures, "not a declaration:\n  " + "\n  ".join(failures)


def _light_dark_tokens() -> set[str]:
    """Every token, in any of these files, whose value carries a `light-dark()`."""
    found: set[str] = set()
    for name in STYLESHEETS:
        source = _strip_comments((REPO / name).read_text())
        found |= {
            token
            for token, value in re.findall(r"(--[\w-]+)\s*:\s*([^;}]+)", source)
            if "light-dark(" in value
        }
    return found


def test_light_dark_is_only_ever_asked_for_a_colour() -> None:
    """`light-dark()` takes COLOURS. It shipped once holding two numbers.

    `--page-graph-opacity: light-dark(0.022, 0.05)` is not a value. The
    declaration consuming it is invalid at computed-value time, so `opacity`
    falls back to its initial `1` -- and a separate front page's background graph, a
    texture meant to sit at 5%, painted at FULL STRENGTH on both themes. The
    report was "it lost its subtlety" and "the nodes are in the foreground",
    neither of which points anywhere near `light-dark()`.

    Numbers take the long shape instead: each side declared under its own name,
    and only the pointer re-aimed under `prefers-color-scheme` and `[data-theme]`.

    The same trap caught `--ring-focus`, which is a `box-shadow` -- a shadow is
    not a colour either, so its colour was lifted into `--focus-wash` and the
    shadow references that. That one was caught while writing it. This catches
    the next one.
    """
    tokens = _light_dark_tokens()
    assert tokens, "no light-dark() tokens found at all -- the parser has stopped working"
    failures: list[str] = []
    for name in STYLESHEETS:
        source = _strip_comments((REPO / name).read_text())
        for lineno, line in enumerate(source.splitlines(), start=1):
            # Only what is INSIDE a block. A one-line rule
            # (`a:hover { color: var(--accent-hover); }`) otherwise reads as a
            # declaration whose property is `a` -- the selector -- and every such
            # rule in the repo failed as a false positive.
            body = line.rsplit("{", 1)[-1] if "{" in line else line
            if "}" in body:
                body = body.split("}", 1)[0]
            for fragment in body.split(";"):
                match = re.match(r"\s*([-a-zA-Z][\w-]*)\s*:\s*(.+)", fragment)
                if not match:
                    continue
                prop, value = match.group(1), match.group(2)
                if COLOUR_CONTEXT.match(prop):
                    continue
                for token in sorted(set(re.findall(r"var\(\s*(--[\w-]+)", value)) & tokens):
                    failures.append(
                        f"{name}:{lineno}: `{prop}` consumes {token}, which is a light-dark(). "
                        f"That declaration is invalid and the property falls back to its "
                        f"initial value, silently."
                    )
    assert not failures, "light-dark() where a colour is not expected:\n  " + "\n  ".join(failures)


# ── a stylesheet nobody imports ──────────────────────────────────────────
#
