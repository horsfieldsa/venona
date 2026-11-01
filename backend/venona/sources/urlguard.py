"""Which addresses venona will dial, re-exported from the shared notice core.

THE RULES LIVE IN ONE PLACE BECAUSE BOTH SERVICES ENFORCE THEM. The instance
checks a feed URL somebody typed into Admin and a webhook a reader pasted into
their own settings; the hub checks a webhook an operator pasted into its
console. Two copies of an SSRF guard is two guards that drift, and the drift is
not a duplication bug, it is a hole -- so the predicates live in `notices` and
this module is the name the rest of `sources/` already knows them by.

`notices` carries no dependency beyond the standard library, which is what lets
the hub import it without taking on this package's tree.
"""

from __future__ import annotations

from notices.urlguard import (
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    BlockedUrl,
    check_resolved,
    check_static,
    is_https,
)

__all__ = [
    "ALLOWED_PORTS",
    "ALLOWED_SCHEMES",
    "BlockedUrl",
    "check_resolved",
    "check_static",
    "is_https",
]
