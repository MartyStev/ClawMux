"""
ClawMux — Secret encryption at rest (Fernet).

OpenClaw device credentials (Ed25519 private key, device/gateway tokens) are
stored in PostgreSQL encrypted with CREDENTIAL_ENCRYPTION_KEY.

Stored format:   "fernet:v1:<base64 token>"
Legacy plaintext values (no prefix) are still readable, which allows a
zero-downtime rollout: set the key, then run
`scripts/encrypt_existing_credentials.py` to re-encrypt old rows.
"""

from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from src.core.config import settings

PREFIX = "fernet:v1:"

_fernet: Optional[Fernet] = None
_fernet_key_source: Optional[str] = None


class CredentialEncryptionError(RuntimeError):
    """Raised when a value cannot be (de)crypted with the configured key."""


def _get_fernet() -> Fernet:
    global _fernet, _fernet_key_source
    key = settings.credential_encryption_key.strip()
    if not key:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is not configured but an encrypted "
            "credential was encountered"
        )
    if _fernet is None or _fernet_key_source != key:
        try:
            _fernet = Fernet(key.encode())
        except (ValueError, TypeError) as e:
            raise CredentialEncryptionError(
                f"CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key: {e}"
            ) from e
        _fernet_key_source = key
    return _fernet


def is_encrypted(value: str) -> bool:
    return value.startswith(PREFIX)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt with the configured key; no-op passthrough when key is unset."""
    if not settings.credential_encryption_key.strip():
        return plaintext
    token = _get_fernet().encrypt(plaintext.encode()).decode()
    return f"{PREFIX}{token}"


def decrypt_secret(value: str) -> str:
    """Decrypt a 'fernet:v1:...' value; legacy plaintext passes through."""
    if not is_encrypted(value):
        return value
    try:
        return _get_fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        raise CredentialEncryptionError(
            "Failed to decrypt credential — CREDENTIAL_ENCRYPTION_KEY does not "
            "match the key the value was encrypted with"
        ) from e


class EncryptedText(TypeDecorator):
    """TEXT column transparently encrypted at rest via Fernet."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if is_encrypted(value):
            return value  # already encrypted (e.g. raw values from the script)
        return encrypt_secret(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decrypt_secret(value)
