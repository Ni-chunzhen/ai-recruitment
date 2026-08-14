import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    new_refresh_token,
    verify_password,
)


def test_hash_password_is_not_plaintext() -> None:
    password = "StrongPass123!"
    hashed = hash_password(password)

    assert hashed != password
    assert hashed.startswith("$argon2")


def test_verify_password_accepts_correct_password() -> None:
    password = "StrongPass123!"
    hashed = hash_password(password)

    assert verify_password(password, hashed) is True


def test_verify_password_rejects_wrong_password() -> None:
    hashed = hash_password("StrongPass123!")

    assert verify_password("WrongPass123!", hashed) is False


def test_create_and_decode_access_token(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-jwt-signing-32b")
    get_settings.cache_clear()

    user_id = uuid4()
    session_id = uuid4()

    token = create_access_token(user_id, session_id)
    claims = decode_access_token(token)

    assert claims.sub == user_id
    assert claims.sid == session_id
    assert claims.type == "access"

    get_settings.cache_clear()


def test_decode_access_token_rejects_expired_token(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-jwt-signing-32b")
    get_settings.cache_clear()

    user_id = uuid4()
    session_id = uuid4()
    expired_now = datetime(2020, 1, 1, tzinfo=UTC)
    token = create_access_token(user_id, session_id, now=expired_now)

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)

    get_settings.cache_clear()


def test_decode_access_token_rejects_wrong_type(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-jwt-signing-32b")
    get_settings.cache_clear()

    settings = get_settings()
    payload = {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=15)).timestamp()),
        "type": "refresh",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token)

    get_settings.cache_clear()


def test_decode_access_token_rejects_invalid_signature(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-jwt-signing-32b")
    get_settings.cache_clear()

    token = create_access_token(uuid4(), uuid4())
    tampered = token + "tampered"

    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(tampered)

    get_settings.cache_clear()


def test_new_refresh_token_returns_random_plaintext_and_digest() -> None:
    raw_token, digest = new_refresh_token()

    assert len(raw_token) >= 32
    assert digest == hashlib.sha256(raw_token.encode()).hexdigest()
    assert digest != raw_token


def test_new_refresh_token_generates_unique_values() -> None:
    first = new_refresh_token()
    second = new_refresh_token()

    assert first != second
