"""Which build of the hub this process is.

The instance half of this lives in `venona.release` and reports the same three
facts through `GET /api/mgmt/status`; the hub's own has nowhere to be reported
but its operator page, which is the point -- three properties deploy from one
branch on separate filters, so the hub is as capable of being a release behind
as any domain it polls.

No em or en dashes in any string literal here: `tests/test_copy_style.py`
sweeps every top-level module of this package.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime


def _release() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("venona-hub")
    except PackageNotFoundError:  # pragma: no cover - a source checkout, not a wheel
        return "unknown"


def _commit() -> str | None:
    """The short sha, or None off a build host.

    `HUB_BUILD_COMMIT` first so the production path can be exercised before a
    deploy; `RENDER_GIT_COMMIT` is what production actually sets.
    """
    raw = os.environ.get("HUB_BUILD_COMMIT") or os.environ.get("RENDER_GIT_COMMIT")
    return raw.strip()[:7] if raw and raw.strip() else None


RELEASE = _release()
COMMIT = _commit()
STARTED_AT = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
