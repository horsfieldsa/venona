"""Line surgery on `settings.yaml`, so a configuration update keeps the prose.

`yaml.safe_load` then `yaml.safe_dump` would hand back a file with every
comment deleted and every block reordered. That file is where each of the 57
knobs explains what it buys and what it costs, and the app tells the operator it
is theirs to edit, so a round trip through the parser is a silent total loss of
the document's whole value. `config_service.add_to_stoplist` reached the same
conclusion for `lexicon.yaml` and took the same way out.

Nothing here parses YAML. It reads and rewrites lines, and the caller checks the
result against a parsed shadow before anything is written -- see
`services/config_migrations`. That pairing is what makes text surgery safe
enough to run unattended at boot: a splice that ate a neighbour shows up as a
value mismatch rather than as a config file nobody looks at until something is
wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TOP_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")
INDENTED_KEY = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_]*)\s*:")
DEFAULT_INDENT = 2


class ConfigTextError(ValueError):
    """The document holds something this module refuses to guess at."""


@dataclass(frozen=True)
class KeySpan:
    """Where one key lives, in line indices into the document.

    `comment_start` is where the key's own explanation begins, `key` is its
    `name:` line, and `value_end` is the last line the value occupies. The three
    together are the unit that moves: a key lifted without its comment arrives
    somewhere else unexplained.
    """

    comment_start: int
    key: int
    value_end: int


def check(text: str) -> None:
    """Refuse a document with a shape this module would have to guess about.

    Every one of these is legal YAML that line surgery cannot follow: a second
    document has a second set of keys, an anchor's value lives somewhere else
    entirely, and a duplicated key means an edit has two candidate targets and
    no way to choose. None appears in any instance file. Stopping is the only
    honest answer, and it stops before anything is written.
    """
    lines = text.splitlines()
    for n, line in enumerate(lines, start=1):
        if line.startswith(("---", "...")):
            raise ConfigTextError(f"line {n}: a multi-document marker; refusing to edit")
        stripped = line.split("#", 1)[0]
        if re.search(r"(?:^|\s)[&*][A-Za-z_]", stripped):
            raise ConfigTextError(f"line {n}: a YAML anchor or alias; refusing to edit")

    seen_top: set[str] = set()
    for start, end, name in _groups(lines):
        if name in seen_top:
            raise ConfigTextError(f"duplicate top-level key {name!r}; refusing to edit")
        seen_top.add(name)
        seen: set[str] = set()
        indent = child_indent(lines, start, end)
        for i in range(start + 1, end):
            match = INDENTED_KEY.match(lines[i])
            if match and len(match.group(1)) == indent:
                child = match.group(2)
                if child in seen:
                    raise ConfigTextError(f"duplicate key {name}.{child}; refusing to edit")
                seen.add(child)


def _groups(lines: list[str]):
    starts = [i for i, line in enumerate(lines) if TOP_KEY.match(line)]
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        yield i, end, TOP_KEY.match(lines[i]).group(1)  # type: ignore[union-attr]


def group_span(lines: list[str], group: str) -> tuple[int, int] | None:
    """The half-open line range a top-level group occupies, its own key line
    included. A group runs until the next key at column zero, so its trailing
    blank lines and comments belong to it."""
    for start, end, name in _groups(lines):
        if name == group:
            return start, end
    return None


def child_indent(lines: list[str], start: int, end: int) -> int:
    """Read the group's indent off its first child rather than assuming two.

    A file indented four spaces stays indented four spaces after an update.
    """
    for i in range(start + 1, end):
        match = INDENTED_KEY.match(lines[i])
        if match:
            return len(match.group(1))
    return DEFAULT_INDENT


def key_span(lines: list[str], group: str, key: str) -> KeySpan | None:
    span = group_span(lines, group)
    if span is None:
        return None
    start, end = span
    indent = child_indent(lines, start, end)
    pattern = re.compile(rf"^{' ' * indent}{re.escape(key)}\s*:")
    for i in range(start + 1, end):
        if pattern.match(lines[i]):
            return KeySpan(
                comment_start=_comment_start(lines, i, floor=start + 1),
                key=i,
                value_end=_value_end(lines, i, end, indent),
            )
    return None


def _comment_start(lines: list[str], key_line: int, *, floor: int) -> int:
    """Walk back over the comment block sitting directly above a key.

    A BLANK LINE ENDS OWNERSHIP, and that rule is what makes the real document
    work. `outage_wait_seconds`' explanation sits between two keys with no blank
    line above it, so it belongs to the key below it rather than to the one
    above, and moving `max_calls_per_run` cannot carry it away. The blank line
    before `# Opus for judgment-heavy...` is what hands that block to
    `discover_model` instead.
    """
    i = key_line
    while i > floor and lines[i - 1].lstrip().startswith("#"):
        i -= 1
    return i


def _value_end(lines: list[str], key_line: int, end: int, indent: int) -> int:
    """The last line of a key's value.

    A value continues through blank lines, anything indented deeper than the
    key, and sequence items at the key's own indent, which is legal YAML and the
    one genuinely ambiguous shape. A comment at the key's indent is not deeper,
    so it ends the value and stays with whatever follows it; a `#` inside a
    block scalar is deeper, so it stays content.
    """
    i = key_line
    while i + 1 < end:
        nxt = lines[i + 1]
        deeper = nxt.strip() and len(nxt) - len(nxt.lstrip()) > indent
        sequence = re.match(rf"^{' ' * indent}-\s", nxt)
        if not (not nxt.strip() or deeper or sequence):
            break
        i += 1
    while i > key_line and not lines[i].strip():
        i -= 1
    return i


def has_key(text: str, group: str, key: str) -> bool:
    return key_span(text.splitlines(), group, key) is not None


def extract_block(text: str, group: str, key: str) -> list[str]:
    """One key's comment, its name and its value, verbatim, ready to insert
    somewhere else. The lines are taken as they are, so wording and blank lines
    inside the block survive the move."""
    lines = text.splitlines()
    span = key_span(lines, group, key)
    if span is None:
        raise ConfigTextError(f"{group}.{key} is not in this document")
    return lines[span.comment_start : span.value_end + 1]


def extract_group(text: str, group: str) -> list[str]:
    """A whole group, verbatim, without the blank lines that trail it."""
    lines = text.splitlines()
    span = group_span(lines, group)
    if span is None:
        raise ConfigTextError(f"{group} is not a group in this document")
    start, end = span
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def has_group(text: str, group: str) -> bool:
    return group_span(text.splitlines(), group) is not None


def insert_key(text: str, group: str, block: list[str]) -> str:
    """Add a key at the END of its group.

    End of group, rather than the position it holds in the shipped file, for
    three reasons: matching position would need the shipped file at runtime,
    mapping order carries no meaning in YAML, and appending can never land
    between a key and the comment explaining it. The cost is that an instance's
    key order drifts from the shipped file's over time, which costs a reader
    nothing and an update everything if we got it wrong.
    """
    lines = text.splitlines()
    span = group_span(lines, group)
    if span is None:
        raise ConfigTextError(f"{group} is not a group in this document")
    start, end = span
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    return "\n".join(lines[:at] + _reindent(block, child_indent(lines, start, end)) + lines[at:])


def add_group(text: str, block: list[str]) -> str:
    """Append a whole group, with a blank line before it."""
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join([*lines, "", *block]) if lines else "\n".join(block)


def _reindent(block: list[str], indent: int) -> list[str]:
    """Blocks are written at two spaces; a document indented otherwise keeps
    its own."""
    if indent == DEFAULT_INDENT:
        return list(block)
    shift = " " * (indent - DEFAULT_INDENT)
    return [shift + line if line.strip() else line for line in block]


def set_scalar(text: str, group: str, key: str, rendered: str) -> str:
    """Rewrite one key's value, keeping any comment that trails it on the line.

    `rendered` is already YAML, so the caller decides quoting. Scalars only: a
    value spanning lines is a shape this cannot rewrite in place, and it says so
    rather than truncating one.
    """
    lines = text.splitlines()
    span = key_span(lines, group, key)
    if span is None:
        raise ConfigTextError(f"{group}.{key} is not in this document")
    if span.value_end != span.key:
        raise ConfigTextError(f"{group}.{key} spans several lines; refusing to rewrite it")
    head, _, rest = lines[span.key].partition(":")
    trailing = rest.split("#", 1)
    comment = f"  #{trailing[1]}" if len(trailing) > 1 else ""
    lines[span.key] = f"{head}: {rendered}{comment}"
    return "\n".join(lines)


VERSION_KEY = "settings_version"
VERSION_NOTE = (
    "# Managed by Venona. It records which configuration updates this instance has",
    "# already received, so a new release can add a setting without changing a value",
    "# you chose. Leave this line as it is.",
)


def read_version(text: str) -> int:
    """How far this document has been brought forward. An absent marker reads
    as zero, so a hand-written file is simply brought up to date."""
    for line in text.splitlines():
        match = re.match(rf"^{VERSION_KEY}\s*:\s*(\d+)", line)
        if match:
            return int(match.group(1))
    return 0


def stamp_version(text: str, version: int) -> str:
    """Record the version, rewriting the marker or introducing it below the
    document's own opening comment."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"^{VERSION_KEY}\s*:", line):
            lines[i] = f"{VERSION_KEY}: {version}"
            return "\n".join(lines)
    at = 0
    while at < len(lines) and (lines[at].startswith("#") or not lines[at].strip()):
        at += 1
    block = [*VERSION_NOTE, f"{VERSION_KEY}: {version}", ""]
    return "\n".join(lines[:at] + block + lines[at:])
