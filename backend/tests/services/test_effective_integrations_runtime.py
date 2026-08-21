"""Runtime effective integrations: process overlay → Dify/MinIO (TDD)."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from app.core.config import get_settings
from app.services.crypto import encrypt_secret

BACKEND_APP = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    key = Fernet.generate_key()
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", key.decode("ascii"))
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_process_overlay():
    from app.services import integration_config as cfg

    cfg.set_process_overlay(cfg.IntegrationOverlay.empty())
    yield
    cfg.set_process_overlay(cfg.IntegrationOverlay.empty())
    try:
        from app.integrations.minio_storage import get_minio_client

        get_minio_client.cache_clear()
    except Exception:
        pass


def _overlay_entry(
    *,
    provider: str,
    config_key: str,
    plain: str,
    enabled: bool = True,
    is_secret: bool = False,
):
    from app.services.integration_config import materialize_overlay_entry

    cipher = encrypt_secret(plain)
    assert cipher is not None
    return materialize_overlay_entry(
        provider=provider,
        config_key=config_key,
        value_encrypted=cipher,
        enabled=enabled,
        is_secret=is_secret,
    )


def test_effective_dify_prefers_enabled_process_overlay(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services.integration_config import (
        IntegrationOverlay,
        effective_dify_api_base_url,
        effective_dify_api_key,
        set_process_overlay,
    )

    monkeypatch.setenv("DIFY_API_BASE_URL", "https://env.dify.example/v1")
    monkeypatch.setenv("DIFY_API_KEY", "env-dify-key")
    get_settings.cache_clear()

    set_process_overlay(
        IntegrationOverlay(
            entries={
                ("dify", "api_base_url"): _overlay_entry(
                    provider="dify",
                    config_key="api_base_url",
                    plain="https://db.dify.example/v1",
                ),
                ("dify", "api_key"): _overlay_entry(
                    provider="dify",
                    config_key="api_key",
                    plain="db-dify-key",
                    is_secret=True,
                ),
            }
        )
    )
    assert effective_dify_api_base_url() == "https://db.dify.example/v1"
    assert effective_dify_api_key() == "db-dify-key"
    assert get_settings().DIFY_API_BASE_URL == "https://env.dify.example/v1"
    assert get_settings().dify_api_key == "env-dify-key"


def test_effective_falls_back_to_env_when_overlay_empty(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services.integration_config import (
        IntegrationOverlay,
        effective_dify_api_base_url,
        effective_minio_bucket,
        set_process_overlay,
    )

    monkeypatch.setenv("DIFY_API_BASE_URL", "https://env-only.example")
    monkeypatch.setenv("MINIO_BUCKET", "env-bucket")
    get_settings.cache_clear()
    set_process_overlay(IntegrationOverlay.empty())
    assert effective_dify_api_base_url() == "https://env-only.example"
    assert effective_minio_bucket() == "env-bucket"


def test_disabled_decrypt_error_unknown_do_not_override(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services.integration_config import (
        OVERLAY_STATUS_DECRYPT_ERROR,
        OVERLAY_STATUS_DISABLED,
        OVERLAY_STATUS_UNKNOWN_KEY,
        IntegrationOverlay,
        effective_dify_api_key,
        materialize_overlay_entry,
        set_process_overlay,
    )

    monkeypatch.setenv("DIFY_API_KEY", "env-fallback-key")
    get_settings.cache_clear()

    disabled = _overlay_entry(
        provider="dify",
        config_key="api_key",
        plain="should-not-apply",
        enabled=False,
        is_secret=True,
    )
    assert disabled.status == OVERLAY_STATUS_DISABLED

    bad = materialize_overlay_entry(
        provider="dify",
        config_key="api_key",
        value_encrypted="enc:v1:not-valid-token====",
        enabled=True,
        is_secret=True,
    )
    assert bad.status == OVERLAY_STATUS_DECRYPT_ERROR

    unknown = materialize_overlay_entry(
        provider="dify",
        config_key="smtp_password",
        value_encrypted=encrypt_secret("x") or "",
        enabled=True,
        is_secret=True,
    )
    assert unknown.status == OVERLAY_STATUS_UNKNOWN_KEY

    set_process_overlay(IntegrationOverlay(entries={("dify", "api_key"): disabled}))
    assert effective_dify_api_key() == "env-fallback-key"

    set_process_overlay(IntegrationOverlay(entries={("dify", "api_key"): bad}))
    assert effective_dify_api_key() == "env-fallback-key"


@pytest.mark.asyncio
async def test_dify_post_workflow_uses_effective_overlay(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.models.ai_task import TASK_TYPE_JD_PARSE
    from app.services.ai_providers import dify as dify_mod
    from app.services.integration_config import IntegrationOverlay, set_process_overlay

    monkeypatch.setenv("DIFY_API_BASE_URL", "https://env.example")
    monkeypatch.setenv("DIFY_API_KEY", "env-key")
    monkeypatch.setenv("DIFY_JD_PARSE_API_KEY", "env-jd-key")
    monkeypatch.setenv("DIFY_JD_PARSE_WORKFLOW_ID", "env-wf")
    get_settings.cache_clear()

    set_process_overlay(
        IntegrationOverlay(
            entries={
                ("dify", "api_base_url"): _overlay_entry(
                    provider="dify",
                    config_key="api_base_url",
                    plain="https://overlay.example",
                ),
                ("dify", "jd_parse_api_key"): _overlay_entry(
                    provider="dify",
                    config_key="jd_parse_api_key",
                    plain="overlay-jd-key",
                    is_secret=True,
                ),
                ("dify", "jd_parse_workflow_id"): _overlay_entry(
                    provider="dify",
                    config_key="jd_parse_workflow_id",
                    plain="overlay-wf",
                ),
            }
        )
    )

    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "data": {
                    "outputs": {
                        "result": {
                            "structured_jd": {"title": "t"},
                            "dimensions": [],
                        }
                    }
                }
            }

        @property
        def text(self):
            return "{}"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, headers=None, json=None, **kwargs):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(dify_mod.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        dify_mod, "build_dify_inputs", lambda task_type, snap: {"raw_jd_text": "x"}
    )

    await dify_mod._post_workflow(
        task_type=TASK_TYPE_JD_PARSE,
        input_snapshot={"raw_jd_text": "hello"},
    )
    assert captured["url"].startswith("https://overlay.example/")
    assert captured["headers"]["Authorization"] == "Bearer overlay-jd-key"
    assert captured["json"].get("workflow_id") == "overlay-wf"
    assert "env.example" not in captured["url"]
    assert "env-jd-key" not in captured["headers"]["Authorization"]


def test_minio_client_uses_effective_overlay(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.integrations import minio_storage
    from app.services.integration_config import IntegrationOverlay, set_process_overlay

    monkeypatch.setenv("MINIO_ENDPOINT", "127.0.0.1:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "env-ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "env-sk")
    monkeypatch.setenv("MINIO_BUCKET", "env-bucket")
    monkeypatch.setenv("MINIO_SECURE", "false")
    get_settings.cache_clear()
    minio_storage.get_minio_client.cache_clear()

    set_process_overlay(
        IntegrationOverlay(
            entries={
                ("minio", "endpoint"): _overlay_entry(
                    provider="minio", config_key="endpoint", plain="10.0.0.9:9000"
                ),
                ("minio", "access_key"): _overlay_entry(
                    provider="minio",
                    config_key="access_key",
                    plain="db-ak",
                    is_secret=True,
                ),
                ("minio", "secret_key"): _overlay_entry(
                    provider="minio",
                    config_key="secret_key",
                    plain="db-sk",
                    is_secret=True,
                ),
                ("minio", "bucket"): _overlay_entry(
                    provider="minio", config_key="bucket", plain="db-bucket"
                ),
                ("minio", "secure"): _overlay_entry(
                    provider="minio", config_key="secure", plain="true"
                ),
            }
        )
    )
    minio_storage.get_minio_client.cache_clear()

    constructed: dict = {}

    class FakeMinio:
        def __init__(self, endpoint, access_key, secret_key, secure=False):
            constructed.update(
                {
                    "endpoint": endpoint,
                    "access_key": access_key,
                    "secret_key": secret_key,
                    "secure": secure,
                }
            )

    monkeypatch.setattr(minio_storage, "Minio", FakeMinio)
    assert minio_storage.get_minio_client() is not None
    assert constructed["endpoint"] == "10.0.0.9:9000"
    assert constructed["access_key"] == "db-ak"
    assert constructed["secret_key"] == "db-sk"
    assert constructed["secure"] is True
    assert minio_storage.effective_bucket_name() == "db-bucket"


@pytest.mark.asyncio
async def test_bootstrap_integration_overlay_loads_process_snapshot(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services import integration_config as cfg

    async def fake_load(session):
        return cfg.IntegrationOverlay(
            entries={
                ("dify", "api_key"): _overlay_entry(
                    provider="dify",
                    config_key="api_key",
                    plain="boot-key",
                    is_secret=True,
                )
            }
        )

    monkeypatch.setattr(cfg, "load_integration_overlay", fake_load)

    class FakeFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *a):
            return None

    await cfg.bootstrap_integration_overlay(session_factory=FakeFactory())
    assert cfg.get_process_overlay().effective_plain("dify", "api_key") == "boot-key"


@pytest.mark.asyncio
async def test_bootstrap_failure_degrades_to_empty_overlay(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, fernet_key: bytes
) -> None:
    from app.services import integration_config as cfg

    async def boom(session):
        raise RuntimeError("db down secret=should-not-log")

    monkeypatch.setattr(cfg, "load_integration_overlay", boom)

    class FakeFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *a):
            return None

    with caplog.at_level(logging.WARNING):
        await cfg.bootstrap_integration_overlay(session_factory=FakeFactory())
    assert cfg.get_process_overlay().entries == {}
    rendered = " ".join(r.getMessage() for r in caplog.records)
    assert "should-not-log" not in rendered
    assert "secret=" not in rendered


def test_lifespan_and_worker_wire_bootstrap() -> None:
    main_text = (BACKEND_APP / "main.py").read_text(encoding="utf-8")
    assert "bootstrap_integration_overlay" in main_text

    celery_text = (BACKEND_APP / "workers" / "celery_app.py").read_text(
        encoding="utf-8"
    )
    assert "worker_process_init" in celery_text
    assert "bootstrap_integration_overlay" in celery_text

    ai_text = (BACKEND_APP / "workers" / "ai_tasks.py").read_text(encoding="utf-8")
    assert "effective_ai_provider" in ai_text

    dify_text = (BACKEND_APP / "services" / "ai_providers" / "dify.py").read_text(
        encoding="utf-8"
    )
    assert "effective_dify_api_base_url" in dify_text
    assert "effective_dify_api_key_for" in dify_text

    minio_text = (BACKEND_APP / "integrations" / "minio_storage.py").read_text(
        encoding="utf-8"
    )
    assert "effective_minio_" in minio_text or "effective_bucket_name" in minio_text


def test_effective_ai_provider_from_overlay(
    monkeypatch: pytest.MonkeyPatch, fernet_key: bytes
) -> None:
    from app.services.integration_config import (
        IntegrationOverlay,
        effective_ai_provider,
        set_process_overlay,
    )

    monkeypatch.setenv("AI_PROVIDER", "mock")
    get_settings.cache_clear()
    set_process_overlay(
        IntegrationOverlay(
            entries={
                ("dify", "ai_provider"): _overlay_entry(
                    provider="dify", config_key="ai_provider", plain="dify"
                )
            }
        )
    )
    assert effective_ai_provider() == "dify"
