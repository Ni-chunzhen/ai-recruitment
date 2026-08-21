"""Integrations summary / partial update / audit (Task 3 RED)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.core.config import get_settings
from app.models.integration_secret import IntegrationSecret
from app.services.audit import RequestContext
from app.services.crypto import encrypt_secret

MODULE = "app.services.integrations"
REPO = "app.repositories.integration_secrets"


@pytest.fixture
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    key = Fernet.generate_key()
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", key.decode("ascii"))
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


def _ctx() -> RequestContext:
    return RequestContext(request_id="req-int-1", ip_address="127.0.0.1")


def _settings(**overrides) -> SimpleNamespace:
    base = dict(
        DIFY_API_BASE_URL="https://env.dify.example/v1",
        dify_api_key="env-api-key-secret",
        dify_jd_parse_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_score_dimension_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_resume_parse_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_resume_score_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_interview_question_generate_api_key_secret=SimpleNamespace(
            get_secret_value=lambda: ""
        ),
        DIFY_JD_PARSE_WORKFLOW_ID="wf-jd",
        DIFY_SCORE_DIMENSION_WORKFLOW_ID="",
        DIFY_RESUME_PARSE_WORKFLOW_ID="",
        DIFY_RESUME_SCORE_WORKFLOW_ID="",
        dify_interview_question_generate_workflow_id="",
        AI_PROVIDER="mock",
        dify_interview_question_live_enabled=False,
        MINIO_ENDPOINT="127.0.0.1:9000",
        MINIO_ACCESS_KEY="env-access",
        minio_access_key="env-access",
        minio_secret_key="env-secret",
        MINIO_BUCKET="resumes",
        MINIO_SECURE=False,
        MINIO_PRESIGN_SECONDS=600,
        celery_mail_queue_name="mail_outbound",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _row(
    *,
    provider: str,
    config_key: str,
    value_encrypted: str,
    is_secret: bool,
    enabled: bool = True,
) -> IntegrationSecret:
    now = datetime.now(UTC)
    return IntegrationSecret(
        id=uuid4(),
        provider=provider,
        config_key=config_key,
        value_encrypted=value_encrypted,
        is_secret=is_secret,
        enabled=enabled,
        updated_by=None,
        created_at=now,
        updated_at=now,
    )


def _install_store(monkeypatch: pytest.MonkeyPatch, store: dict[tuple[str, str], IntegrationSecret]):
    async def list_secrets(session, *, provider=None):
        rows = list(store.values())
        if provider is not None:
            rows = [r for r in rows if r.provider == provider]
        return rows

    async def get_secret(session, *, provider, config_key):
        return store.get((provider, config_key))

    async def upsert(
        session,
        *,
        provider,
        config_key,
        value_encrypted,
        is_secret,
        enabled=True,
        updated_by=None,
    ):
        existing = store.get((provider, config_key))
        if existing is None:
            row = _row(
                provider=provider,
                config_key=config_key,
                value_encrypted=value_encrypted,
                is_secret=is_secret,
                enabled=enabled,
            )
            row.updated_by = updated_by
            store[(provider, config_key)] = row
            return row
        existing.value_encrypted = value_encrypted
        existing.is_secret = is_secret
        existing.enabled = enabled
        existing.updated_by = updated_by
        return existing

    monkeypatch.setattr(f"{REPO}.list_integration_secrets", list_secrets)
    monkeypatch.setattr(f"{REPO}.get_integration_secret", get_secret)
    monkeypatch.setattr(f"{REPO}.upsert_integration_secret", upsert)
    # integrations may import symbols directly — patch both
    import app.services.integrations as svc
    import app.services.integration_config as cfg

    monkeypatch.setattr(cfg.secrets_repo, "list_integration_secrets", list_secrets)
    if hasattr(svc, "secrets_repo"):
        monkeypatch.setattr(svc.secrets_repo, "list_integration_secrets", list_secrets)
        monkeypatch.setattr(svc.secrets_repo, "get_integration_secret", get_secret)
        monkeypatch.setattr(svc.secrets_repo, "upsert_integration_secret", upsert)


@pytest.mark.asyncio
async def test_summary_secrets_only_configured_flag(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services import integrations as svc

    store: dict = {}
    _install_store(monkeypatch, store)
    summary = await svc.get_integrations_summary(
        AsyncMock(), settings=_settings()
    )
    rendered = json.dumps(summary, ensure_ascii=False)
    assert "env-api-key-secret" not in rendered
    assert "env-secret" not in rendered
    assert "value_encrypted" not in rendered
    assert "enc:v1:" not in rendered
    assert summary["dify"]["api_key"]["configured"] is True
    assert "value" not in summary["dify"]["api_key"]
    assert summary["dify"]["api_base_url"]["value"] == "https://env.dify.example/v1"
    assert summary["restart_required"] is True
    assert summary["mail"]["delivery_provider"] == "console"
    assert summary["mail"]["smtp_enabled"] is False
    assert "live_enabled_env" in summary["dify"]
    assert summary["dify"]["live_enabled_env"] is False


@pytest.mark.asyncio
async def test_update_empty_secret_keeps_previous(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services import integrations as svc

    store: dict = {}
    original = encrypt_secret("keep-me-secret")
    assert original is not None
    store[("dify", "api_key")] = _row(
        provider="dify",
        config_key="api_key",
        value_encrypted=original,
        is_secret=True,
    )
    _install_store(monkeypatch, store)
    audits: list[dict] = []

    async def capture_audit(session, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(svc, "record_audit", capture_audit)
    actor = uuid4()
    result = await svc.update_dify(
        AsyncMock(),
        payload={"api_key": "", "api_base_url": "https://new.example/v1"},
        actor_user_id=actor,
        request_context=_ctx(),
        settings=_settings(),
    )
    assert store[("dify", "api_key")].value_encrypted == original
    assert store[("dify", "api_base_url")].value_encrypted.startswith("enc:v1:")
    assert result["restart_required"] is True
    assert result["dify"]["api_key"]["configured"] is True
    assert "keep-me-secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_update_rejects_live_enabled_field(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services import integrations as svc

    _install_store(monkeypatch, {})
    with pytest.raises(Exception) as excinfo:
        await svc.update_dify(
            AsyncMock(),
            payload={"live_enabled": True},
            actor_user_id=uuid4(),
            request_context=_ctx(),
            settings=_settings(),
        )
    assert "live" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_update_rejects_smtp_and_mail_write(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services import integrations as svc

    _install_store(monkeypatch, {})
    with pytest.raises(Exception):
        await svc.update_dify(
            AsyncMock(),
            payload={"smtp_host": "mail.example"},
            actor_user_id=uuid4(),
            request_context=_ctx(),
            settings=_settings(),
        )
    with pytest.raises(Exception):
        await svc.update_mail(
            AsyncMock(),
            payload={"provider_name": "smtp"},
            actor_user_id=uuid4(),
            request_context=_ctx(),
            settings=_settings(),
        )


@pytest.mark.asyncio
async def test_update_audits_without_secret_values(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services import integrations as svc

    store: dict = {}
    _install_store(monkeypatch, store)
    audits: list[dict] = []

    async def capture_audit(session, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(svc, "record_audit", capture_audit)
    await svc.update_dify(
        AsyncMock(),
        payload={"api_key": "brand-new-secret-value", "ai_provider": "dify"},
        actor_user_id=uuid4(),
        request_context=_ctx(),
        settings=_settings(),
    )
    assert len(audits) == 1
    assert audits[0]["action"] == "integration.config_updated"
    changes = audits[0]["changes"]
    rendered = json.dumps(changes, ensure_ascii=False)
    assert "brand-new-secret-value" not in rendered
    assert "enc:v1:" not in rendered
    assert changes["provider"] == "dify"
    assert "api_key" in changes["updated_keys"] or "api_key" in changes.get(
        "secret_keys_updated", []
    )
    assert "ai_provider" in changes["updated_keys"]


@pytest.mark.asyncio
async def test_update_minio_presign_and_ai_provider_validation(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services import integrations as svc

    _install_store(monkeypatch, {})
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    with pytest.raises(Exception):
        await svc.update_dify(
            AsyncMock(),
            payload={"ai_provider": "openai"},
            actor_user_id=uuid4(),
            request_context=_ctx(),
            settings=_settings(),
        )
    with pytest.raises(Exception):
        await svc.update_minio(
            AsyncMock(),
            payload={"presign_seconds": "999999"},
            actor_user_id=uuid4(),
            request_context=_ctx(),
            settings=_settings(),
        )
