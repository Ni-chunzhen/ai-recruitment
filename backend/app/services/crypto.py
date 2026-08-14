"""Field encryption for meeting passwords and other secrets.

Uses Fernet (AES-128-CBC + HMAC). Key comes only from DATA_ENCRYPTION_KEY.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

CIPHER_PREFIX = "enc:v1:"


class EncryptionError(Exception):
    pass


def _load_fernet() -> Fernet:
    key = get_settings().data_encryption_key.strip()
    if not key:
        raise EncryptionError("encryption key is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise EncryptionError("encryption key is invalid") from exc


def encrypt_secret(plain: str | None) -> str | None:
    if plain is None or plain == "":
        return None
    token = _load_fernet().encrypt(plain.encode("utf-8"))
    return CIPHER_PREFIX + token.decode("ascii")


def decrypt_secret(cipher: str | None) -> str | None:
    if cipher is None or cipher == "":
        return None
    if not cipher.startswith(CIPHER_PREFIX):
        raise EncryptionError("unsupported ciphertext version")
    raw = cipher[len(CIPHER_PREFIX) :].encode("ascii")
    try:
        return _load_fernet().decrypt(raw).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionError("ciphertext is invalid") from exc
    except Exception as exc:
        raise EncryptionError("ciphertext is invalid") from exc
