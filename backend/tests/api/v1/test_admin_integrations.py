"""Admin integrations API (Task 4 RED) — mock all Dify/MinIO calls."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, get_db_session
from app.main import app
from app.models import Permission, Role, User
from app.services.integration_connectivity import ConnectivityResult

PERM = "integration.manage"
BASE = "/api/v1/admin/integrations"
BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _user(*permission_codes: str) -> User:
    user = User(
        id=uuid4(),
        username="tester",
        username_normalized="tester",
        display_name="Tester",
        password_hash="x",
        is_active=True,
        must_change_password=False,
        token_version=1,
    )
    role = Role(name="role", description="role")
    role.permissions = [
        Permission(code=code, description=code) for code in permission_codes
    ]
    user.roles = [role]
    return user


def _client_for(user: User) -> TestClient:
    async def override_user() -> User:
        return user

    async def override_db():
        yield AsyncMock()

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_db_session] = override_db
    return TestClient(app)


@pytest.fixture
def lifespan_patches():
    with (
        patch("app.main.create_database_engine"),
        patch("app.main.create_session_factory"),
        patch("app.main.create_redis_client", return_value=AsyncMock()),
        patch("app.main.close_redis", new_callable=AsyncMock),
        patch("app.main.dispose_database", new_callable=AsyncMock),
    ):
        yield
    app.dependency_overrides.clear()


def _safe_summary(*, api_key_configured: bool = True) -> dict:
    return {
        "dify": {
            "api_base_url": {
                "value": "https://example.invalid/v1",
                "configured": True,
                "enabled": True,
                "status": "ok",
            },
            "api_key": {
                "configured": api_key_configured,
                "enabled": True,
                "status": "ok",
            },
            "jd_parse_api_key": {"configured": False, "enabled": True, "status": "ok"},
            "score_dimension_api_key": {
                "configured": False,
                "enabled": True,
                "status": "ok",
            },
            "jd_parse_workflow_id": {
                "value": "",
                "configured": False,
                "enabled": True,
                "status": "ok",
            },
            "score_dimension_workflow_id": {
                "value": "",
                "configured": False,
                "enabled": True,
                "status": "ok",
            },
            "resume_parse_api_key": {
                "configured": False,
                "enabled": True,
                "status": "ok",
            },
            "resume_score_api_key": {
                "configured": False,
                "enabled": True,
                "status": "ok",
            },
            "resume_parse_workflow_id": {
                "value": "",
                "configured": False,
                "enabled": True,
                "status": "ok",
            },
            "resume_score_workflow_id": {
                "value": "",
                "configured": False,
                "enabled": True,
                "status": "ok",
            },
            "interview_question_generate_api_key": {
                "configured": False,
                "enabled": True,
                "status": "ok",
            },
            "interview_question_generate_workflow_id": {
                "value": "",
                "configured": False,
                "enabled": True,
                "status": "ok",
            },
            "ai_provider": {
                "value": "mock",
                "configured": True,
                "enabled": True,
                "status": "ok",
            },
            "live_enabled_env": False,
        },
        "minio": {
            "endpoint": {
                "value": "127.0.0.1:9000",
                "configured": True,
                "enabled": True,
                "status": "ok",
            },
            "access_key": {"configured": True, "enabled": True, "status": "ok"},
            "secret_key": {"configured": True, "enabled": True, "status": "ok"},
            "bucket": {
                "value": "resumes",
                "configured": True,
                "enabled": True,
                "status": "ok",
            },
            "secure": {
                "value": "false",
                "configured": True,
                "enabled": True,
                "status": "ok",
            },
            "presign_seconds": {
                "value": "600",
                "configured": True,
                "enabled": True,
                "status": "ok",
            },
        },
        "mail": {
            "delivery_provider": "console",
            "queue_name": "mail_outbound",
            "smtp_enabled": False,
            "note": "一期仅 Console，无 SMTP",
        },
        "restart_required": True,
        "message_key": "integrations.restart_required",
    }


def _assert_no_secrets(payload: object) -> None:
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "enc:v1:" not in rendered
    assert "value_encrypted" not in rendered
    assert "Authorization" not in rendered
    assert "Bearer " not in rendered
    # must not echo typical plaintext secrets used in fixtures
    assert "super-secret-key" not in rendered
    assert "brand-new-secret" not in rendered
    assert "minioadmin-secret" not in rendered


def test_get_requires_integration_manage(lifespan_patches) -> None:
    with patch(
        "app.api.v1.endpoints.admin_integrations.get_integrations_summary",
        new_callable=AsyncMock,
        return_value=_safe_summary(),
    ):
        admin = _client_for(_user(PERM))
        r = admin.get(BASE)
        assert r.status_code == 200
        assert r.headers.get("Cache-Control") == "no-store"

        for codes in (
            ("recruitment.manage", "interview.execute"),
            ("audit.read",),
            ("interview.execute",),
            ("profile.read", "profile.change_password"),
        ):
            client = _client_for(_user(*codes))
            denied = client.get(BASE)
            assert denied.status_code == 403, codes


def test_get_never_returns_raw_secrets(lifespan_patches) -> None:
    with patch(
        "app.api.v1.endpoints.admin_integrations.get_integrations_summary",
        new_callable=AsyncMock,
        return_value=_safe_summary(),
    ):
        client = _client_for(_user(PERM))
        response = client.get(BASE)
        assert response.status_code == 200
        body = response.json()
        _assert_no_secrets(body)
        assert body["dify"]["api_key"]["configured"] is True
        assert "value" not in body["dify"]["api_key"]
        assert body["mail"]["delivery_provider"] == "console"
        assert body["mail"]["smtp_enabled"] is False


def test_put_dify_restart_required(lifespan_patches) -> None:
    summary = _safe_summary()
    summary["restart_required"] = True
    with patch(
        "app.api.v1.endpoints.admin_integrations.update_dify",
        new_callable=AsyncMock,
        return_value=summary,
    ) as mock_update:
        client = _client_for(_user(PERM))
        response = client.put(
            f"{BASE}/dify",
            json={"api_base_url": "https://new.example/v1", "api_key": ""},
        )
        assert response.status_code == 200
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.json()["restart_required"] is True
        mock_update.assert_awaited_once()
        _assert_no_secrets(response.json())


def test_put_extra_forbid_unknown_field(lifespan_patches) -> None:
    client = _client_for(_user(PERM))
    response = client.put(
        f"{BASE}/dify",
        json={"api_base_url": "https://x", "smtp_host": "evil"},
    )
    assert response.status_code == 422


def test_put_minio_and_get_configured(lifespan_patches) -> None:
    updated = _safe_summary()
    updated["minio"]["access_key"]["configured"] = True
    updated["minio"]["secret_key"]["configured"] = True
    with (
        patch(
            "app.api.v1.endpoints.admin_integrations.update_minio",
            new_callable=AsyncMock,
            return_value=updated,
        ),
        patch(
            "app.api.v1.endpoints.admin_integrations.get_integrations_summary",
            new_callable=AsyncMock,
            return_value=updated,
        ),
    ):
        client = _client_for(_user(PERM))
        put = client.put(
            f"{BASE}/minio",
            json={
                "endpoint": "127.0.0.1:9000",
                "access_key": "brand-new-secret",
                "secret_key": "minioadmin-secret",
                "bucket": "resumes",
            },
        )
        assert put.status_code == 200
        assert put.json()["minio"]["secret_key"]["configured"] is True
        assert "value" not in put.json()["minio"]["secret_key"]
        _assert_no_secrets(put.json())

        got = client.get(BASE)
        assert got.status_code == 200
        assert got.json()["minio"]["access_key"]["configured"] is True
        _assert_no_secrets(got.json())


def test_post_test_response_only_three_fields(lifespan_patches) -> None:
    result = ConnectivityResult(ok=True, error_code=None, latency_ms=12)
    with (
        patch(
            "app.api.v1.endpoints.admin_integrations.test_dify",
            new_callable=AsyncMock,
            return_value=result,
        ) as mock_dify,
        patch(
            "app.api.v1.endpoints.admin_integrations.test_minio",
            new_callable=AsyncMock,
            return_value=result,
        ) as mock_minio,
        patch(
            "app.api.v1.endpoints.admin_integrations.test_mail",
            new_callable=AsyncMock,
            return_value=result,
        ) as mock_mail,
    ):
        client = _client_for(_user(PERM))
        for provider in ("dify", "minio", "mail"):
            response = client.post(f"{BASE}/{provider}/test")
            assert response.status_code == 200, provider
            assert response.headers.get("Cache-Control") == "no-store"
            data = response.json()
            assert set(data.keys()) == {"ok", "error_code", "latency_ms"}
            assert data["ok"] is True
            assert data["error_code"] is None
            assert data["latency_ms"] == 12
            _assert_no_secrets(data)

        mock_dify.assert_awaited_once()
        mock_minio.assert_awaited_once()
        mock_mail.assert_awaited_once()


def test_post_test_forbidden_without_permission(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    assert client.post(f"{BASE}/dify/test").status_code == 403


def test_no_mail_put_or_smtp_routes(lifespan_patches) -> None:
    client = _client_for(_user(PERM))
    assert client.put(f"{BASE}/mail", json={"provider_name": "console"}).status_code == (
        404
    )
    assert client.put(
        f"{BASE}/smtp", json={"smtp_host": "x"}
    ).status_code == 404
    # unknown provider test
    assert client.post(f"{BASE}/smtp/test").status_code in {404, 422}


def test_env_example_documents_minio_resume_dify_without_smtp() -> None:
    text = (BACKEND_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "MINIO_ENDPOINT=" in text
    assert "MINIO_ACCESS_KEY=" in text
    assert "MINIO_SECRET_KEY=" in text
    assert "MINIO_BUCKET=" in text
    assert "DIFY_RESUME_PARSE_API_KEY=" in text
    assert "DIFY_RESUME_SCORE_API_KEY=" in text
    assert "DIFY_RESUME_PARSE_WORKFLOW_ID=" in text
    assert "DIFY_RESUME_SCORE_WORKFLOW_ID=" in text
    assert "integration_secrets" in text.lower() or "重启" in text
    assert "DATA_ENCRYPTION_KEY" in text
    assert "smtp" not in text.lower()
    assert "SMTP" not in text
    # no realistic-looking secrets
    assert "sk-" not in text
    assert "minioadmin" not in text.lower()


def test_integrations_api_source_has_no_smtp_or_enqueue() -> None:
    path = (
        BACKEND_ROOT
        / "app"
        / "api"
        / "v1"
        / "endpoints"
        / "admin_integrations.py"
    )
    text = path.read_text(encoding="utf-8").lower()
    assert "smtp" not in text
    assert "apply_async" not in text
    assert "workflow" not in text or "workflow_id" in text  # schema field ok
    # no direct minio put/get in API layer
    assert "put_object" not in text
    assert "get_object" not in text
    assert "presigned" not in text


def test_ready_checks_still_only_postgres_and_redis() -> None:
    from app.services import readiness

    src = Path(readiness.__file__).read_text(encoding="utf-8")
    assert "postgresql" in src
    assert "redis" in src
    assert "dify" not in src.lower()
    assert "minio" not in src.lower()
    assert "smtp" not in src.lower()


@pytest.fixture
def fernet_key(monkeypatch: pytest.MonkeyPatch):
    from cryptography.fernet import Fernet

    from app.core.config import get_settings

    key = Fernet.generate_key()
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", key.decode("ascii"))
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


def _disabled_summary() -> dict:
    summary = _safe_summary()
    for block_name in ("dify", "minio"):
        block = summary[block_name]
        for key, field in list(block.items()):
            if not isinstance(field, dict):
                continue
            field = dict(field)
            field["enabled"] = False
            field["status"] = "disabled"
            block[key] = field
    summary["restart_required"] = True
    return summary


def test_put_enabled_false_requires_schema_and_returns_disabled(
    lifespan_patches,
) -> None:
    """system_admin can PUT enabled=false maps; summary shows disabled; no secrets."""
    disabled = _disabled_summary()
    with (
        patch(
            "app.api.v1.endpoints.admin_integrations.update_dify",
            new_callable=AsyncMock,
            return_value=disabled,
        ) as mock_dify,
        patch(
            "app.api.v1.endpoints.admin_integrations.update_minio",
            new_callable=AsyncMock,
            return_value=disabled,
        ) as mock_minio,
    ):
        client = _client_for(_user(PERM))
        dify_body = {
            "enabled": {"api_base_url": False, "api_key": False},
            "api_key": "",
        }
        minio_body = {
            "enabled": {
                "endpoint": False,
                "access_key": False,
                "secret_key": False,
                "bucket": False,
            },
            "secret_key": "",
        }
        put_d = client.put(f"{BASE}/dify", json=dify_body)
        put_m = client.put(f"{BASE}/minio", json=minio_body)
        assert put_d.status_code == 200, put_d.text
        assert put_m.status_code == 200, put_m.text
        body_d = put_d.json()
        body_m = put_m.json()
        assert body_d["restart_required"] is True
        assert body_m["restart_required"] is True
        assert body_d["dify"]["api_key"]["enabled"] is False
        assert body_d["dify"]["api_key"]["status"] == "disabled"
        assert body_d["dify"]["api_base_url"]["enabled"] is False
        assert body_m["minio"]["secret_key"]["enabled"] is False
        assert body_m["minio"]["secret_key"]["status"] == "disabled"
        assert "value" not in body_d["dify"]["api_key"]
        assert "value" not in body_m["minio"]["secret_key"]
        _assert_no_secrets(body_d)
        _assert_no_secrets(body_m)

        mock_dify.assert_awaited_once()
        mock_minio.assert_awaited_once()
        assert mock_dify.await_args.kwargs["payload"]["enabled"] == {
            "api_base_url": False,
            "api_key": False,
        }
        assert mock_dify.await_args.kwargs["payload"]["api_key"] == ""
        assert mock_minio.await_args.kwargs["payload"]["enabled"]["secret_key"] is False
        assert mock_minio.await_args.kwargs["payload"]["secret_key"] == ""


def test_put_enabled_false_forbidden_for_non_admin(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage", "interview.execute"))
    assert (
        client.put(
            f"{BASE}/dify",
            json={"enabled": {"api_key": False}},
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"{BASE}/minio",
            json={"enabled": {"secret_key": False}},
        ).status_code
        == 403
    )


def test_put_enabled_false_keeps_ciphertext_and_env_fallback_after_reload(
    lifespan_patches, fernet_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty secret keeps ciphertext; enabled=false; reload overlay → env baseline."""
    from app.core.config import get_settings
    from app.models.integration_secret import IntegrationSecret
    from app.services.crypto import encrypt_secret
    from app.services.integration_config import (
        effective_dify_api_base_url,
        effective_dify_api_key,
        effective_minio_bucket,
        effective_minio_secret_key,
        materialize_overlay_from_rows,
        set_process_overlay,
    )
    from app.services import integrations as svc

    monkeypatch.setenv("DIFY_API_BASE_URL", "https://env-baseline.example/v1")
    monkeypatch.setenv("DIFY_API_KEY", "env-baseline-dify-key")
    monkeypatch.setenv("MINIO_BUCKET", "env-baseline-bucket")
    monkeypatch.setenv("MINIO_SECRET_KEY", "env-baseline-minio-secret")
    get_settings.cache_clear()

    store: dict = {}
    original_dify = encrypt_secret("db-overlay-dify-key")
    original_minio = encrypt_secret("db-overlay-minio-secret")
    assert original_dify and original_minio

    def _row(provider: str, config_key: str, cipher: str, *, secret: bool) -> IntegrationSecret:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        return IntegrationSecret(
            id=uuid4(),
            provider=provider,
            config_key=config_key,
            value_encrypted=cipher,
            is_secret=secret,
            enabled=True,
            updated_by=None,
            created_at=now,
            updated_at=now,
        )

    store[("dify", "api_key")] = _row("dify", "api_key", original_dify, secret=True)
    store[("dify", "api_base_url")] = _row(
        "dify",
        "api_base_url",
        encrypt_secret("https://db-overlay.example/v1") or "",
        secret=False,
    )
    store[("minio", "secret_key")] = _row(
        "minio", "secret_key", original_minio, secret=True
    )
    store[("minio", "bucket")] = _row(
        "minio",
        "bucket",
        encrypt_secret("db-overlay-bucket") or "",
        secret=False,
    )

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
            row = _row(provider, config_key, value_encrypted, secret=is_secret)
            row.enabled = enabled
            row.updated_by = updated_by
            store[(provider, config_key)] = row
            return row
        existing.value_encrypted = value_encrypted
        existing.is_secret = is_secret
        existing.enabled = enabled
        existing.updated_by = updated_by
        return existing

    async def capture_audit(session, **kwargs):
        return None

    monkeypatch.setattr(svc.secrets_repo, "list_integration_secrets", list_secrets)
    monkeypatch.setattr(svc.secrets_repo, "get_integration_secret", get_secret)
    monkeypatch.setattr(svc.secrets_repo, "upsert_integration_secret", upsert)
    monkeypatch.setattr(svc, "record_audit", capture_audit)
    import app.services.integration_config as cfg

    monkeypatch.setattr(cfg.secrets_repo, "list_integration_secrets", list_secrets)

    session = AsyncMock()
    session.commit = AsyncMock()

    async def override_db():
        yield session

    admin = _user(PERM)
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]

    async def override_user() -> User:
        return admin

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_db_session] = override_db
    client = TestClient(app)

    put_d = client.put(
        f"{BASE}/dify",
        json={
            "enabled": {"api_key": False, "api_base_url": False},
            "api_key": "",
        },
    )
    put_m = client.put(
        f"{BASE}/minio",
        json={
            "enabled": {"secret_key": False, "bucket": False},
            "secret_key": "",
        },
    )
    assert put_d.status_code == 200, put_d.text
    assert put_m.status_code == 200, put_m.text
    body_d = put_d.json()
    body_m = put_m.json()
    assert body_d["dify"]["api_key"]["enabled"] is False
    assert body_d["dify"]["api_key"]["status"] == "disabled"
    assert body_d["dify"]["api_key"]["configured"] is True  # env baseline still configured
    assert body_d["dify"]["api_base_url"]["enabled"] is False
    assert body_d["dify"]["api_base_url"]["status"] == "disabled"
    # non-secret shows env baseline value after disable (not DB overlay URL)
    assert body_d["dify"]["api_base_url"]["value"] == "https://env-baseline.example/v1"
    assert body_m["minio"]["secret_key"]["enabled"] is False
    assert body_m["minio"]["secret_key"]["status"] == "disabled"
    assert body_m["minio"]["bucket"]["value"] == "env-baseline-bucket"
    assert "value" not in body_d["dify"]["api_key"]
    assert "value" not in body_m["minio"]["secret_key"]
    _assert_no_secrets(body_d)
    _assert_no_secrets(body_m)
    # disabled fields must not echo overlay plaintext
    assert "db-overlay" not in body_d["dify"]["api_base_url"]["value"]
    assert "db-overlay" not in body_m["minio"]["bucket"]["value"]
    assert "db-overlay-dify-key" not in json.dumps(body_d)
    assert "db-overlay-minio-secret" not in json.dumps(body_m)

    # empty secret must not clear previous ciphertext
    assert store[("dify", "api_key")].value_encrypted == original_dify
    assert store[("minio", "secret_key")].value_encrypted == original_minio
    assert store[("dify", "api_key")].enabled is False
    assert store[("minio", "secret_key")].enabled is False

    # simulate process restart: reload overlay from DB rows into process snapshot
    set_process_overlay(materialize_overlay_from_rows(list(store.values())))
    assert effective_dify_api_key() == "env-baseline-dify-key"
    assert effective_dify_api_base_url() == "https://env-baseline.example/v1"
    assert effective_minio_secret_key() == "env-baseline-minio-secret"
    assert effective_minio_bucket() == "env-baseline-bucket"
