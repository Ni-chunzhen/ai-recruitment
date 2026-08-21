"""IntegrationSecret whitelist / root-key bans (Task 1 RED)."""

from __future__ import annotations

import pytest


def test_integration_providers_and_keys_whitelist() -> None:
    from app.models.integration_secret import (
        DIFY_CONFIG_KEYS,
        INTEGRATION_PROVIDER_DIFY,
        INTEGRATION_PROVIDER_MINIO,
        INTEGRATION_PROVIDERS_STORABLE,
        MINIO_CONFIG_KEYS,
        is_secret_config_key,
        validate_integration_config_key,
    )

    assert INTEGRATION_PROVIDERS_STORABLE == frozenset(
        {INTEGRATION_PROVIDER_DIFY, INTEGRATION_PROVIDER_MINIO}
    )
    assert "mail" not in INTEGRATION_PROVIDERS_STORABLE
    assert "smtp" not in DIFY_CONFIG_KEYS
    assert "smtp_host" not in MINIO_CONFIG_KEYS

    assert DIFY_CONFIG_KEYS["api_key"] is True
    assert DIFY_CONFIG_KEYS["api_base_url"] is False
    assert MINIO_CONFIG_KEYS["secret_key"] is True
    assert MINIO_CONFIG_KEYS["access_key"] is True
    assert MINIO_CONFIG_KEYS["endpoint"] is False

    assert is_secret_config_key(INTEGRATION_PROVIDER_DIFY, "api_key") is True
    assert is_secret_config_key(INTEGRATION_PROVIDER_MINIO, "bucket") is False

    validate_integration_config_key(INTEGRATION_PROVIDER_DIFY, "api_key")
    with pytest.raises(ValueError, match="unknown"):
        validate_integration_config_key(INTEGRATION_PROVIDER_DIFY, "smtp_password")
    with pytest.raises(ValueError, match="unknown|provider"):
        validate_integration_config_key("mail", "provider_name")


def test_root_secret_env_names_locked_and_never_config_keys() -> None:
    from app.models.integration_secret import (
        DIFY_CONFIG_KEYS,
        MINIO_CONFIG_KEYS,
        ROOT_SECRET_ENV_NAMES,
        validate_integration_config_key,
    )

    assert "DATA_ENCRYPTION_KEY" in ROOT_SECRET_ENV_NAMES
    assert "DATABASE_URL" in ROOT_SECRET_ENV_NAMES
    assert "JWT_SECRET" in ROOT_SECRET_ENV_NAMES
    assert "REDIS_URL" in ROOT_SECRET_ENV_NAMES
    assert "CELERY_BROKER_URL" in ROOT_SECRET_ENV_NAMES

    all_keys = set(DIFY_CONFIG_KEYS) | set(MINIO_CONFIG_KEYS)
    assert all_keys.isdisjoint(ROOT_SECRET_ENV_NAMES)
    assert "DATA_ENCRYPTION_KEY" not in all_keys

    with pytest.raises(ValueError):
        validate_integration_config_key("dify", "DATA_ENCRYPTION_KEY")
    with pytest.raises(ValueError):
        validate_integration_config_key("minio", "DATA_ENCRYPTION_KEY")


def test_integration_secret_model_has_value_encrypted_no_plaintext_secret_column() -> None:
    from app.models.integration_secret import IntegrationSecret

    cols = {c.name for c in IntegrationSecret.__table__.columns}
    assert "value_encrypted" in cols
    assert "enabled" in cols
    assert "created_at" in cols
    assert "updated_at" in cols
    assert "provider" in cols
    assert "config_key" in cols
    assert "is_secret" in cols
    # no plaintext secret storage columns
    forbidden = {
        "secret",
        "secret_key",
        "api_key",
        "password",
        "value_plain",
        "value_nonsecret",
        "plaintext",
    }
    assert cols.isdisjoint(forbidden)
    assert "smtp" not in cols

    uq_names = {uq.name for uq in IntegrationSecret.__table__.constraints if getattr(uq, "name", None)}
    assert "uq_integration_secrets_provider_key" in uq_names
