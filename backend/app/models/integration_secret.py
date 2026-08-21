"""Integration secrets ORM + whitelist (stage 11 / Task 1).

Stored values use a single ``value_encrypted`` column (Fernet via DATA_ENCRYPTION_KEY
at the service layer). No plaintext secret columns. Only Dify/MinIO keys are storable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

INTEGRATION_PROVIDER_DIFY = "dify"
INTEGRATION_PROVIDER_MINIO = "minio"
INTEGRATION_PROVIDERS_STORABLE = frozenset(
    {INTEGRATION_PROVIDER_DIFY, INTEGRATION_PROVIDER_MINIO}
)

# config_key -> is_secret (metadata only; all values still stored encrypted)
DIFY_CONFIG_KEYS: dict[str, bool] = {
    "api_base_url": False,
    "api_key": True,
    "jd_parse_api_key": True,
    "score_dimension_api_key": True,
    "jd_parse_workflow_id": False,
    "score_dimension_workflow_id": False,
    "resume_parse_api_key": True,
    "resume_score_api_key": True,
    "resume_parse_workflow_id": False,
    "resume_score_workflow_id": False,
    "interview_question_generate_api_key": True,
    "interview_question_generate_workflow_id": False,
    "ai_provider": False,
}

MINIO_CONFIG_KEYS: dict[str, bool] = {
    "endpoint": False,
    "access_key": True,
    "secret_key": True,
    "bucket": False,
    "secure": False,
    "presign_seconds": False,
}

ROOT_SECRET_ENV_NAMES = frozenset(
    {
        "DATA_ENCRYPTION_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "CELERY_BROKER_URL",
        "JWT_SECRET",
    }
)

_PROVIDER_KEYS: dict[str, dict[str, bool]] = {
    INTEGRATION_PROVIDER_DIFY: DIFY_CONFIG_KEYS,
    INTEGRATION_PROVIDER_MINIO: MINIO_CONFIG_KEYS,
}


class IntegrationConfigKeyError(ValueError):
    """Unknown provider/key or forbidden root-secret key name."""


def is_secret_config_key(provider: str, config_key: str) -> bool:
    keys = _PROVIDER_KEYS.get(provider)
    if keys is None or config_key not in keys:
        raise IntegrationConfigKeyError(
            f"unknown integration config key: {provider}.{config_key}"
        )
    return keys[config_key]


def validate_integration_config_key(provider: str, config_key: str) -> None:
    if config_key in ROOT_SECRET_ENV_NAMES:
        raise IntegrationConfigKeyError(
            f"root secret key is forbidden as config_key: {config_key}"
        )
    if provider not in INTEGRATION_PROVIDERS_STORABLE:
        raise IntegrationConfigKeyError(f"unknown integration provider: {provider}")
    keys = _PROVIDER_KEYS[provider]
    if config_key not in keys:
        raise IntegrationConfigKeyError(
            f"unknown integration config key: {provider}.{config_key}"
        )


class IntegrationSecret(Base):
    __tablename__ = "integration_secrets"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "config_key",
            name="uq_integration_secrets_provider_key",
        ),
        CheckConstraint(
            "provider IN ('dify', 'minio')",
            name="ck_integration_secrets_provider",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    config_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
