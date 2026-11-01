#!/usr/bin/env python3
"""Contract C5 enforcement: colour, dimension and type may not leave the token layer.

The three colour checks, and what each is worth:

  1. No raw colour outside tokens.css -- else light mode is a rewrite, not a swap.
  2. No tier-1 primitive in a component -- a primitive carries no meaning, so
     consuming one opts silently out of theming.
  3. No tier-2 name tokens.css does not declare -- a typo'd custom property is
     not a CSS error; it resolves to nothing and the element renders transparent.

The banner comments below open the dimension and type halves.

Run: make lint-tokens
"""

from __future__ import annotations

import pathlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = REPO_ROOT / "web" / "src"
TOKENS_FILE = WEB_SRC / "styles" / "tokens.css"

# The extension ships the app's card into somebody else's page, so C5 reaches it
# too. Left out, `extension/` grew a stylesheet of raw hex and a set of
# hard-coded badge colours that `make lint` could not see.
EXT_SRC = REPO_ROOT / "extension" / "src"

# The hub's operator app imports `web/src/styles/{tokens,theme,base}.css`, so it
# is on the app's own cascade and belongs in the FULL web lane: no raw colour, no
# tier-1 primitive, no undeclared token.
HUB_SRC = REPO_ROOT / "hub" / "web" / "src"

# The front door's hand-written rules. NOT `hub/hub/static/public.css` beside
# it, which `scripts/hub_public_styles.py` emits: that file IS the token block,
# so scanning it would report every tier-1 primitive it exists to carry.
HUB_PUBLIC = REPO_ROOT / "hub" / "hub" / "styles"
SCAN_ROOTS = (WEB_SRC, EXT_SRC, HUB_SRC, HUB_PUBLIC)

VAR_USE = re.compile(r"var\(\s*(--[a-zA-Z0-9-]+)")
VAR_DECL = re.compile(r"^\s*(--[a-zA-Z0-9-]+)\s*:", re.MULTILINE)

# The banner comments separating the tiers in tokens.css, and LOAD-BEARING:
# `_tier1_prefixes()` finds the primitive block by looking for them, so a rename
# that looks cosmetic would empty the tier-1 block and stop check 2 firing for
# every family at once.
TIER1_BANNER = "══ Tier 1: primitives ══"
TIER2_BANNER = "══ Tier 2: semantic ══"


def _tier1_prefixes() -> tuple[str, ...]:
    """The primitive families, DERIVED from tokens.css rather than listed here.

    A family missing from a hand-maintained list is not "unenforced pending
    cleanup": check 2 never fires for it, so a component may write
    `var(--moss-500)` and `make lint-tokens` passes clean. `--moss-`, `--plum-`
    and `--rose-` sat in exactly that state, unremarked from the day the
    ontology-v3 organisation split added them (2026-08-16).

    A prefix is everything up to and including the first hyphen after the `--`,
    which makes `--wash-accent-strong` and `--wash-focus` one family rather than
    two, and `--grey-995` and `--grey-000` one rather than a hundred.
    """
    source = TOKENS_FILE.read_text()
    try:
        start = source.index(TIER1_BANNER)
        end = source.index(TIER2_BANNER)
    except ValueError:
        print(
            "lint-tokens: cannot find the tier banners in tokens.css.\n"
            f"  expected {TIER1_BANNER!r} then {TIER2_BANNER!r}\n"
            "  These comments are load-bearing: the tier-1 family list is derived\n"
            "  from the block between them. Without them check 2 would pass\n"
            "  vacuously for every primitive family in the file.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    if start > end:
        print("lint-tokens: the tier banners in tokens.css are out of order", file=sys.stderr)
        raise SystemExit(2)

    names = VAR_DECL.findall(source[start:end])
    prefixes = {re.sub(r"^(--[a-zA-Z0-9]+-).*$", r"\1", name) for name in names}
    unfamilied = sorted(name for name in names if not re.fullmatch(r"--[a-zA-Z0-9]+-.+", name))
    if unfamilied:
        print(
            "lint-tokens: tier-1 tokens with no family segment: "
            + ", ".join(unfamilied)
            + "\n  A primitive is `--family-rung`. Without the hyphen there is no\n"
            "  prefix to enforce and the token is silently reachable from a component.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return tuple(sorted(prefixes))


TIER1_PREFIXES = _tier1_prefixes()


# `color-mix()` and the CIE functions are listed because catching `oklch(` alone
# left a hole a component could synthesise any colour it liked through. Not
# hypothetical: `prose.css` ships a `color-mix()` today, legal via the per-file
# allowance below because mixing with `currentColor` is a relationship rather
# than a value. A new one has to be argued for rather than slipping through.
RAW_COLOR = re.compile(
    r"(?<![\w-])("
    r"#[0-9a-fA-F]{3,8}\b"
    r"|\brgba?\s*\("
    r"|\bhsla?\s*\("
    r"|\boklch\s*\("
    r"|\boklab\s*\("
    r"|\blab\s*\("
    r"|\blch\s*\("
    r"|\bcolor-mix\s*\("
    r"|\bcolor\s*\("
    r")"
)
# The one legitimate escape, mirroring `lint_determinism.py`'s `# determinism: ok`.
# It exists for colour FUNCTIONS OF `currentColor` -- a mention's hover wash is
# "14% of whatever this entity's hue already is", a relationship the token layer
# cannot express because it depends on the element. Not for literals: a `#hex`
# carrying this comment is still a design decision escaping the token layer.
#
# It suppresses the TYPE checks on the same terms, for code that GENERATES CSS
# rather than declaring it: `@font-face` is document-scoped, so the extension's
# card cannot take its typefaces from a stylesheet inside its shadow root and
# builds the rule in TypeScript instead (`shared/fonts.ts`, which reads the
# family off the token -- a hard-coded name there would still be a violation).
# Same test either way: plumbing, or a design decision sneaking past?
ALLOW = re.compile(r"token-lint:\s*ok")


# Contract C5 names entity colour tokens after the C4 id prefix so a consumer can
# resolve one with `var(--entity-${id.split(':')[0]})` and no lookup table. A
# supported pattern, not an escape hatch -- so rather than exempting it, check
# that EVERY prefix the family can produce has a declared token.
DYNAMIC_VAR = re.compile(r"var\(\s*`?(--[a-zA-Z0-9-]*?)\$\{")


def _entity_token_members() -> tuple[str, ...]:
    """Derived from config/ontology.yaml so adding an entity type without its
    colour token is a build failure rather than a transparent element."""
    import yaml

    raw = yaml.safe_load((REPO_ROOT / "config" / "ontology.yaml").read_text())
    prefixes = [spec["prefix"] for spec in (raw.get("entity_types") or {}).values()]
    # Not ontology types, but they share the token family and the id namespace.
    return tuple([*prefixes, "glossary", "undefined"])


DYNAMIC_FAMILIES: dict[str, tuple[str, ...]] = {"--entity-": _entity_token_members()}

SCANNED_SUFFIXES = {".css", ".vue", ".ts", ".tsx"}

# Comment spans, masked out before any check runs. Skipping a line only when it
# STARTS with `/*`, `*` or `//` misses a wrapped block comment whose continuation
# begins with a quote or a backtick -- most of the prose in this repo, because
# these comments name tokens and families -- and scans it as code, so `#hex` or
# `Arial` written mid-sentence while explaining why not to becomes a false
# positive. Masking preserves line and column count, so line numbers stay right.
BLOCK_COMMENT = re.compile(r"/\*.*?\*/|<!--.*?-->", re.S)


def mask_comments(source: str) -> str:
    def blank(match: re.Match[str]) -> str:
        return "".join(ch if ch == "\n" else " " for ch in match.group(0))

    masked = BLOCK_COMMENT.sub(blank, source)
    # `//` only when it opens the line: mid-line it is far more likely a URL
    # (`https://example.com`), and getting that wrong blinds the checks to the rest.
    return "\n".join("" if line.lstrip().startswith("//") else line for line in masked.splitlines())


def declared_tokens() -> set[str]:
    if not TOKENS_FILE.exists():
        print(f"lint-tokens: missing {TOKENS_FILE.relative_to(REPO_ROOT)}", file=sys.stderr)
        raise SystemExit(2)
    return set(VAR_DECL.findall(TOKENS_FILE.read_text()))


# A custom property's COMPUTED VALUE is its declared token stream, not a resolved
# one: `getComputedStyle(root).getPropertyValue('--surface-base')` returns the
# literal text `light-dark(var(--grey-075), var(--grey-950))`. Contract C5.
#
# The consumer that cannot cope is the graph. Cytoscape draws to a CANVAS: it
# takes no `var()`, no `light-dark()`, and errors on neither -- an unparseable
# colour silently becomes its own default, so the graph renders in the wrong
# palette and reads as a design decision. Twenty-odd reads were doing exactly
# this.
#
# The fix is a probe element (`useCytoscape.ts::colour`). This catches the
# literal form only; a read through a variable is not statically visible, which
# is why `token()` in that file carries the warning in its own docstring.
COMPUTED_READ = re.compile(r"getPropertyValue\(\s*['\"](--[a-zA-Z0-9-]+)['\"]")


def _colour_tokens() -> frozenset[str]:
    """Every declared token that resolves to a colour rather than a length."""
    raw = dict(
        re.findall(r"^\s*(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);", TOKENS_FILE.read_text(), re.MULTILINE)
    )
    colours = set()
    for name in raw:
        value, seen = raw[name].strip(), {name}
        while True:
            if value.startswith("light-dark("):
                colours.add(name)
                break
            reference = re.fullmatch(r"var\(\s*(--[a-zA-Z0-9-]+)\s*\)", value)
            if reference and reference.group(1) in raw and reference.group(1) not in seen:
                seen.add(reference.group(1))
                value = raw[reference.group(1)].strip()
                continue
            if RAW_COLOR.search(value):
                colours.add(name)
            break
    return frozenset(colours)


COLOUR_TOKENS = _colour_tokens()


def check_computed_colour_reads(rel: Path, lineno: int, line: str, violations: list[str]) -> None:
    for token in COMPUTED_READ.findall(line):
        if token in COLOUR_TOKENS:
            violations.append(
                f"{rel}:{lineno}: getPropertyValue({token!r}) reads a COLOUR token's "
                f"computed value, which is its token stream -- literally "
                f"`light-dark(...)`, not a colour. Resolve it through a probe "
                f"element; see `useCytoscape.ts::colour`. A canvas consumer will "
                f"not error on this, it will just draw the wrong palette."
            )


def check_tier_collisions(violations: list[str]) -> None:
    """No tier-2 name may look like a tier-1 primitive.

    The two checks below both key off `TIER1_PREFIXES`, and they disagree about
    what to do with a name that matches one: check 2 reports "tier-1 primitive
    referenced -- components may only use tier-2 semantic tokens", check 3 asks
    whether it is declared. So a semantic token named `--grey-page` would be
    declared, legal to consume by C5's own rules, and rejected at every call site
    with a message telling the reader to use a tier-2 token -- which is what it
    IS. The reader's only way out is renaming the token, so the linter may as
    well say that at the declaration rather than at twenty consumers.

    This became reachable when `TIER1_PREFIXES` started being DERIVED: adding a
    primitive family now silently claims a namespace that tier 2 may already be
    using, and nothing else would notice.
    """
    source = TOKENS_FILE.read_text()
    tier2 = VAR_DECL.findall(source[source.index(TIER2_BANNER) :])
    for name in tier2:
        if name.startswith(TIER1_PREFIXES):
            family = next(p for p in TIER1_PREFIXES if name.startswith(p))
            violations.append(
                f"tokens.css: tier-2 token {name} sits inside the tier-1 family "
                f"{family}* -- every component that consumes it will be rejected "
                f"as referencing a primitive. Rename one of the two."
            )


def scan_files() -> list[Path]:
    found: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        found += [
            p
            for p in sorted(root.rglob("*"))
            if p.suffix in SCANNED_SUFFIXES
            and p != TOKENS_FILE
            and not {"node_modules", "dist", "generated"} & set(p.parts)
        ]
    return found


#: `theme.css` declares names `tokens.css` does not -- the Tailwind container
#: scale among them -- and the hub's front door consumes them through a generated
#: block resolving BOTH files, so a surface reading only `tokens.css` would call
#: `--container-hint` a typo. The two are unioned for the typo check.
THEME_FILE = WEB_SRC / "styles" / "theme.css"


def main() -> int:
    declared = declared_tokens()
    if THEME_FILE.exists():
        declared = declared | set(VAR_DECL.findall(THEME_FILE.read_text()))
    violations: list[str] = []

    for path in scan_files():
        rel = path.relative_to(REPO_ROOT)
        raw = path.read_text()
        source = mask_comments(raw)
        # The escape hatch is written IN a comment, so it is read off the
        # unmasked text -- masking it away disarmed every `token-lint: ok` at
        # once, and `prose.css`'s `color-mix()` failed with its annotation intact.
        raw_lines = raw.splitlines()
        # A component may define its OWN custom property (`--dot`, set from an
        # inline style binding and consumed by a scoped rule): component-local
        # state, not a design decision escaping the token layer. The pattern takes
        # both CSS (`--dot: red`) and the quoted JS object key
        # (`:style="{ '--dot': ... }"`) a template passes one in with.
        local = set(re.findall(r"""['"]?(--[a-zA-Z0-9-]+)['"]?\s*:""", source))
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("/*", "*", "//")):
                continue

            annotated = bool(ALLOW.search(raw_lines[lineno - 1]))

            if (match := RAW_COLOR.search(line)) and not annotated:
                violations.append(
                    f"{rel}:{lineno}: raw color {match.group(0)!r} -- use a tier-2 semantic "
                    f"token, or annotate `/* token-lint: ok -- why */` if it is a "
                    f"relationship to currentColor rather than a value"
                )

            # Resolve dynamic families first so their partial stems are not
            # then reported as undeclared tokens.
            dynamic_stems = set(DYNAMIC_VAR.findall(line))
            for stem in dynamic_stems:
                members = DYNAMIC_FAMILIES.get(stem)
                if members is None:
                    violations.append(
                        f"{rel}:{lineno}: dynamic token family {stem}${{...}} is not "
                        f"registered -- add it to DYNAMIC_FAMILIES so its members "
                        f"can be verified"
                    )
                    continue
                for member in members:
                    if f"{stem}{member}" not in declared:
                        violations.append(
                            f"{rel}:{lineno}: dynamic family {stem}${{...}} can produce "
                            f"{stem}{member}, which is not declared in tokens.css"
                        )

            check_computed_colour_reads(rel, lineno, line, violations)
            check_arbitrary_dimensions(path, rel, lineno, line, violations)
            if not annotated:
                check_type(path, rel, lineno, line, violations)

            for token in VAR_USE.findall(line):
                if token in dynamic_stems:
                    continue  # already validated above as a family
                if token.startswith(TIER1_PREFIXES):
                    violations.append(
                        f"{rel}:{lineno}: tier-1 primitive {token} referenced "
                        f"-- components may only use tier-2 semantic tokens"
                    )
                elif token not in declared and token not in local:
                    violations.append(
                        f"{rel}:{lineno}: undeclared token {token} "
                        f"-- not defined in styles/tokens.css (typo renders transparent)"
                    )

    check_tier_collisions(violations)
    check_style_blocks(violations)
    check_raw_buttons(violations)
    check_raw_inputs(violations)
    check_slot_only_props(violations)
    check_slot_only_blocks(violations)
    check_numeric_input_bindings(violations)
    check_menu_items_keep_their_names(violations)

    if violations:
        print("lint-tokens FAILED\n", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(f"\n{len(violations)} violation(s)", file=sys.stderr)
        return 1

    print(f"lint-tokens ok ({len(declared)} tokens, {len(scan_files())} files)")
    return 0


# ══ The dimension half of C5 ═════════════════════════════════════════
#
# Everything above this line checks COLOUR. With only that gate, the app drifted
# (measured 2026-08-06) to nine distinct `padding-top` values, eight gaps, and
# four different gaps inside one 44px header bar: `padding: 2px 4px`,
# `max-height: 460px` and `font-size: 10px` are not colours, so nothing objected.
#
# The closed theme in `styles/theme.css` does most of the work -- `p-9` and
# `bg-red-500` cannot be written because they are not generated. These three
# checks cover what a closed theme cannot.

# A scoped block is CSS that opted out of the utility layer. Two files still need
# one, both for a grid track list: `GraphView` because its rail widths are custom
# properties set inline, so a collapse is a `grid-template-columns` transition
# rather than a `v-if` that would take the Cytoscape instance with it, and
# `ViewShell` because the prose grid places children nothing else can place.
#
# A BUDGET, not just a name. Allowlisting on file identity alone granted a whole
# file permanent amnesty: `GraphView.vue` duly grew to 138 lines against a reason
# that covers about seven of them, all of it ordinary component styling the
# prohibition forbids. The sizes ARE the budgets -- raising one is a reviewable
# diff, shrinking is free and ratchets it down when someone next looks.
STYLE_ALLOWLIST = {
    "web/src/components/ui/ViewShell.vue": (
        188,
        "the prose grid: rail-margin / measure / rail-side, its rail-less band, "
        "and both ends of the chrome cancellation",
    ),
    # 123 -> 188 across 2026-08-15..09-02, each step the REASON growing rather
    # than the file, and each bought by a defect ViewShell's own style block now
    # documents: `.state` had no `grid-area`, so it auto-placed into the margin
    # rail and rendered 104px wide -- one character per line on the first-run
    # screen; the band below 1180px earned a centred composition when a tablet
    # reader hit the prose-pinned-left failure; the loading state under
    # `scroll="none"` was the flex column's last child and the stretch rule
    # centred the spinner mid-screen on Lookup; the rail-less band places the
    # rail's halves either side of the article; and both ends of the chrome
    # cancellation, which is the only place that arithmetic can live -- the
    # alternative is writing `scrollTop` from script, which halts a fling.
    "web/src/views/GraphView.vue": (
        20,
        "three-column grid whose rail tracks animate",
    ),
}

# `p-[7px]`, `h-[460px]`, `text-[14px]` -- an arbitrary value is the escape hatch
# out of the scale. A var reference (`h-(--control-h)`,
# `w-[calc(var(--rail-margin)-var(--space-3))]`) is not one: it resolves through
# a token and remaps with it.
ARBITRARY_DIM = re.compile(
    r"\b(?:[mp][xytrbl]?|gap(?:-[xy])?|w|h|min-w|min-h|max-w|max-h|size|text|leading|"
    r"rounded(?:-[a-z]+)?|top|right|bottom|left|inset|basis|space-[xy])-\[([^\]]+)\]"
)
# Only absolute lengths are scale values. A percentage or a viewport unit is a
# RELATIONSHIP (a circle, a scrim inset, a modal capped against the window), and
# a unitless number is a line-height. None of them has a rung to snap to.
DIM_LITERAL = re.compile(r"^-?[0-9.]+(px|rem|em)$")


# ══ The type half of C5 ══════════════════════════════════════════════
#
# `theme.css` closes the scale, so a bad SIZE cannot be written -- but a bad
# FAMILY can, because a family is a string and every layer of the stack will
# happily accept one it does not have. A missing typeface never errors: it
# renders, in something else, and looks deliberate.
#
# Paid for twice. `--font-ui` named `Inter var` and `--font-prose` named
# `Charter`; neither was ever bundled, so for the life of the project the app
# rendered in Liberation Sans and the reading column in a Times clone. Then the
# extension: `options.css` asked for `'JetBrains Mono'` (the static family, not
# the `Variable` one the package declares) behind a `ui-monospace` that won
# anyway, and `src/shared/fonts.ts` did not exist, so the card and the settings
# page rendered in the platform's mono.
#
# So the rule is the strictest one still true: the FAMILY is named in tokens.css
# and nowhere else.

# The value is captured so the message can quote what was actually written.
FONT_FAMILY_DECL = re.compile(r"font-family\s*:\s*([^;}\n]+)")
FONT_SHORTHAND = re.compile(r"(?<![\w-])font\s*:\s*([^;}\n]+)")
FONT_FAMILY_OK = re.compile(r"^var\(\s*--font-(ui|mono|prose)\s*\)$")

# NAMED families only. A generic keyword (`monospace`, `sans-serif`, `system-ui`)
# carries no design decision -- it means "whatever this platform has" -- and is
# the correct last resort inside the stacks tokens.css owns, as well as the
# correct fallback for the one seam that cannot read a CSS variable
# (`useCytoscape.ts` passes a family string to Cytoscape's canvas renderer). A
# NAME is what can be wrong, unbundled, or a stale duplicate of the token layer.
#
# `/` is in both boundaries so a PATH segment is not a family: the app's entry
# imports `@fontsource-variable/inter`, which is the correct way to get the
# typeface and the exact opposite of naming it in a stylesheet.
NAMED_FAMILY = re.compile(
    r"(?i)(?<![\w\-/])(Inter|JetBrains|Charter|Roboto|Arial|Helvetica|Menlo|Consolas|"
    r"SFMono|Segoe|Georgia|Cambria|Times New Roman|Source Serif|Liberation)(?![\w\-/])"
)

# 11px is the floor, and `--text-2xs` is the rung that sits on it. Below that,
# fixed pitch on a dark ground stops being readable at arm's length -- and this
# interface is mostly 11-13px text, so the floor is load-bearing rather than
# decorative.
MIN_FONT_PX = 11
FONT_SIZE_PX = re.compile(r"font-size\s*:\s*(-?[0-9.]+)px")
FONT_SIZE_UTILITY = re.compile(r"\btext-\[(-?[0-9.]+)px\]")
# `font-600` reads like a weight and is not one. The theme resets
# `--font-weight-*` and declares only the named weights, so Tailwind never
# generates a numeric `font-N` -- the class is inert and the text renders at the
# inherited weight, which looks like a design decision rather than a typo.
FONT_WEIGHT_NUMERIC = re.compile(r"\bfont-\d+\b")


def check_type(path: Path, rel: object, lineno: int, line: str, violations: list[str]) -> None:
    for match in FONT_FAMILY_DECL.finditer(line):
        value = match.group(1).strip().rstrip(",")
        if value != "inherit" and not FONT_FAMILY_OK.match(value):
            violations.append(
                f"{rel}:{lineno}: font-family {value!r} -- the family is named in "
                f"styles/tokens.css and nowhere else. Use var(--font-ui), "
                f"var(--font-mono) or var(--font-prose), or the font-ui / font-mono / "
                f"font-prose utility in a template"
            )

    # CSS only. `font:` is a CSS shorthand, but `font: 'ui'` is equally an
    # ordinary object key, and nothing tells them apart inside a `.vue` or `.ts`.
    # `Input.vue`'s family prop is named `family` for exactly this reason.
    for match in FONT_SHORTHAND.finditer(line if path.suffix == ".css" else ""):
        value = match.group(1).strip()
        if value != "inherit":
            violations.append(
                f"{rel}:{lineno}: `font:` shorthand {value!r} -- it sets the family, so "
                f"it takes the family out of the token layer. Set font-family, font-size "
                f"and line-height separately from tokens (`font: inherit` is fine)"
            )

    if match := NAMED_FAMILY.search(line):
        violations.append(
            f"{rel}:{lineno}: typeface named directly ({match.group(0)!r}) -- only "
            f"styles/tokens.css may name a family. A name that is not bundled does not "
            f"error, it silently renders in something else"
        )

    for match in (*FONT_SIZE_PX.finditer(line), *FONT_SIZE_UTILITY.finditer(line)):
        if float(match.group(1)) < MIN_FONT_PX:
            violations.append(
                f"{rel}:{lineno}: {match.group(0)!r} is below the {MIN_FONT_PX}px floor "
                f"-- the scale starts at --text-2xs and does not go under it"
            )

    # The whole class of "the utility does not exist" bug is only visible by
    # diffing every source class against the built CSS; that is the escalation if
    # it recurs in another shape. This is the one shape it has taken.
    if match := FONT_WEIGHT_NUMERIC.search(line):
        violations.append(
            f"{rel}:{lineno}: {match.group(0)!r} is not a utility -- the theme declares "
            f"named weights only, so this generates no CSS and renders at the inherited "
            f"weight. Use font-medium / font-semibold / font-bold (not font-[600], which "
            f"steps outside the theme)"
        )


def check_style_blocks(violations: list[str]) -> None:
    for path in sorted(pathlib.Path("web/src").rglob("*.vue")):
        rel = str(path).replace("\\", "/")
        text = path.read_text()
        if "<style" not in text:
            continue
        if rel not in STYLE_ALLOWLIST:
            violations.append(
                f"{rel}: has a <style> block. Component styling is utilities now; "
                f"if this file genuinely needs CSS, add it to STYLE_ALLOWLIST with a reason"
            )
            continue
        budget, reason = STYLE_ALLOWLIST[rel]
        match = re.search(r"<style[^>]*>(.*?)</style>", text, re.S)
        size = len(match.group(1).strip().splitlines()) if match else 0
        if size > budget:
            violations.append(
                f"{rel}: <style> block is {size} lines, over its budget of {budget}. "
                f"It is allowlisted for '{reason}' -- anything beyond that belongs in "
                f"utilities. Raise the budget here only if the block's REASON grew."
            )


# CLAUDE.md: "Never add a second implementation of a `components/ui/` primitive."
# `Button.vue`'s docstring counts what that rule is worth; the recipe regenerated
# anyway, because a rule with nothing behind it is a suggestion.
#
# An `<a>` is deliberately NOT caught: there is no Link primitive to duplicate,
# and `LoginView`'s Google control has to be a real navigation because
# /api/auth/login answers 302 to accounts.google.com.
#
# Every entry below is about SHAPE rather than convenience, and the reason IS the
# entry -- an allowlist entry outlives its reason silently. A `Button` is a
# control: `--control-h` tall, its own padding, `justify-center`, uppercase, a
# text baseline. These are clickable ROWS and overlay chrome, full-bleed, laying
# out their own children and sized by what they sit in, so routing them through
# the primitive means overriding four of its properties at the call site --
# precisely the divergence the primitive exists to prevent.
BUTTON_ALLOWLIST = {
    # A numbered contents entry on a 2-column grid with an age badge in the
    # gutter. NOT a missing `ListRail`: that exists and owns the rail itself --
    # the aside, its header, its collapse-to-a-stub. The ROW inside it is what is
    # hand-rolled, and `docs/pm/NOT-DOING.md` carries the decision. Trigger: a
    # SECOND row of this shape. Two is a primitive; one is this.
    "web/src/views/BriefView.vue": "v-for contents rows, not controls",
    # The palette's pinned "search everything" row: it spans the dialog, carries
    # a label and a right-aligned count, and sits on a top hairline. Trigger: a
    # SECOND full-bleed overlay row anywhere.
    "web/src/components/nav/CommandPalette.vue": "pinned full-bleed overlay row, not a control",
    # The word `survivor` / `merges away` in a merge column's corner, which is
    # what actually chooses the side -- so it must be keyboard-reachable and
    # announce `aria-pressed` while looking like the label it sits among. The
    # column around it is a plain div because it carries the card's source links,
    # and an anchor nested in a control follows its href AND flips the survivor.
    "web/src/components/curation/MergeReview.vue": "per-column survivor tag, styled as a label",
    # GraphView is deliberately absent. An entry reading "canvas overlay chrome;
    # tied to the allowlisted <style> block" outlived that block shrinking to the
    # single `.grid` rule, and left the readout's `show` and `reset` and the
    # full-screen escape hatch as three hand-styled buttons tied to nothing but a
    # stale comment -- three of the four controls a reader sees on that page,
    # visibly diverged from the `Button` in the bar. All three are `Button`.
}


INPUT_ALLOWLIST = {
    # Same rule as BUTTON_ALLOWLIST, and DELIBERATELY NOT a `Textarea` primitive:
    # `Input` is a control -- `--control-h` tall, `px-2`, one border, one type
    # scale -- and every entry here is a multi-paragraph prose field or overlay
    # chrome. `Button.vue` justifies a primitive on a count of diverged copies;
    # these are not that. TRIGGER: two sites converging on one shape.
    "web/src/views/GraphView.vue": "a range slider shares no metric with a text field",
    "web/src/views/RequestAccessView.vue": (
        "a 3-row `font-prose` paragraph field; the email input above it IS on Input"
    ),
    "web/src/views/SetupView.vue": (
        "multi-paragraph persona/priorities/seed fields, RequestAccessView's shape; "
        "the one-line fields above them ARE on Input"
    ),
    "web/src/components/nav/CommandPalette.vue": (
        "transparent, border-bottom only, `text-md`, and a role=combobox with "
        "aria-expanded/aria-controls wiring -- overlay chrome, not a bar control"
    ),
    "web/src/components/entity/EntityHoverCard.vue": (
        "`font-[inherit]` on purpose: this card is compiled into the extension's "
        "content script and rendered inside a shadow root, where the app's font "
        "tokens are on `:host` rather than `:root`"
    ),
}


def check_raw_inputs(violations: list[str]) -> None:
    """`<input>`/`<textarea>` outside `components/ui/`.

    Buttons got this rule and converged; inputs had none and drifted. `Input.vue`
    tabulates where they had reached: five fields all meaning "a boxed field in a
    control bar", written five different ways, one of them sharing no structural
    class at all -- not even `h-control`, in a bar where `tokens.css` says every
    control shares one height.
    """
    for path in sorted(pathlib.Path("web/src").rglob("*.vue")):
        rel = str(path).replace("\\", "/")
        if rel.startswith("web/src/components/ui/") or rel in INPUT_ALLOWLIST:
            continue
        in_template = False
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if line.startswith("<template"):
                in_template = True
                continue
            if line.startswith("</template>"):
                in_template = False
                continue
            if not in_template or ALLOW.search(line):
                continue
            if "<input" not in line and "<textarea" not in line:
                continue
            violations.append(
                f"{rel}:{lineno}: raw <input>/<textarea>. Use "
                f"`components/ui/Input.vue` -- five hand-rolled fields had already "
                f"diverged on font, width, height and padding before anything noticed "
                f"(CLAUDE.md)"
            )


def check_raw_buttons(violations: list[str]) -> None:
    for path in sorted(pathlib.Path("web/src").rglob("*.vue")):
        rel = str(path).replace("\\", "/")
        if rel.startswith("web/src/components/ui/") or rel in BUTTON_ALLOWLIST:
            continue
        # Only the TEMPLATE: a grep over the whole file flagged `LoginView.vue`'s
        # docstring about its Google control as the violation it explains.
        in_template = False
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if line.startswith("<template"):
                in_template = True
                continue
            if line.startswith("</template>"):
                in_template = False
                continue
            if not in_template or ALLOW.search(line) or "<button" not in line:
                continue
            violations.append(
                f"{rel}:{lineno}: raw <button>. Use `components/ui/Button.vue` or "
                f"`IconButton.vue` -- four states each is exactly where drift "
                f"regenerates (CLAUDE.md)"
            )


#: Components that take their content as a SLOT, and the prop each is mistaken
#: for taking. Vue passes an undeclared attribute through to the root element, so
#: the mistake is silent twice over -- `vue-tsc` accepts the fallthrough, the
#: browser accepts an unknown attribute -- and `<IconButton icon="down">` drew a
#: correctly sized, correctly positioned, entirely empty square. Only worth
#: checking where the component renders NOTHING without its slot; `Button` really
#: does take `icon`, which is why the wrong one is easy to reach for.
SLOT_ONLY = {"IconButton": "icon"}


def check_slot_only_props(violations: list[str]) -> None:
    """A slot-only component handed the prop its neighbour takes."""
    for path in sorted(pathlib.Path("web/src").rglob("*.vue")):
        rel = str(path).replace("\\", "/")
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if ALLOW.search(line):
                continue
            for component, prop in SLOT_ONLY.items():
                if f"<{component}" not in line:
                    continue
                # The single-line form; the scan below takes the multi-line one.
                if re.search(rf"\b{prop}=", line):
                    violations.append(
                        f"{rel}:{lineno}: <{component}> takes a slot, not `{prop}=`. "
                        f"An undeclared attribute falls through to the element and "
                        f"renders nothing; put an <Icon> inside instead"
                    )


def check_slot_only_blocks(violations: list[str]) -> None:
    """The multi-line form of the above: `<IconButton` ... `prop=` ... `>`."""
    for path in sorted(pathlib.Path("web/src").rglob("*.vue")):
        rel = str(path).replace("\\", "/")
        lines = path.read_text().splitlines()
        for lineno, line in enumerate(lines, start=1):
            for component, prop in SLOT_ONLY.items():
                if f"<{component}" not in line or ">" in line:
                    continue
                for offset, tail in enumerate(lines[lineno : lineno + 12]):
                    if re.search(rf"^\s*:?{prop}=", tail) and not ALLOW.search(tail):
                        violations.append(
                            f"{rel}:{lineno + offset + 1}: <{component}> takes a slot, "
                            f"not `{prop}=`. An undeclared attribute falls through to "
                            f"the element and renders nothing; put an <Icon> inside"
                        )
                    if ">" in tail:
                        break


def check_numeric_input_bindings(violations: list[str]) -> None:
    """A `type="number"` field bound to a ref that was initialised as a string.

    `Input` emits `el.valueAsNumber` for a numeric field: a NUMBER as soon as
    anybody types, `NaN` as soon as they clear it. A ref declared `ref('')` holds
    a string exactly until the field is used, so code treating it as one
    (`.trim()` is the obvious reach) throws on the first real input, and
    `vue-tsc` misses it because `v-model`'s write-back across a component
    boundary is not checked against the ref's inferred type. It cost a submit
    button that threw inside its own click handler, surfaced as "something on
    this page failed to render", and sent no request at all.
    """
    for path in sorted(pathlib.Path("web/src").rglob("*.vue")):
        rel = str(path).replace("\\", "/")
        if rel.endswith("components/ui/Input.vue"):
            continue
        text = path.read_text()
        for match in re.finditer(r"<Input\b[^>]*?>", text, re.S):
            tag = match.group(0)
            if 'type="number"' not in tag:
                continue
            bound = re.search(r'v-model="([A-Za-z_$][\w$]*)"', tag)
            if not bound:
                continue
            name = bound.group(1)
            decl = re.search(rf"\bconst\s+{re.escape(name)}\s*=\s*ref\s*\(\s*['\"`]", text)
            if decl and not ALLOW.search(text[decl.start() : decl.end() + 40]):
                lineno = text[: decl.start()].count("\n") + 1
                violations.append(
                    f'{rel}:{lineno}: `{name}` is bound to a type="number" Input but '
                    f"initialised as a string. That field emits a Number, and NaN when "
                    f"cleared; initialise it as a number and coerce on read"
                )


def check_arbitrary_dimensions(
    path, rel: str, lineno: int, line: str, violations: list[str]
) -> None:
    if ALLOW.search(line):
        return
    for value in ARBITRARY_DIM.findall(line):
        if "var(" in value or "calc(" in value:
            continue  # a token reference, not a literal
        if DIM_LITERAL.match(value):
            violations.append(
                f"{rel}:{lineno}: arbitrary dimension [{value}] "
                f"-- use a scale rung, or add a token to tokens.css and name it in theme.css"
            )


def check_menu_items_keep_their_names(violations: list[str]) -> None:
    """A menu item that collapses to its glyph on a phone.

    `Button` treats `icon` PLUS `label` as "toolbar control, and on a narrow
    screen the glyph is enough": it wraps the slot in `max-sm:hidden` and leans
    on the tooltip. Right in a bar, where the neighbours give the glyph its
    context and there is no room for seven words.

    In a menu the words are the whole content. Two items in the Runs menu carried
    both, so on a phone the panel listed five named runs and then two bare
    glyphs -- two of the most expensive actions in the app, unlabelled, on the
    device with no hover to recover the tooltip. The fix at the call site is to
    drop `label` and leave the words in the slot, which is the accessible name.
    """
    for path in sorted(pathlib.Path("web/src").rglob("*.vue")):
        rel = str(path).replace("\\", "/")
        lines = path.read_text().splitlines()
        for lineno, line in enumerate(lines, start=1):
            if "<Button" not in line or ALLOW.search(line):
                continue
            block = [line]
            for tail in lines[lineno : lineno + 16]:
                block.append(tail)
                if ">" in tail:
                    break
            text = "\n".join(block)
            if 'role="menuitem"' not in text:
                continue
            if re.search(r"^\s*:?icon=", text, re.M) and re.search(r"^\s*:?label=", text, re.M):
                violations.append(
                    f'{rel}:{lineno}: a <Button role="menuitem"> sets both `icon` and '
                    f"`label`, which collapses its words to the glyph below `sm`. A menu "
                    f"item is its name; drop `label` and leave the words in the slot"
                )


if __name__ == "__main__":
    sys.exit(main())
