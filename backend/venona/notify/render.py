"""A notice in the words each channel can carry, re-exported from `notices`.

The rendering is shared with the hub, because the rule it exists to enforce is:
`Notice.quoted` holds text Venona did not write and every channel with markup
has to escape it for that markup. Two copies of that would be two places to
remember it, and the one nobody updated is the injection.
"""

from __future__ import annotations

from notices.render import QUOTE_CHARS, TITLE_CHARS, as_email, as_slack, as_text, title_header

__all__ = [
    "QUOTE_CHARS",
    "TITLE_CHARS",
    "as_email",
    "as_slack",
    "as_text",
    "title_header",
]
