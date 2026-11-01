"""What a notice IS, in the one shape every channel and both services agree on.

NAMED FIELDS, NEVER A SERIALISATION. A channel writes its own lines from these
and there is no `model_dump()` anywhere downstream, so a field added next month
for one channel's benefit cannot leak into another channel's output by default.

WHAT IS NOT HERE. The REGISTRY of notice types is each service's own: the
instance addresses two audiences and the hub addresses one operator, so a shared
`NoticeType` would carry a field that is a lie on one side. What has to be
shared is the shape a rendered notice takes, because that is what `render` and
the channels are written against.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: What a notice is raised with. Plain data, resolved by the sender.
Facts = Mapping[str, Any]


@dataclass(frozen=True)
class Notice:
    """One rendered occurrence, in named fields rather than a formatted string.

    Every channel writes its own lines from these, the way `api/mcp/render.py`
    reads named fields and never serialises a model. A single pre-formatted
    `body` would have forced plain text on Slack and Slack's shape on email, and
    it would have made the next rule unenforceable.

    `quoted` IS THE RULE. It holds text venona did not write: a stranger's
    coverage note from the access form, a headline out of the trade press. Today
    a plain-text POST neutralises that by accident; a Slack block or an HTML
    body would not. Keeping it in its own field is what lets every channel wrap
    and escape it without having to find it inside a sentence first.
    """

    #: One line. What happened. Rendered as the subject, and as ntfy's `Title`.
    title: str
    #: Venona's own words. One string per paragraph, trusted, never escaped as
    #: though it came from outside.
    body: tuple[str, ...] = ()
    #: Text venona did not write. Wrapped and escaped by every channel.
    quoted: str | None = None
    #: Where to go about it. A path, resolved against the instance's public URL
    #: by the channel, because only the channel knows whether a relative link is
    #: any use to the reader.
    link: str | None = None
    #: What to call the link. Required whenever `link` is set: a bare URL in an
    #: email reads as something to be suspicious of.
    link_label: str | None = None
