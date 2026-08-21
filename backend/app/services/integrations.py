"""Integration configuration management (summary / update / connectivity).

Service layer only — no HTTP routes. Never returns ciphertext or decrypted secrets.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.integration_secret import (
    DIFY_CONFIG_KEYS,
    INTEGRATION_PROVIDER_DIFY,
    INTEGRATION_PROVIDER_MINIO,
    MINIO_CONFIG_KEYS,
    ROOT_SECRET_ENV_NAMES,
    IntegrationConfigKeyError,
    is_secret_config_key,
    validate_integration_config_key,
)
from app.repositories import integration_secrets as secrets_repo
from app.services.audit import RequestContext, record_audit
from app.services.crypto import EncryptionError, encrypt_secret
from app.services.integration_config import (
    IntegrationOverlay,
    config_update_metadata,
    load_integration_overlay,
    resolve_config_value,
    resolve_mail_block,
)
from app.services.integration_connectivity import (
    ConnectivityResult,
    probe_dify,
    probe_mail_console,
    probe_minio,
)

AUDIT_CONFIG_UPDATED = "integration.config_updated"
AUDIT_CONNECTIVITY_TESTED = "integration.connectivity_tested"

AI_PROVIDER_ALLOWED = frozenset({"mock", "dify"})
MINIO_PRESIGN_MAX = 86400

_FORBIDDEN_UPDATE_KEYS = frozenset(
    {
        "live_enabled",
        "live_enabled_env",
        "dify_interview_question_live_enabled",
        "smtp_host",
        "smtp_port",
        "smtp_user",
        "smtp_password",
        "smtp_enabled",
        "value_encrypted",
        "secret_ciphertext",
    }
) | ROOT_SECRET_ENV_NAMES


class IntegrationValidationError(ValueError):
    """Invalid integration update payload."""


class IntegrationMailWriteError(ValueError):
    """Mail block is read-only (Console only)."""


def _settings_or_default(settings: Any | None) -> Any:
    return settings if settings is not None else get_settings()


def _field_view(
    *,
    overlay: IntegrationOverlay,
    settings: Any,
    provider: str,
    config_key: str,
    is_secret: bool,
) -> dict[str, Any]:
    entry = overlay.entries.get((provider, config_key))
    status = entry.status if entry is not None else "ok"
    enabled = bool(entry.enabled) if entry is not None else True
    try:
        effective = resolve_config_value(overlay, settings, provider, config_key)
    except IntegrationConfigKeyError:
        effective = None
    configured = bool(effective and str(effective).strip())
    if is_secret:
        return {
            "configured": configured,
            "enabled": enabled,
            "status": status,
        }
    return {
        "value": "" if effective is None else str(effective),
        "configured": configured,
        "enabled": enabled,
        "status": status,
    }


def _provider_block(
    *,
    overlay: IntegrationOverlay,
    settings: Any,
    provider: str,
    keys: dict[str, bool],
) -> dict[str, Any]:
    block: dict[str, Any] = {}
    for config_key, secret in keys.items():
        block[config_key] = _field_view(
            overlay=overlay,
            settings=settings,
            provider=provider,
            config_key=config_key,
            is_secret=secret,
        )
    return block


async def get_integrations_summary(
    session: AsyncSession,
    *,
    settings: Any | None = None,
) -> dict[str, Any]:
    settings_obj = _settings_or_default(settings)
    overlay = await load_integration_overlay(session)
    meta = config_update_metadata()
    dify = _provider_block(
        overlay=overlay,
        settings=settings_obj,
        provider=INTEGRATION_PROVIDER_DIFY,
        keys=DIFY_CONFIG_KEYS,
    )
    dify["live_enabled_env"] = bool(
        getattr(settings_obj, "dify_interview_question_live_enabled", False)
    )
    minio = _provider_block(
        overlay=overlay,
        settings=settings_obj,
        provider=INTEGRATION_PROVIDER_MINIO,
        keys=MINIO_CONFIG_KEYS,
    )
    return {
        "dify": dify,
        "minio": minio,
        "mail": resolve_mail_block(settings_obj),
        "restart_required": meta["restart_required"],
        "message_key": meta["message_key"],
    }


def _reject_forbidden_keys(payload: dict[str, Any]) -> None:
    for key in payload:
        lowered = str(key).lower()
        if key in _FORBIDDEN_UPDATE_KEYS or lowered in {
            k.lower() for k in _FORBIDDEN_UPDATE_KEYS
        }:
            raise IntegrationValidationError(f"forbidden integration field: {key}")
        if lowered.startswith("smtp_"):
            raise IntegrationValidationError(f"forbidden smtp field: {key}")
        if "live" in lowered and key not in DIFY_CONFIG_KEYS and key not in MINIO_CONFIG_KEYS:
            raise IntegrationValidationError(f"live switch is not writable: {key}")


def _validate_value(provider: str, config_key: str, value: str) -> None:
    if provider == INTEGRATION_PROVIDER_DIFY and config_key == "ai_provider":
        if value not in AI_PROVIDER_ALLOWED:
            raise IntegrationValidationError(
                "ai_provider must be 'mock' or 'dify'"
            )
    if provider == INTEGRATION_PROVIDER_MINIO and config_key == "presign_seconds":
        try:
            seconds = int(str(value).strip())
        except ValueError as exc:
            raise IntegrationValidationError(
                "presign_seconds must be a positive integer"
            ) from exc
        if seconds < 1 or seconds > MINIO_PRESIGN_MAX:
            raise IntegrationValidationError(
                f"presign_seconds must be between 1 and {MINIO_PRESIGN_MAX}"
            )
    if provider == INTEGRATION_PROVIDER_MINIO and config_key == "secure":
        if str(value).strip().lower() not in {"true", "false", "1", "0"}:
            raise IntegrationValidationError("secure must be true/false")


async def _apply_partial_update(
    session: AsyncSession,
    *,
    provider: str,
    keys: dict[str, bool],
    payload: dict[str, Any],
    actor_user_id: UUID | None,
) -> tuple[list[str], list[str]]:
    _reject_forbidden_keys(payload)
    updated_keys: list[str] = []
    secret_keys_updated: list[str] = []

    for config_key, raw in payload.items():
        if config_key == "enabled":
            # optional map of key -> bool
            if not isinstance(raw, dict):
                raise IntegrationValidationError("enabled must be a mapping")
            for ek, ev in raw.items():
                validate_integration_config_key(provider, str(ek))
                existing = await secrets_repo.get_integration_secret(
                    session, provider=provider, config_key=str(ek)
                )
                if existing is None:
                    raise IntegrationValidationError(
                        f"cannot set enabled without existing row: {ek}"
                    )
                existing.enabled = bool(ev)
                updated_keys.append(str(ek))
            continue

        if config_key not in keys:
            raise IntegrationValidationError(
                f"unknown integration config key: {provider}.{config_key}"
            )
        validate_integration_config_key(provider, config_key)
        secret = keys[config_key]

        if raw is None:
            continue
        if not isinstance(raw, (str, int, bool)):
            raise IntegrationValidationError(
                f"invalid value type for {config_key}"
            )
        text = raw if isinstance(raw, str) else (
            "true" if raw is True else "false" if raw is False else str(raw)
        )
        if secret and text.strip() == "":
            # omit / empty secret → keep previous ciphertext
            continue

        _validate_value(provider, config_key, text)
        try:
            cipher = encrypt_secret(text)
        except EncryptionError as exc:
            raise IntegrationValidationError("encryption failed") from exc
        if cipher is None:
            # non-secret empty after strip — skip
            continue

        await secrets_repo.upsert_integration_secret(
            session,
            provider=provider,
            config_key=config_key,
            value_encrypted=cipher,
            is_secret=secret,
            enabled=True,
            updated_by=actor_user_id,
        )
        updated_keys.append(config_key)
        if secret:
            secret_keys_updated.append(config_key)

    return updated_keys, secret_keys_updated


async def update_dify(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    actor_user_id: UUID | None,
    request_context: RequestContext,
    settings: Any | None = None,
) -> dict[str, Any]:
    updated_keys, secret_keys_updated = await _apply_partial_update(
        session,
        provider=INTEGRATION_PROVIDER_DIFY,
        keys=DIFY_CONFIG_KEYS,
        payload=payload,
        actor_user_id=actor_user_id,
    )
    await record_audit(
        session,
        action=AUDIT_CONFIG_UPDATED,
        result="success",
        resource_type="integration",
        request_context=request_context,
        actor_user_id=actor_user_id,
        resource_id="dify",
        changes={
            "provider": "dify",
            "updated_keys": updated_keys,
            "secret_keys_updated": secret_keys_updated,
            "configured": True,
            "updated": True,
        },
    )
    await session.commit()
    summary = await get_integrations_summary(session, settings=settings)
    summary.update(config_update_metadata())
    return summary


async def update_minio(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    actor_user_id: UUID | None,
    request_context: RequestContext,
    settings: Any | None = None,
) -> dict[str, Any]:
    updated_keys, secret_keys_updated = await _apply_partial_update(
        session,
        provider=INTEGRATION_PROVIDER_MINIO,
        keys=MINIO_CONFIG_KEYS,
        payload=payload,
        actor_user_id=actor_user_id,
    )
    await record_audit(
        session,
        action=AUDIT_CONFIG_UPDATED,
        result="success",
        resource_type="integration",
        request_context=request_context,
        actor_user_id=actor_user_id,
        resource_id="minio",
        changes={
            "provider": "minio",
            "updated_keys": updated_keys,
            "secret_keys_updated": secret_keys_updated,
            "configured": True,
            "updated": True,
        },
    )
    await session.commit()
    summary = await get_integrations_summary(session, settings=settings)
    summary.update(config_update_metadata())
    return summary


async def update_mail(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    actor_user_id: UUID | None,
    request_context: RequestContext,
    settings: Any | None = None,
) -> dict[str, Any]:
    raise IntegrationMailWriteError(
        "mail integration is read-only (console only; no SMTP)"
    )


async def _audit_connectivity(
    session: AsyncSession,
    *,
    provider: str,
    result: ConnectivityResult,
    actor_user_id: UUID | None,
    request_context: RequestContext,
) -> None:
    await record_audit(
        session,
        action=AUDIT_CONNECTIVITY_TESTED,
        result="success" if result.ok else "failure",
        resource_type="integration",
        request_context=request_context,
        actor_user_id=actor_user_id,
        resource_id=provider,
        changes={
            "provider": provider,
            "ok": result.ok,
            "error_code": result.error_code,
            "latency_ms": result.latency_ms,
        },
    )


async def test_dify(
    session: AsyncSession,
    *,
    actor_user_id: UUID | None,
    request_context: RequestContext,
    settings: Any | None = None,
    http_client: Any | None = None,
    timeout_seconds: float = 5.0,
) -> ConnectivityResult:
    settings_obj = _settings_or_default(settings)
    overlay = await load_integration_overlay(session)
    base = resolve_config_value(
        overlay, settings_obj, INTEGRATION_PROVIDER_DIFY, "api_base_url"
    ) or ""
    key = resolve_config_value(
        overlay, settings_obj, INTEGRATION_PROVIDER_DIFY, "api_key"
    ) or ""
    result = await probe_dify(
        api_base_url=base,
        api_key=key,
        http_client=http_client,
        timeout_seconds=timeout_seconds,
    )
    await _audit_connectivity(
        session,
        provider="dify",
        result=result,
        actor_user_id=actor_user_id,
        request_context=request_context,
    )
    return result


async def test_minio(
    session: AsyncSession,
    *,
    actor_user_id: UUID | None,
    request_context: RequestContext,
    settings: Any | None = None,
    minio_client: Any | None = None,
) -> ConnectivityResult:
    settings_obj = _settings_or_default(settings)
    overlay = await load_integration_overlay(session)
    endpoint = resolve_config_value(
        overlay, settings_obj, INTEGRATION_PROVIDER_MINIO, "endpoint"
    ) or ""
    access_key = resolve_config_value(
        overlay, settings_obj, INTEGRATION_PROVIDER_MINIO, "access_key"
    ) or ""
    secret_key = resolve_config_value(
        overlay, settings_obj, INTEGRATION_PROVIDER_MINIO, "secret_key"
    ) or ""
    bucket = resolve_config_value(
        overlay, settings_obj, INTEGRATION_PROVIDER_MINIO, "bucket"
    ) or ""
    secure_raw = resolve_config_value(
        overlay, settings_obj, INTEGRATION_PROVIDER_MINIO, "secure"
    ) or "false"
    secure = str(secure_raw).strip().lower() in {"true", "1", "yes"}
    result = probe_minio(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
        secure=secure,
        minio_client=minio_client,
    )
    await _audit_connectivity(
        session,
        provider="minio",
        result=result,
        actor_user_id=actor_user_id,
        request_context=request_context,
    )
    return result


async def test_mail(
    session: AsyncSession,
    *,
    actor_user_id: UUID | None,
    request_context: RequestContext,
    settings: Any | None = None,
) -> ConnectivityResult:
    result = probe_mail_console()
    await _audit_connectivity(
        session,
        provider="mail",
        result=result,
        actor_user_id=actor_user_id,
        request_context=request_context,
    )
    return result
