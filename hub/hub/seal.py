"""Sealing instance management tokens at rest.

Fernet under `HUB_SEAL_KEY`. Sealed rather than HASHED because the hub must
PRESENT the token outbound (the poller and the provision proxy put it in an
Authorization header), and sealed rather than PLAINTEXT so a `pg_dump` in
`backups/` does not carry a credential that reaches every registered
instance's management surface. The key lives only in the environment, so the
dump and the key travel separately.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


def new_seal_key() -> str:
    return Fernet.generate_key().decode()


def key_usable(key: str) -> bool:
    try:
        fernet = Fernet(key.encode())
    except Exception:
        return False
    probe = b"round-trip"
    return fernet.decrypt(fernet.encrypt(probe)) == probe


def seal(key: str, value: str) -> str:
    return Fernet(key.encode()).encrypt(value.encode()).decode()


def unseal(key: str, sealed: str) -> str:
    try:
        return Fernet(key.encode()).decrypt(sealed.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "sealed value does not open with HUB_SEAL_KEY -- the key was "
            "rotated without resealing the directory (hub instances "
            "set-mgmt-token re-seals a row)"
        ) from exc
