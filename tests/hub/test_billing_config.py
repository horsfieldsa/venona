"""The billing configuration is all-or-nothing.

`assert_usable` refuses a partial configuration on purpose: a secret key with no
webhook secret takes money and never hears that it did.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "hub"))

from hub.config import Settings, assert_usable  # noqa: E402
from hubmaterial import TEST_SEAL_KEY, TEST_SIGNING_KEY  # noqa: E402

#: The five that must arrive together.
BILLING_KEYS = {
    "HUB_STRIPE_SECRET_KEY",
    "HUB_STRIPE_WEBHOOK_SECRET",
}

#: Read by `config.py`, but not part of the all-or-nothing set: the hub starts
#: happily without them.
OPTIONAL_BILLING_KEYS = {
    "HUB_STRIPE_PORTAL_CONFIGURATION",
}


def _settings(**over) -> Settings:
    base = dict(
        _env_file=None,
        public_url="https://hub.example.com",
        session_secret="s" * 48,
        seal_key=TEST_SEAL_KEY,
        admin_token="a" * 40,
        oidc_signing_key=TEST_SIGNING_KEY,
        google_client_id="x",
        google_client_secret="y",
    )
    return Settings(**{**base, **over})


def test_no_billing_configuration_at_all_is_valid() -> None:
    """A hub that takes no money is a valid hub. Every local one, and the
    production one until launch."""
    assert_usable(_settings())


def test_one_billing_variable_alone_is_refused() -> None:
    with pytest.raises(RuntimeError, match="partly configured"):
        assert_usable(_settings(stripe_secret_key="sk_test_" + "x" * 24))


def test_both_together_are_accepted() -> None:
    assert_usable(
        _settings(
            stripe_secret_key="sk_test_" + "x" * 24,
            stripe_webhook_secret="whsec_" + "y" * 32,
        )
    )
