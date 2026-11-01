"""Which build of venona this process is.

TWO ANSWERS, because neither is sufficient alone. `RELEASE` is the number a
person says out loud, propagated from the repo's `VERSION` file into every
package manifest by `scripts/version.py` -- but between bumps it says nothing
about what is deployed, and this repo autodeploys from master. `COMMIT` is the
exact code, which is what tells two surfaces apart when they disagree.

`STARTED_AT` rather than a build timestamp: the host exposes no build time to a
Docker build, and an immutable-image deploy restarts the process, so process
start answers the question an operator is actually asking. Safe to stamp at
import -- uvicorn's reloader re-imports in the child, and this service runs one
worker (`tests/test_single_process_assumptions.py`).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime


def _release() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("venona")
    except PackageNotFoundError:  # pragma: no cover - a source checkout, not a wheel
        return "unknown"


def _commit() -> str | None:
    """The short sha, or None off a build host.

    `VENONA_BUILD_COMMIT` first so the production path can be exercised before
    a deploy; `RENDER_GIT_COMMIT` is what production actually sets. Read from
    the environment at import, the pattern `settings.is_production()` and
    `main.py`'s `_SPA_DIR` already establish. None renders as `local` rather
    than as a blank, because "not built by a deploy" is a fact worth stating.
    """
    raw = os.environ.get("VENONA_BUILD_COMMIT") or os.environ.get("RENDER_GIT_COMMIT")
    return raw.strip()[:7] if raw and raw.strip() else None


RELEASE = _release()
COMMIT = _commit()
STARTED_AT = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
