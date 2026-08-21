"""Integration config overlay: env baseline ← enabled DB (Task 2 RED)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.core.config import get_settings
from app.services.crypto import encrypt_secret


@pytest.fixture
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    key = Fernet.generate_key()
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", key.decode("ascii"))
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


def _settings(
    *,
    dify_api_key: str = "env-dify-key",
    dify_base: str = "https://env.example/v1",
    ai_provider: str = "mock",
    minio_endpoint: str = "127.0.0.1:9000",
    mail_queue: str = "mail_outbound",
) -> SimpleNamespace:
    return SimpleNamespace(
        DIFY_API_BASE_URL=dify_base,
        dify_api_key=dify_api_key,
        dify_jd_parse_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_score_dimension_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_resume_parse_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_resume_score_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_interview_question_generate_api_key_secret=SimpleNamespace(
            get_secret_value=lambda: ""
        ),
        DIFY_JD_PARSE_WORKFLOW_ID="",
        DIFY_SCORE_DIMENSION_WORKFLOW_ID="",
        DIFY_RESUME_PARSE_WORKFLOW_ID="",
        DIFY_RESUME_SCORE_WORKFLOW_ID="",
        dify_interview_question_generate_workflow_id="",
        AI_PROVIDER=ai_provider,
        MINIO_ENDPOINT=minio_endpoint,
        MINIO_ACCESS_KEY="env-access",
        minio_access_key="env-access",
        minio_secret_key="env-secret",
        MINIO_BUCKET="resumes",
        MINIO_SECURE=False,
        MINIO_PRESIGN_SECONDS=600,
        celery_mail_queue_name=mail_queue,
    )


def test_overlay_enabled_db_secret_overrides_env(fernet_key: bytes) -> None:
    from app.services.integration_config import (
        OVERLAY_STATUS_OK,
        materialize_overlay_entry,
        resolve_config_value,
    )

    cipher = encrypt_secret("db-dify-key")
    assert cipher is not None
    entry = materialize_overlay_entry(
        provider="dify",
        config_key="api_key",
        value_encrypted=cipher,
        enabled=True,
        is_secret=True,
    )
    assert entry.status == OVERLAY_STATUS_OK
    assert entry.plain_value == "db-dify-key"

    overlay = entry.as_overlay()
    settings = _settings(dify_api_key="env-dify-key")
    assert resolve_config_value(overlay, settings, "dify", "api_key") == "db-dify-key"
    assert resolve_config_value(overlay, settings, "dify", "api_base_url") == (
        "https://env.example/v1"
    )


def test_overlay_disabled_falls_back_to_env(fernet_key: bytes) -> None:
    from app.services.integration_config import (
        OVERLAY_STATUS_DISABLED,
        materialize_overlay_entry,
        resolve_config_value,
    )

    cipher = encrypt_secret("db-should-not-apply")
    entry = materialize_overlay_entry(
        provider="dify",
        config_key="api_key",
        value_encrypted=cipher,
        enabled=False,
        is_secret=True,
    )
    assert entry.status == OVERLAY_STATUS_DISABLED
    assert entry.plain_value is None

    settings = _settings(dify_api_key="env-dify-key")
    assert (
        resolve_config_value(entry.as_overlay(), settings, "dify", "api_key")
        == "env-dify-key"
    )


def test_overlay_decrypt_failure_falls_back(
    fernet_key: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    from app.services.integration_config import (
        OVERLAY_STATUS_DECRYPT_ERROR,
        materialize_overlay_entry,
        resolve_config_value,
    )

    bad = "enc:v1:not-a-valid-fernet-token===="
    with caplog.at_level(logging.WARNING):
        entry = materialize_overlay_entry(
            provider="dify",
            config_key="api_key",
            value_encrypted=bad,
            enabled=True,
            is_secret=True,
        )
    assert entry.status == OVERLAY_STATUS_DECRYPT_ERROR
    assert entry.plain_value is None
    rendered = " ".join(r.getMessage() for r in caplog.records)
    assert bad not in rendered
    assert "enc:v1:" not in rendered

    settings = _settings(dify_api_key="env-fallback")
    assert (
        resolve_config_value(entry.as_overlay(), settings, "dify", "api_key")
        == "env-fallback"
    )


def test_overlay_empty_ciphertext_classified(fernet_key: bytes) -> None:
    from app.services.integration_config import (
        OVERLAY_STATUS_EMPTY_CIPHERTEXT,
        materialize_overlay_entry,
        resolve_config_value,
    )

    entry = materialize_overlay_entry(
        provider="minio",
        config_key="secret_key",
        value_encrypted="",
        enabled=True,
        is_secret=True,
    )
    assert entry.status == OVERLAY_STATUS_EMPTY_CIPHERTEXT
    settings = _settings()
    assert (
        resolve_config_value(entry.as_overlay(), settings, "minio", "secret_key")
        == "env-secret"
    )


def test_reject_root_secret_keys() -> None:
    from app.services.integration_config import (
        IntegrationOverlay,
        assert_not_root_secret,
        resolve_config_value,
    )

    with pytest.raises(ValueError, match="root|forbidden|DATA_ENCRYPTION"):
        assert_not_root_secret("DATA_ENCRYPTION_KEY")
    with pytest.raises(ValueError):
        assert_not_root_secret("JWT_SECRET")

    overlay = IntegrationOverlay.empty()
    settings = _settings()
    with pytest.raises(ValueError):
        resolve_config_value(overlay, settings, "dify", "DATA_ENCRYPTION_KEY")


def test_whitelist_rejects_unknown_provider_key(fernet_key: bytes) -> None:
    from app.services.integration_config import (
        OVERLAY_STATUS_UNKNOWN_KEY,
        IntegrationOverlay,
        materialize_overlay_entry,
        resolve_config_value,
    )

    entry = materialize_overlay_entry(
        provider="dify",
        config_key="smtp_password",
        value_encrypted=encrypt_secret("x") or "enc:v1:x",
        enabled=True,
        is_secret=True,
    )
    assert entry.status == OVERLAY_STATUS_UNKNOWN_KEY
    assert entry.plain_value is None

    with pytest.raises(ValueError, match="unknown"):
        resolve_config_value(IntegrationOverlay.empty(), _settings(), "dify", "smtp_host")
    with pytest.raises(ValueError, match="unknown|provider"):
        resolve_config_value(IntegrationOverlay.empty(), _settings(), "mail", "queue_name")


def test_mail_block_is_console_only() -> None:
    from app.services.integration_config import resolve_mail_block

    block = resolve_mail_block(_settings(mail_queue="mail_outbound"))
    assert block["delivery_provider"] == "console"
    assert block["queue_name"] == "mail_outbound"
    assert block["smtp_enabled"] is False
    assert "smtp_host" not in block
    assert "smtp_password" not in block


def test_config_update_metadata_restart_required() -> None:
    from app.services.integration_config import config_update_metadata

    meta = config_update_metadata()
    assert meta["restart_required"] is True
    assert "restart" in str(meta.get("message_key", "")).lower() or meta[
        "restart_required"
    ]


def test_resolve_does_not_mutate_settings_cache(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services.integration_config import (
        materialize_overlay_entry,
        resolve_config_value,
    )

    monkeypatch.setenv("DIFY_API_KEY", "cached-env-key")
    get_settings.cache_clear()
    before = get_settings()
    cipher = encrypt_secret("overlay-key")
    entry = materialize_overlay_entry(
        provider="dify",
        config_key="api_key",
        value_encrypted=cipher or "",
        enabled=True,
        is_secret=True,
    )
    # resolve against live Settings object for api_key property
    assert resolve_config_value(entry.as_overlay(), before, "dify", "api_key") == (
        "overlay-key"
    )
    after = get_settings()
    assert after is before
    assert after.dify_api_key == "cached-env-key"
