"""One rule for every URL that reaches a browser as an `href`.

A card's `url` is not typed by the person reading it. It arrives from a
document the crawler fetched, from a grounding a model returned, or from a
curator's paste box -- and the client renders it as a link. `javascript:` and
`data:` URLs in that position execute in the reader's session, so the scheme is
checked here, once, rather than at each of the places a link is drawn.

TWO TYPES, because a request and a response want opposite failure modes:

* `WebUrl` REFUSES, and is for a request body. The caller is a person who can
  be told what is wrong, and 422 is the honest answer.
* `SafeWebUrl` DROPS to None, and is for a response body. A response model
  that raises turns one bad row in the corpus into a 500 for the whole card
  batch -- so the link disappears and the rest of the card still renders.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator

# A browser strips control characters and whitespace before it parses a URL, so
# `java\nscript:alert(1)` is `javascript:alert(1)` by the time it runs. Any of
# them present means the value is not what it appears to be, whatever it parses
# to here.
_CONTROL = re.compile(r"[\x00-\x20\x7f]")

_ALLOWED = ("http", "https")


def is_web_url(value: str) -> bool:
    """True for an absolute `http`/`https` URL and nothing else.

    Scheme-relative (`//host/path`) is refused along with the rest. It is
    harmless in a browser, but nothing in this app produces one, so its
    appearance means something upstream is wrong rather than unusual.
    """
    if _CONTROL.search(value):
        return False
    scheme, sep, rest = value.partition(":")
    return bool(sep) and scheme.lower() in _ALLOWED and rest.startswith("//")


def _refuse(value: str | None) -> str | None:
    if value is None:
        return None
    if not is_web_url(value):
        raise ValueError("must be an http:// or https:// URL")
    return value


def _drop(value: str | None) -> str | None:
    return value if value is not None and is_web_url(value) else None


#: A URL in a REQUEST body. Refuses anything that is not http(s).
WebUrl = Annotated[str, AfterValidator(_refuse)]

#: An optional URL in a REQUEST body.
OptionalWebUrl = Annotated[str | None, AfterValidator(_refuse)]

#: A URL in a RESPONSE body. Anything that is not http(s) renders as absent.
SafeWebUrl = Annotated[str | None, AfterValidator(_drop)]
