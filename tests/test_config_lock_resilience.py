"""A lock we cannot take must not end the save.

`PUT /api/admin/model` answered 500 in production on 2026-09-15 with
`PermissionError: [Errno 13] Permission denied:
'/tmp/venona-config-<digest>.lock'`. The temp directory had become a single
point of failure for every edit in the app: one unwritable lock file and the
operator could not change the model service, a source, a prompt or the
relevance profile again, behind a reference id naming nothing they could act
on.

The cross-process lock is the rarer half of the guard. `_key_lock` serialises
the config routes against each other -- they are sync `def` and run two at a
time on Starlette's threadpool -- and that is what the double-submit hazard
actually needs.
"""

from __future__ import annotations

import pytest
from venona.services import config_service


def test_a_lock_file_that_cannot_be_opened_does_not_break_the_save(tmp_path, monkeypatch) -> None:
    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(config_service, "open", refuse, raising=False)

    entered = False
    with config_service._exclusive(tmp_path / "settings.yaml", "settings"):
        entered = True
    assert entered, "the save never ran because the lock could not be taken"


def test_the_lock_is_keyed_by_user_as_well_as_by_path(tmp_path) -> None:
    """Two users take two files rather than contending for one they cannot
    share. A lock file is created 0644 by whoever gets there first, so a second
    user can never open it for writing -- which is the production failure."""
    import os

    path = tmp_path / "settings.yaml"
    path.write_text("a: 1")
    assert str(os.getuid()) in config_service._lock_path(path).name


def test_two_paths_still_take_two_locks(tmp_path) -> None:
    """The digest half of the key is what keeps two instances on one machine
    from serialising against each other."""
    one = tmp_path / "a.yaml"
    two = tmp_path / "b.yaml"
    one.write_text("a: 1")
    two.write_text("b: 2")
    assert config_service._lock_path(one) != config_service._lock_path(two)


@pytest.mark.parametrize("failure", [PermissionError, OSError, FileNotFoundError])
def test_every_os_failure_degrades_rather_than_raising(tmp_path, monkeypatch, failure) -> None:
    """`PermissionError` is the one that happened; a read-only or full temp
    directory raises a sibling, and neither is a reason to refuse an edit."""

    def refuse(*args, **kwargs):
        raise failure("nope")

    monkeypatch.setattr(config_service, "open", refuse, raising=False)
    with config_service._exclusive(tmp_path / "settings.yaml", "settings"):
        pass
