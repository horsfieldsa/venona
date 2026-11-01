"""Copy style is enforced, not remembered (operator rules, 2026-08-24).

Two rules. Em and en dashes are banned from user-facing copy: dash-spliced
sentences read as machine-written, so copy recasts them with a period, a
comma, a semicolon or a word; the one exception is the empty-cell
placeholder, where a standalone `'—'` is typography, not prose. And the
brand word is "Venona", capital V, in every sentence; lowercase survives
only in literals a user must type or a machine must match (CLI commands,
domains, identifiers).

Comments are not user-facing and are exempt -- that is why this test strips
them instead of grepping files whole. Backend-served strings are in scope
wherever Python answers with a page, which `served_pages` derives rather
than lists; the other surfaces below are the ones a reader renders
directly.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DASHES = re.compile(r"[—–]")  # em, en

# A dash that IS the whole rendered value: quoted alone, or alone between
# tags. These are placeholders, not sentences.
PLACEHOLDER = re.compile(r"(['\"`])—\1|>\s*—\s*<")

# The brand word, bare: not part of a domain (venona.example.com), an identifier
# (venona_search, venona-lockup) or a path. A dot only disqualifies when a
# word character follows it: "venona.example" is a literal, "…runs venona." is a
# sentence.
BRAND_LOWER = re.compile(r"(?<![\w.:/@-])venona(?:s)?(?![\w:/-])(?!\.\w)")

# Venona is a company name and is never pluralized or counted: "your
# Venonas" shipped once and was rejected on sight. A deployment is a
# "domain".
BRAND_PLURAL = re.compile(r"\b[Vv]enonas\b")

# A lowercase brand word starting a CLI invocation is a literal the user
# must type, not copy.
CLI_AFTER = re.compile(
    r"\s+(?:graph|users|mcp|instance|anchors|auth|entity|run|research|seed"
    r"|conflicts|serve|access)\b|\s+--"
)

# Wire values that are legitimately lowercase, per file.
BRAND_EXEMPT = {
    # PORT_NAME: a chrome.runtime port identifier matched across extension
    # contexts; an old content script must still reach an updated worker.
    "extension/src/shared/protocol.ts",
}

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"//[^\n]*")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def stripped(text: str) -> str:
    # All three comment forms are stripped regardless of file type: a Vue SFC
    # mixes HTML and TS, and stripping a form a file cannot contain is a
    # no-op. Line-comment stripping can eat the tail of a line holding a
    # URL's `//`, which only ever hides a finding -- it never invents one.
    text = BLOCK_COMMENT.sub("", text)
    text = HTML_COMMENT.sub("", text)
    return LINE_COMMENT.sub("", text)


def sources() -> list[Path]:
    web = ROOT / "web" / "src"
    ext = ROOT / "extension" / "src"
    # The hub's operator app. Its sentences are read by a person the same way
    # the instance's admin screens are, and `hub/hub/*.py` has always been
    # swept for the server-rendered half -- so the half that moved into a
    # bundle has to be swept too, or the rules simply stopped applying to it.
    hub = ROOT / "hub" / "web" / "src"
    return [
        ROOT / "extension" / "options.html",
        ROOT / "web" / "index.html",
        # The in-app guides render at /guide, so they are user-facing copy the
        # same way a view's template is. Code fences are NOT stripped here;
        # a lowercase `venona` inside one passes only through CLI_AFTER.
        ROOT / "docs" / "USER-GUIDE.md",
        ROOT / "docs" / "ADMIN-GUIDE.md",
        *(
            p
            for base in (web, ext, hub)
            for pattern in ("*.vue", "*.ts")
            for p in base.rglob(pattern)
            if not p.name.endswith((".spec.ts", ".d.ts"))
        ),
    ]


def served_pages() -> list[Path]:
    """Every module whose strings reach a reader's browser, derived not listed.

    A hand-written list is how one gets missed, and two were: the access
    endpoint's no-script reply and the hub routers' sign-in error copy both
    render HTML from Python and both sat outside a list that named the hub
    package and the consent screen. A module that answers with a page imports
    `HTMLResponse`, so that import is the membership test, applied on top of the
    whole hub package rather than in place of it.
    """
    roots = (ROOT / "hub" / "hub", ROOT / "backend" / "venona")
    # The hub package's own modules stay in whole: it is small, every string in
    # it is written for a person, and its terminal output is read by an
    # administrator, who is a user too.
    out = sorted((ROOT / "hub" / "hub").glob("*.py"))
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or path in out:
                continue
            if "HTMLResponse" in path.read_text():
                out.append(path)
    return out


def served_strings() -> list[tuple[Path, str]]:
    # Comments and docstrings never reach the wire, so only the other string
    # constants are checked.
    paths = served_pages()
    out = []
    for path in paths:
        tree = ast.parse(path.read_text())
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                out.append((path, node.value))
    return out


def test_no_dashes_in_user_facing_copy() -> None:
    bad: dict[str, list[str]] = {}
    for path in sources():
        text = PLACEHOLDER.sub("", stripped(path.read_text()))
        lines = [line.strip() for line in text.splitlines() if DASHES.search(line)]
        if lines:
            bad[str(path.relative_to(ROOT))] = lines
    for path, value in served_strings():
        if DASHES.search(PLACEHOLDER.sub("", value)):
            bad.setdefault(str(path.relative_to(ROOT)), []).append(value.strip()[:80])
    detail = "\n".join(f"{f}: {ls[:3]}" for f, ls in sorted(bad.items()))
    assert not bad, f"em/en dash in user-facing copy (recast the sentence):\n{detail}"


def lowercase_brand(text: str) -> list[str]:
    hits = []
    for m in BRAND_LOWER.finditer(text):
        if CLI_AFTER.match(text[m.end() :]):
            continue
        line = text[: m.start()].count("\n")
        hits.append(text.splitlines()[line].strip()[:90])
    for m in BRAND_PLURAL.finditer(text):
        line = text[: m.start()].count("\n")
        hits.append(text.splitlines()[line].strip()[:90])
    return hits


#: The domain descriptor's own display strings. `config/` is user-editable and
#: deliberately outside `sources()`, but THIS copy of the file is the one that
#: ships, and `login_blurb` and `tagline` render on the login page, on the hub's
#: gallery beside a price, and in the status envelope every domain publishes
#: about itself. The lowercase brand sat in it for months for exactly this
#: reason: nothing looked at the file a reader reads from.
DESCRIPTOR_COPY = ("display_name", "tagline", "login_blurb")


def descriptor_strings() -> list[tuple[Path, str]]:
    import yaml

    path = ROOT / "config" / "domain.yaml"
    if not path.is_file():
        return []
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [(path, str(loaded[key])) for key in DESCRIPTOR_COPY if loaded.get(key)]


def test_brand_is_capitalized() -> None:
    bad: dict[str, list[str]] = {}
    for path in sources():
        rel = str(path.relative_to(ROOT))
        if rel in BRAND_EXEMPT:
            continue
        hits = lowercase_brand(stripped(path.read_text()))
        if hits:
            bad[rel] = hits
    for path, value in [*served_strings(), *descriptor_strings()]:
        if lowercase_brand(value):
            bad.setdefault(str(path.relative_to(ROOT)), []).append(value.strip()[:80])
    detail = "\n".join(f"{f}: {ls[:3]}" for f, ls in sorted(bad.items()))
    assert not bad, f'lowercase "venona" in copy (the name is Venona):\n{detail}'


# ── Run messages ─────────────────────────────────────────────────────
#
# `served_pages` cannot see these. Its membership test is `HTMLResponse in
# source`, which in `backend/venona` matches two auth routers and nothing
# else -- so every sentence a run says about itself, rendered verbatim as a
# list item on `/runs/:id`, sat outside every copy rule this file holds. It
# was the largest body of unchecked user-facing copy in the app, and it
# showed: five of them named a CLI command, and others carried `gazetteer`,
# `ambiguous_surfaces` and raw exception reprs.

#: `ctx.warn` / `ctx.todo` / `ctx.note`, and the stage-local lists that drain
#: into them. Matching on the ATTRIBUTE name rather than the receiver, because
#: `ingest.py` wraps them in closures and `target.ctx.warn` is the launcher's
#: spelling.
MESSAGE_CALLS = ("warn", "todo", "note")
MESSAGE_LISTS = ("warnings", "notices", "todos")

#: A command a reader is told to run. These render on a run page; a reader is
#: not at a shell, and the surfaces that can act are linked instead (`todo`'s
#: `to`). `venona graph rebuild` was the clearest case: the rebuild is not
#: even the reader's job.
CLI_IN_MESSAGE = re.compile(r"`?\bvenona\s+[a-z-]+")

#: Words this repo uses about itself. A reader has never heard any of them,
#: and an admin is a reader too (CLAUDE.md). The list is deliberately short:
#: it holds the ones that actually shipped, and grows when another does.
JARGON = (
    "gazetteer",
    "ambiguous_surfaces",
    "alias conflict",
    "cluster_id",
    "assertion",
    "projection",
    "surfaces deferred",
    "uncorroborated",
)


def message_strings() -> list[tuple[Path, str]]:
    """Every literal that can reach a run's message list.

    Static f-string parts count: `f"{surface}: needs a person"` renders that
    tail to a reader as surely as a plain constant does.
    """
    out: list[tuple[Path, str]] = []

    def literals(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.JoinedStr):
            return [v.value for v in node.values if isinstance(v, ast.Constant)]
        if isinstance(node, ast.BinOp):
            return literals(node.left) + literals(node.right)
        return []

    for path in sorted((ROOT / "backend" / "venona").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            attr = node.func.attr
            args = list(node.args)
            if attr in MESSAGE_CALLS:
                pass
            elif attr == "append" and isinstance(node.func.value, ast.Attribute):
                if node.func.value.attr not in MESSAGE_LISTS:
                    continue
            else:
                continue
            for arg in args:
                out.extend((path, text) for text in literals(arg) if text.strip())
    return out


def test_no_run_message_names_a_command() -> None:
    """A run tells a reader what happened, never what to type."""
    bad: dict[str, list[str]] = {}
    for path, value in message_strings():
        if CLI_IN_MESSAGE.search(value):
            bad.setdefault(str(path.relative_to(ROOT)), []).append(value.strip()[:90])
    detail = "\n".join(f"{f}: {ls}" for f, ls in sorted(bad.items()))
    assert not bad, (
        "a run message names a CLI command. Say what happened, and if there is "
        f"a surface that can act on it use `ctx.todo(..., to=...)`:\n{detail}"
    )


def test_no_run_message_uses_internal_vocabulary() -> None:
    bad: dict[str, list[str]] = {}
    for path, value in message_strings():
        hits = [w for w in JARGON if w in value.lower()]
        if hits:
            where = str(path.relative_to(ROOT))
            bad.setdefault(where, []).append(f"{hits} in {value.strip()[:70]}")
    detail = "\n".join(f"{f}: {ls}" for f, ls in sorted(bad.items()))
    assert not bad, f"internal vocabulary in a run message (say it in reader words):\n{detail}"


def test_no_dashes_in_run_messages() -> None:
    bad: dict[str, list[str]] = {}
    for path, value in message_strings():
        if DASHES.search(PLACEHOLDER.sub("", value)):
            bad.setdefault(str(path.relative_to(ROOT)), []).append(value.strip()[:90])
    detail = "\n".join(f"{f}: {ls}" for f, ls in sorted(bad.items()))
    assert not bad, f"em/en dash in a run message (recast the sentence):\n{detail}"
