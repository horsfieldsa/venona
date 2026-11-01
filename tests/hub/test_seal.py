"""Fernet sealing of instance mgmt tokens."""

from __future__ import annotations

import pytest
from hubmaterial import TEST_SEAL_KEY

from hub import seal


def test_round_trip() -> None:
    token = "vmgmt_" + "0" * 64
    sealed = seal.seal(TEST_SEAL_KEY, token)
    assert sealed != token
    assert seal.unseal(TEST_SEAL_KEY, sealed) == token


def test_wrong_key_refuses() -> None:
    sealed = seal.seal(TEST_SEAL_KEY, "value")
    with pytest.raises(ValueError, match="HUB_SEAL_KEY"):
        seal.unseal(seal.new_seal_key(), sealed)


def test_key_usable() -> None:
    assert seal.key_usable(TEST_SEAL_KEY)
    assert seal.key_usable(seal.new_seal_key())
    assert not seal.key_usable("")
    assert not seal.key_usable("not-a-key")
