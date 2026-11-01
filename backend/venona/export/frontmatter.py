"""YAML properties, written by hand.

NOT PyYAML, and the reasons are all load-bearing rather than stylistic.

  * It sorts keys, so the order a note declares its properties in is lost, and
    the order is the only thing making a note readable as prose.
  * It line-wraps at eighty columns, which BREAKS A QUOTED WIKILINK across two
    lines -- and Obsidian then stops parsing the property entirely, so a
    relationship silently vanishes from a note that still looks right.
  * It writes `null` for an absent value, which Obsidian renders as a property
    that exists and is empty rather than one that was never claimed.
  * A golden test needs byte-stable output and PyYAML's is a function of its
    own version.

Obsidian's own rules are enforced here rather than trusted to the caller:
a property name is unique per note (a dict gives that), values are scalars or
flat lists of them, and NOTHING NESTS. A dict value raises rather than being
flattened, because a nested property does not fail -- it renders as a string
that looks like YAML, which is the kind of wrong nobody reports.
"""

from __future__ import annotations

import re

#: What Obsidian reads as a date rather than as text. Left unquoted so the
#: property gets its Date type; quoting it makes the same value plain text.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: A bare scalar is only safe when it could not be read as anything else. This
#: is deliberately narrow: the cost of quoting something that did not need it is
#: nothing, and the cost of not quoting something that did is a broken note.
_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]*")

Value = str | int | float | bool | None | list[str]


def _scalar(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if _ISO_DATE.fullmatch(text):
        return text
    if _PLAIN.fullmatch(text) and text.strip(" ") == text:
        return text
    # Double quotes, so a wikilink's brackets survive and an apostrophe in a
    # name does not have to be doubled. Backslashes and quotes are the only two
    # things that mean anything inside them.
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render(properties: dict[str, Value]) -> str:
    """The `---` block for one note, in the order given.

    An empty value is DROPPED rather than written empty. A note with no short
    forms should have no `aliases` property at all, not one holding nothing --
    Obsidian shows the second as a property the author meant to fill in.
    """
    lines: list[str] = []
    for key, value in properties.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, dict):
            raise TypeError(
                f"property {key!r} is a mapping. Obsidian has no nested properties, and "
                f"one written anyway renders as a string that looks like YAML."
            )
        if isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            # A link inside a property MUST be quoted -- unquoted, the leading
            # bracket starts a YAML flow sequence and the parse fails. `_scalar`
            # quotes it because a bracket is not in `_PLAIN`.
            lines.extend(f"  - {_scalar(item)}" for item in value)
            continue
        lines.append(f"{key}: {_scalar(value)}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return f"---\n{body}\n---\n"
