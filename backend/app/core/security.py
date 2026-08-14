import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from pydantic import BaseModel

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"
REFRESH_TOKEN_BYTES = 32

_password_hasher = PasswordHash.recommended()


class AccessClaims(BaseModel):
    sub: UUID
    sid: UUID
    iat: int
    exp: int
    type: str


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hasher.verify(password, password_hash)


def create_access_token(
    user_id: UUID,
    session_id: UUID,
    now: datetime | None = None,
) -> str:
    settings = get_settings()
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.ACCESS_TOKEN_MINUTES)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> AccessClaims:
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[JWT_ALGORITHM],
        options={"require": ["exp", "sub", "sid", "type", "iat"]},
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("invalid token type")
    return AccessClaims(
        sub=UUID(payload["sub"]),
        sid=UUID(payload["sid"]),
        iat=payload["iat"],
        exp=payload["exp"],
        type=payload["type"],
    )


def new_refresh_token() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    digest = hashlib.sha256(raw_token.encode()).hexdigest()
    return raw_token, digest
