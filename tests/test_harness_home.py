"""A model service's credential survives a restart with no disk (ADR 0234).

The api runs without a persistent disk, so a credential the CLI writes and
refreshes itself -- Codex's `auth.json`, which has nothing to paste -- lives as
a row, restored into the home at boot. The last test is the load-bearing one:
the others failing is loud, that one failing is an instance signed out silently.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from venona.db import core as db
from venona.db.repositories import harness as repo
from venona.llm.harness import resolve
from venona.services import harness_service
from venona.settings import get_settings

CODEX = resolve("codex")


@pytest.fixture
def settings(tmp_path: Path):
    out = copy.deepcopy(get_settings())
    out.data_dir = tmp_path / "data"
    out.data_dir.mkdir()
    return out


def _sign_in(settings, harness, payload: str = '{"token": "abc"}') -> Path:
    """What `codex login` leaves behind."""
    home = harness_service.ensure_dirs(settings, harness)
    target = home / harness.credential_paths[0]
    target.write_text(payload)
    return target


def test_the_declared_file_is_what_gets_kept(settings, conn: db.Conn) -> None:
    _sign_in(settings, CODEX)
    home = harness_service.home_dir(settings, CODEX)
    (home / "models_cache.json").write_text("x" * 5000)

    assert harness_service.capture(conn, settings, CODEX) is True

    kept = json.loads(repo.home(conn, harness="codex")["files"])
    assert list(kept) == ["auth.json"], "the whole home was kept, not the credential"


def test_a_restart_with_an_empty_home_gets_the_credential_back(settings, conn, tmp_path) -> None:
    _sign_in(settings, CODEX)
    harness_service.capture(conn, settings, CODEX)

    fresh = copy.deepcopy(settings)
    fresh.data_dir = tmp_path / "after-deploy"
    fresh.data_dir.mkdir()

    assert harness_service.restore(conn, fresh, CODEX) is True
    restored = harness_service.home_dir(fresh, CODEX) / "auth.json"
    assert restored.read_text() == '{"token": "abc"}'
    assert restored.stat().st_mode & 0o777 == 0o600, "a credential readable by anything else"


def test_a_rotation_is_captured_and_an_unchanged_home_is_not_rewritten(settings, conn) -> None:
    _sign_in(settings, CODEX)
    assert harness_service.capture(conn, settings, CODEX) is True
    assert harness_service.capture(conn, settings, CODEX) is False, "rewrote an unchanged home"

    _sign_in(settings, CODEX, '{"token": "rotated"}')
    assert harness_service.capture(conn, settings, CODEX) is True
    kept = json.loads(repo.home(conn, harness="codex")["files"])
    import base64

    assert base64.b64decode(kept["auth.json"]).decode() == '{"token": "rotated"}'


def test_restore_never_clobbers_a_live_credential(settings, conn) -> None:
    """The home the CLI has been using may have rotated since the row was
    written, so an older copy must not be written over it."""
    _sign_in(settings, CODEX)
    harness_service.capture(conn, settings, CODEX)
    _sign_in(settings, CODEX, '{"token": "newer-than-the-row"}')

    harness_service.restore(conn, settings, CODEX)

    live = harness_service.home_dir(settings, CODEX) / "auth.json"
    assert live.read_text() == '{"token": "newer-than-the-row"}'


def test_a_harness_declaring_no_credential_is_never_captured(settings, conn) -> None:
    """Grok declares nothing because it has never been run: a guessed file would
    restore a home that looks populated and does not authenticate."""
    grok = resolve("grok")
    assert grok.credential_paths == ()
    home = harness_service.ensure_dirs(settings, grok)
    (home / "auth.json").write_text('{"token": "guessed"}')

    assert harness_service.capture(conn, settings, grok) is False
    assert repo.home(conn, harness="grok") is None


def test_a_sign_in_to_the_clis_own_home_is_captured(settings, conn, monkeypatch, tmp_path) -> None:
    """THE ONE THAT SHIPPED BROKEN AND SIGNED A LIVE INSTANCE OUT. `codex login`
    in a shell puts `auth.json` in the CLI's DEFAULT home under `$HOME`;
    Venona's stays empty until Venona writes there, so capture read only
    Venona's and wrote no row -- and the empty table read as "nothing to keep".
    """
    fake_home = tmp_path / "container-home"
    (fake_home / ".codex").mkdir(parents=True)
    (fake_home / ".codex" / "auth.json").write_text('{"token": "signed in by hand"}')
    monkeypatch.setenv("HOME", str(fake_home))

    # Venona's own home exists and is EMPTY, which is the live shape.
    harness_service.ensure_dirs(settings, CODEX)

    assert harness_service.capture(conn, settings, CODEX) is True, (
        "a sign-in to the CLI's own home was not captured"
    )
    kept = json.loads(repo.home(conn, harness="codex")["files"])
    import base64

    assert base64.b64decode(kept["auth.json"]).decode() == '{"token": "signed in by hand"}'


def test_venonas_own_home_wins_once_it_is_populated(settings, conn, monkeypatch, tmp_path) -> None:
    """The child uses Venona's as soon as it is populated, so capturing
    `$HOME`'s stale copy would keep the wrong one."""
    fake_home = tmp_path / "container-home"
    (fake_home / ".codex").mkdir(parents=True)
    (fake_home / ".codex" / "auth.json").write_text('{"token": "stale"}')
    monkeypatch.setenv("HOME", str(fake_home))
    _sign_in(settings, CODEX, '{"token": "the one in use"}')

    harness_service.capture(conn, settings, CODEX)

    kept = json.loads(repo.home(conn, harness="codex")["files"])
    import base64

    assert base64.b64decode(kept["auth.json"]).decode() == '{"token": "the one in use"}'


@pytest.mark.negative
def test_an_empty_home_never_overwrites_a_stored_credential(settings, conn, tmp_path) -> None:
    """THE ONE THAT WOULD BE SILENT. Two instances overlap for sixty seconds
    during a zero-downtime deploy and both capture; the one whose home is empty
    would put "no credential" over the only copy, and the next boot would come
    up signed out with nothing saying why.
    """
    _sign_in(settings, CODEX)
    harness_service.capture(conn, settings, CODEX)
    before = repo.home(conn, harness="codex")["files"]

    empty = copy.deepcopy(settings)
    empty.data_dir = tmp_path / "no-credential-here"
    empty.data_dir.mkdir()
    harness_service.ensure_dirs(empty, CODEX)

    assert harness_service.capture(conn, empty, CODEX) is False
    assert repo.home(conn, harness="codex")["files"] == before, (
        "an instance with an empty home overwrote the stored credential"
    )
