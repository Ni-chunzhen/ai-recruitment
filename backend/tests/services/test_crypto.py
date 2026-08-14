"""RED/GREEN tests for meeting-password authenticated encryption."""

from __future__ import annotations

import logging

import pytest
from cryptography.fernet import Fernet

from app.core.config import get_settings
from app.services.crypto import (
    CIPHER_PREFIX,
    EncryptionError,
    decrypt_secret,
    encrypt_secret,
)


def _set_key(monkeypatch: pytest.MonkeyPatch, key: bytes | str) -> None:
    value = key.decode("ascii") if isinstance(key, bytes) else key
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", value)
    get_settings.cache_clear()


@pytest.fixture
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    key = Fernet.generate_key()
    _set_key(monkeypatch, key)
    yield key
    get_settings.cache_clear()


def test_plaintext_does_not_appear_in_ciphertext(fernet_key: bytes) -> None:
    plain = "meet-secret-password-123"
    cipher = encrypt_secret(plain)
    assert cipher is not None
    assert cipher.startswith(CIPHER_PREFIX)
    assert plain not in cipher
    assert "xor" not in cipher.lower()


def test_correct_key_can_decrypt(fernet_key: bytes) -> None:
    plain = "correct-horse-battery"
    cipher = encrypt_secret(plain)
    assert decrypt_secret(cipher) == plain


def test_wrong_key_cannot_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    first = Fernet.generate_key()
    second = Fernet.generate_key()
    _set_key(monkeypatch, first)
    cipher = encrypt_secret("round-password")
    _set_key(monkeypatch, second)
    with pytest.raises(EncryptionError):
        decrypt_secret(cipher)


def test_same_plaintext_encrypts_to_different_ciphertext(fernet_key: bytes) -> None:
    plain = "same-plain-text"
    first = encrypt_secret(plain)
    second = encrypt_secret(plain)
    assert first != second
    assert decrypt_secret(first) == plain
    assert decrypt_secret(second) == plain


def test_tampered_ciphertext_fails(fernet_key: bytes) -> None:
    cipher = encrypt_secret("tamper-me")
    assert cipher is not None
    mutated = cipher[:-2] + ("A" if cipher[-1] != "A" else "B")
    with pytest.raises(EncryptionError):
        decrypt_secret(mutated)


def test_missing_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(EncryptionError):
        encrypt_secret("cannot-store-plaintext")
    get_settings.cache_clear()


def test_invalid_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "not-a-fernet-key")
    get_settings.cache_clear()
    with pytest.raises(EncryptionError):
        encrypt_secret("cannot-downgrade-to-xor")
    get_settings.cache_clear()


def test_encrypt_does_not_log_plaintext(
    fernet_key: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "do-not-log-this-password"
    with caplog.at_level(logging.DEBUG):
        encrypt_secret(secret)
    assert secret not in caplog.text
