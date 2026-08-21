"""Effective integration config: Settings env baseline ← enabled DB overlay.

Internal service-only view. Does not mutate ``get_settings()`` cache, write ``.env``,
or hot-reload processes. PUT callers should surface ``config_update_metadata()``
(``restart_required=true``); this module never restarts anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration_secret import (
    DIFY_CONFIG_KEYS,
    INTEGRATION_PROVIDER_DIFY,
    INTEGRATION_PROVIDER_MINIO,
    INTEGRATION_PROVIDERS_STORABLE,
    MINIO_CONFIG_KEYS,
    ROOT_SECRET_ENV_NAMES,
    IntegrationConfigKeyError,
    IntegrationSecret,
    validate_integration_config_key,
)
from app.repositories import integration_secrets as secrets_repo
from app.services.crypto import EncryptionError, decrypt_secret
from app.core.config import get_settings

logger = logging.getLogger(__name__)

OVERLAY_STATUS_OK = "ok"
OVERLAY_STATUS_DISABLED = "disabled"
OVERLAY_STATUS_EMPTY_CIPHERTEXT = "empty_ciphertext"
OVERLAY_STATUS_DECRYPT_ERROR = "decrypt_error"
OVERLAY_STATUS_UNKNOWN_KEY = "unknown_key"
OVERLAY_STATUS_FORBIDDEN_ROOT = "forbidden_root"

RESTART_REQUIRED = True
INTEGRATIONS_RESTART_REQUIRED_MESSAGE_KEY = "integrations.restart_required"

# Settings attribute / property accessors for whitelist keys (string view).
_SETTINGS_ACCESSORS: dict[tuple[str, str], str] = {
    (INTEGRATION_PROVIDER_DIFY, "api_base_url"): "DIFY_API_BASE_URL",
    (INTEGRATION_PROVIDER_DIFY, "api_key"): "dify_api_key",
    (INTEGRATION_PROVIDER_DIFY, "jd_parse_api_key"): "dify_jd_parse_api_key_secret",
    (INTEGRATION_PROVIDER_DIFY, "score_dimension_api_key"): (
        "dify_score_dimension_api_key_secret"
    ),
    (INTEGRATION_PROVIDER_DIFY, "jd_parse_workflow_id"): "DIFY_JD_PARSE_WORKFLOW_ID",
    (INTEGRATION_PROVIDER_DIFY, "score_dimension_workflow_id"): (
        "DIFY_SCORE_DIMENSION_WORKFLOW_ID"
    ),
    (INTEGRATION_PROVIDER_DIFY, "resume_parse_api_key"): (
        "dify_resume_parse_api_key_secret"
    ),
    (INTEGRATION_PROVIDER_DIFY, "resume_score_api_key"): (
        "dify_resume_score_api_key_secret"
    ),
    (INTEGRATION_PROVIDER_DIFY, "resume_parse_workflow_id"): (
        "DIFY_RESUME_PARSE_WORKFLOW_ID"
    ),
    (INTEGRATION_PROVIDER_DIFY, "resume_score_workflow_id"): (
        "DIFY_RESUME_SCORE_WORKFLOW_ID"
    ),
    (INTEGRATION_PROVIDER_DIFY, "interview_question_generate_api_key"): (
        "dify_interview_question_generate_api_key_secret"
    ),
    (INTEGRATION_PROVIDER_DIFY, "interview_question_generate_workflow_id"): (
        "dify_interview_question_generate_workflow_id"
    ),
    (INTEGRATION_PROVIDER_DIFY, "ai_provider"): "AI_PROVIDER",
    (INTEGRATION_PROVIDER_MINIO, "endpoint"): "MINIO_ENDPOINT",
    (INTEGRATION_PROVIDER_MINIO, "access_key"): "minio_access_key",
    (INTEGRATION_PROVIDER_MINIO, "secret_key"): "minio_secret_key",
    (INTEGRATION_PROVIDER_MINIO, "bucket"): "MINIO_BUCKET",
    (INTEGRATION_PROVIDER_MINIO, "secure"): "MINIO_SECURE",
    (INTEGRATION_PROVIDER_MINIO, "presign_seconds"): "MINIO_PRESIGN_SECONDS",
}


@dataclass(frozen=True)
class OverlayEntry:
    provider: str
    config_key: str
    enabled: bool
    is_secret: bool
    status: str
    plain_value: str | None = None

    def as_overlay(self) -> IntegrationOverlay:
        return IntegrationOverlay(
            entries={(self.provider, self.config_key): self}
        )


@dataclass
class IntegrationOverlay:
    """Process-local snapshot of decrypted DB overlays (service-internal)."""

    entries: dict[tuple[str, str], OverlayEntry] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> IntegrationOverlay:
        return cls(entries={})

    def effective_plain(self, provider: str, config_key: str) -> str | None:
        entry = self.entries.get((provider, config_key))
        if entry is None:
            return None
        if entry.status != OVERLAY_STATUS_OK or not entry.enabled:
            return None
        if entry.plain_value is None or entry.plain_value == "":
            return None
        return entry.plain_value


def assert_not_root_secret(config_key_or_env: str) -> None:
    name = (config_key_or_env or "").strip()
    if name in ROOT_SECRET_ENV_NAMES or name.upper() in ROOT_SECRET_ENV_NAMES:
        raise IntegrationConfigKeyError(
            f"root secret key is forbidden as config_key: {name}"
        )


def config_update_metadata() -> dict[str, Any]:
    """Stable metadata for config-change responses (no process restart)."""
    return {
        "restart_required": RESTART_REQUIRED,
        "message_key": INTEGRATIONS_RESTART_REQUIRED_MESSAGE_KEY,
    }


def resolve_mail_block(settings: Any) -> dict[str, Any]:
    """Read-only mail view: Console only; queue from Settings."""
    queue = getattr(settings, "celery_mail_queue_name", "mail_outbound") or (
        "mail_outbound"
    )
    return {
        "delivery_provider": "console",
        "queue_name": str(queue),
        "smtp_enabled": False,
        "note": "一期仅 Console，无 SMTP",
    }


def materialize_overlay_entry(
    *,
    provider: str,
    config_key: str,
    value_encrypted: str | None,
    enabled: bool,
    is_secret: bool,
) -> OverlayEntry:
    """Classify one DB row into an overlay entry (decrypt when applicable).

    Never raises with ciphertext/plain in the exception message.
    """
    if config_key in ROOT_SECRET_ENV_NAMES:
        logger.warning(
            "integration overlay skipped: forbidden root key provider=%s key=%s",
            provider,
            config_key,
        )
        return OverlayEntry(
            provider=provider,
            config_key=config_key,
            enabled=enabled,
            is_secret=is_secret,
            status=OVERLAY_STATUS_FORBIDDEN_ROOT,
            plain_value=None,
        )

    try:
        validate_integration_config_key(provider, config_key)
    except IntegrationConfigKeyError:
        logger.warning(
            "integration overlay skipped: unknown key provider=%s key=%s",
            provider,
            config_key,
        )
        return OverlayEntry(
            provider=provider,
            config_key=config_key,
            enabled=enabled,
            is_secret=is_secret,
            status=OVERLAY_STATUS_UNKNOWN_KEY,
            plain_value=None,
        )

    if not enabled:
        return OverlayEntry(
            provider=provider,
            config_key=config_key,
            enabled=False,
            is_secret=is_secret,
            status=OVERLAY_STATUS_DISABLED,
            plain_value=None,
        )

    cipher = value_encrypted if isinstance(value_encrypted, str) else ""
    if cipher == "":
        logger.warning(
            "integration overlay skipped: empty ciphertext provider=%s key=%s",
            provider,
            config_key,
        )
        return OverlayEntry(
            provider=provider,
            config_key=config_key,
            enabled=True,
            is_secret=is_secret,
            status=OVERLAY_STATUS_EMPTY_CIPHERTEXT,
            plain_value=None,
        )

    try:
        plain = decrypt_secret(cipher)
    except EncryptionError:
        logger.warning(
            "integration overlay skipped: decrypt_error provider=%s key=%s",
            provider,
            config_key,
        )
        return OverlayEntry(
            provider=provider,
            config_key=config_key,
            enabled=True,
            is_secret=is_secret,
            status=OVERLAY_STATUS_DECRYPT_ERROR,
            plain_value=None,
        )

    if plain is None or plain == "":
        return OverlayEntry(
            provider=provider,
            config_key=config_key,
            enabled=True,
            is_secret=is_secret,
            status=OVERLAY_STATUS_EMPTY_CIPHERTEXT,
            plain_value=None,
        )

    return OverlayEntry(
        provider=provider,
        config_key=config_key,
        enabled=True,
        is_secret=is_secret,
        status=OVERLAY_STATUS_OK,
        plain_value=plain,
    )


def materialize_overlay_from_rows(
    rows: list[IntegrationSecret],
) -> IntegrationOverlay:
    entries: dict[tuple[str, str], OverlayEntry] = {}
    for row in rows:
        entry = materialize_overlay_entry(
            provider=row.provider,
            config_key=row.config_key,
            value_encrypted=row.value_encrypted,
            enabled=bool(row.enabled),
            is_secret=bool(row.is_secret),
        )
        entries[(entry.provider, entry.config_key)] = entry
    return IntegrationOverlay(entries=entries)


async def load_integration_overlay(session: AsyncSession) -> IntegrationOverlay:
    rows = await secrets_repo.list_integration_secrets(session)
    return materialize_overlay_from_rows(rows)


def _settings_baseline(settings: Any, provider: str, config_key: str) -> str | None:
    attr = _SETTINGS_ACCESSORS.get((provider, config_key))
    if attr is None:
        return None
    value = getattr(settings, attr, None)
    if value is None:
        return None
    # SecretStr-like
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        raw = getter()
        return "" if raw is None else str(raw)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return str(value)


def resolve_config_value(
    overlay: IntegrationOverlay,
    settings: Any,
    provider: str,
    config_key: str,
) -> str | None:
    """Resolve one whitelist key: enabled DB plain > Settings/env baseline."""
    assert_not_root_secret(config_key)
    validate_integration_config_key(provider, config_key)

    override = overlay.effective_plain(provider, config_key)
    if override is not None:
        return override
    return _settings_baseline(settings, provider, config_key)


def resolve_nonsecret(
    overlay: IntegrationOverlay,
    settings: Any,
    provider: str,
    config_key: str,
) -> str | None:
    keys = (
        DIFY_CONFIG_KEYS
        if provider == INTEGRATION_PROVIDER_DIFY
        else MINIO_CONFIG_KEYS
        if provider == INTEGRATION_PROVIDER_MINIO
        else None
    )
    if keys is None or config_key not in keys or keys[config_key]:
        raise IntegrationConfigKeyError(
            f"not a non-secret integration key: {provider}.{config_key}"
        )
    return resolve_config_value(overlay, settings, provider, config_key)


def resolve_secret(
    overlay: IntegrationOverlay,
    settings: Any,
    provider: str,
    config_key: str,
) -> str | None:
    keys = (
        DIFY_CONFIG_KEYS
        if provider == INTEGRATION_PROVIDER_DIFY
        else MINIO_CONFIG_KEYS
        if provider == INTEGRATION_PROVIDER_MINIO
        else None
    )
    if keys is None or config_key not in keys or not keys[config_key]:
        raise IntegrationConfigKeyError(
            f"not a secret integration key: {provider}.{config_key}"
        )
    return resolve_config_value(overlay, settings, provider, config_key)


# Optional process snapshot — never written back into Settings / get_settings cache.
_process_overlay: IntegrationOverlay = IntegrationOverlay.empty()


def get_process_overlay() -> IntegrationOverlay:
    return _process_overlay


async def warm_integration_overlay(session: AsyncSession) -> IntegrationOverlay:
    """Load DB overlay into process snapshot (call at startup; no hot reload)."""
    overlay = await load_integration_overlay(session)
    set_process_overlay(overlay)
    return overlay


def _settings_obj(settings: Any | None = None) -> Any:
    return settings if settings is not None else get_settings()


def _effective(provider: str, config_key: str, *, settings: Any | None = None) -> str:
    raw = resolve_config_value(
        get_process_overlay(),
        _settings_obj(settings),
        provider,
        config_key,
    )
    return "" if raw is None else str(raw)


def effective_ai_provider(*, settings: Any | None = None) -> str:
    value = _effective(
        INTEGRATION_PROVIDER_DIFY, "ai_provider", settings=settings
    ).strip().lower()
    return value or "mock"


def effective_dify_api_base_url(*, settings: Any | None = None) -> str:
    return _effective(
        INTEGRATION_PROVIDER_DIFY, "api_base_url", settings=settings
    ).strip()


def effective_dify_api_key(*, settings: Any | None = None) -> str:
    return _effective(
        INTEGRATION_PROVIDER_DIFY, "api_key", settings=settings
    ).strip()


def effective_dify_api_key_for(task_type: str, *, settings: Any | None = None) -> str:
    """Mirror Settings.dify_api_key_for with process overlay priority."""
    from app.models.ai_task import (
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        TASK_TYPE_JD_PARSE,
        TASK_TYPE_RESUME_PARSE,
        TASK_TYPE_RESUME_SCORE,
        TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
    )

    key_map = {
        TASK_TYPE_JD_PARSE: "jd_parse_api_key",
        TASK_TYPE_SCORE_DIMENSION_RECOMMEND: "score_dimension_api_key",
        TASK_TYPE_RESUME_PARSE: "resume_parse_api_key",
        TASK_TYPE_RESUME_SCORE: "resume_score_api_key",
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE: "interview_question_generate_api_key",
    }
    config_key = key_map.get(task_type)
    if config_key is None:
        return effective_dify_api_key(settings=settings)
    specific = _effective(
        INTEGRATION_PROVIDER_DIFY, config_key, settings=settings
    ).strip()
    if task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        return specific
    return specific or effective_dify_api_key(settings=settings)


def effective_dify_workflow_id(task_type: str, *, settings: Any | None = None) -> str:
    from app.models.ai_task import (
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        TASK_TYPE_JD_PARSE,
        TASK_TYPE_RESUME_PARSE,
        TASK_TYPE_RESUME_SCORE,
        TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
    )

    key_map = {
        TASK_TYPE_JD_PARSE: "jd_parse_workflow_id",
        TASK_TYPE_SCORE_DIMENSION_RECOMMEND: "score_dimension_workflow_id",
        TASK_TYPE_RESUME_PARSE: "resume_parse_workflow_id",
        TASK_TYPE_RESUME_SCORE: "resume_score_workflow_id",
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE: (
            "interview_question_generate_workflow_id"
        ),
    }
    config_key = key_map.get(task_type)
    if config_key is None:
        return ""
    return _effective(
        INTEGRATION_PROVIDER_DIFY, config_key, settings=settings
    ).strip()


def effective_minio_endpoint(*, settings: Any | None = None) -> str:
    return _effective(
        INTEGRATION_PROVIDER_MINIO, "endpoint", settings=settings
    ).strip()


def effective_minio_access_key(*, settings: Any | None = None) -> str:
    return _effective(
        INTEGRATION_PROVIDER_MINIO, "access_key", settings=settings
    ).strip()


def effective_minio_secret_key(*, settings: Any | None = None) -> str:
    return _effective(
        INTEGRATION_PROVIDER_MINIO, "secret_key", settings=settings
    ).strip()


def effective_minio_bucket(*, settings: Any | None = None) -> str:
    return _effective(
        INTEGRATION_PROVIDER_MINIO, "bucket", settings=settings
    ).strip()


def effective_minio_secure(*, settings: Any | None = None) -> bool:
    raw = _effective(
        INTEGRATION_PROVIDER_MINIO, "secure", settings=settings
    ).strip().lower()
    return raw in {"true", "1", "yes"}


def effective_minio_presign_seconds(*, settings: Any | None = None) -> int:
    raw = _effective(
        INTEGRATION_PROVIDER_MINIO, "presign_seconds", settings=settings
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        return int(getattr(_settings_obj(settings), "MINIO_PRESIGN_SECONDS", 600))
    return value


async def bootstrap_integration_overlay(
    *,
    session_factory: Any | None = None,
    database_url: str | None = None,
) -> IntegrationOverlay:
    """Load overlay once at process start. On failure → empty overlay (env baseline).

    Never clears ``get_settings()`` cache; never writes ``.env``; no hot reload.
    """
    from app.db.session import (
        create_database_engine,
        create_session_factory,
        dispose_database,
    )

    owns_engine = False
    engine = None
    try:
        if session_factory is None:
            url = database_url or get_settings().database_url
            engine = create_database_engine(url)
            session_factory = create_session_factory(engine)
            owns_engine = True
        async with session_factory() as session:
            overlay = await load_integration_overlay(session)
        set_process_overlay(overlay)
        return overlay
    except Exception:  # noqa: BLE001 — degrade to env; never log secrets
        logger.warning(
            "integration overlay bootstrap failed error_code=bootstrap_failed; "
            "using env baseline"
        )
        set_process_overlay(IntegrationOverlay.empty())
        return IntegrationOverlay.empty()
    finally:
        if owns_engine and engine is not None:
            await dispose_database(engine)


def bootstrap_integration_overlay_sync(
    *, database_url: str | None = None
) -> IntegrationOverlay:
    """Sync wrapper for Celery worker_process_init."""
    import asyncio

    return asyncio.run(
        bootstrap_integration_overlay(database_url=database_url)
    )


def set_process_overlay(overlay: IntegrationOverlay) -> None:
    global _process_overlay
    _process_overlay = overlay
    # MinIO client caches construction args; refresh after overlay swap (startup only).
    try:
        from app.integrations.minio_storage import get_minio_client

        get_minio_client.cache_clear()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "INTEGRATION_PROVIDERS_STORABLE",
    "INTEGRATIONS_RESTART_REQUIRED_MESSAGE_KEY",
    "OVERLAY_STATUS_DECRYPT_ERROR",
    "OVERLAY_STATUS_DISABLED",
    "OVERLAY_STATUS_EMPTY_CIPHERTEXT",
    "OVERLAY_STATUS_FORBIDDEN_ROOT",
    "OVERLAY_STATUS_OK",
    "OVERLAY_STATUS_UNKNOWN_KEY",
    "RESTART_REQUIRED",
    "IntegrationOverlay",
    "OverlayEntry",
    "assert_not_root_secret",
    "bootstrap_integration_overlay",
    "bootstrap_integration_overlay_sync",
    "config_update_metadata",
    "effective_ai_provider",
    "effective_dify_api_base_url",
    "effective_dify_api_key",
    "effective_dify_api_key_for",
    "effective_dify_workflow_id",
    "effective_minio_access_key",
    "effective_minio_bucket",
    "effective_minio_endpoint",
    "effective_minio_presign_seconds",
    "effective_minio_secret_key",
    "effective_minio_secure",
    "get_process_overlay",
    "load_integration_overlay",
    "materialize_overlay_entry",
    "materialize_overlay_from_rows",
    "resolve_config_value",
    "resolve_mail_block",
    "resolve_nonsecret",
    "resolve_secret",
    "set_process_overlay",
    "warm_integration_overlay",
]
