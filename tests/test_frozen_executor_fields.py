"""A session cookie is not a shell.

`PUT /api/config/settings` writes a file `get_settings` layers under env, so
anything reachable from it is writable by whoever holds a session cookie.
`CliExecutor._binary` runs `shutil.which` on `settings.llm.cli_path_for(...)`
and execs the result, which is why every path a harness can be exec'd through
is in `config_service.FROZEN_FIELDS` and refused there.

`grok_cli_path` and `grok_pinned_version` were in neither `FROZEN_FIELDS` nor
`RESTART_FIELDS` while their Claude Code and Codex counterparts were in both,
so on an instance running Grok an administrator could name the binary. The
gap was silent because the guard is a list somebody extends by hand, which is
what this derives instead.
"""

from __future__ import annotations

import pytest
from venona.services.config_service import FROZEN_FIELDS
from venona.settings import LlmSettings

#: Suffixes that name something the server EXECS or compares an install against.
EXECUTABLE = ("cli_path", "pinned_version")

FIELDS = sorted(name for name in LlmSettings.model_fields if name.endswith(EXECUTABLE))


def test_the_suffixes_still_match_something() -> None:
    """A rename that emptied this list would make every case below vacuous."""
    assert len(FIELDS) >= 6, FIELDS


@pytest.mark.parametrize("field", FIELDS)
def test_every_executable_setting_is_frozen(field: str) -> None:
    assert ("llm", field) in FROZEN_FIELDS, (
        f"llm.{field} reaches argv through `CliExecutor._binary` but is writable "
        "over HTTP -- add it to config_service.FROZEN_FIELDS"
    )
