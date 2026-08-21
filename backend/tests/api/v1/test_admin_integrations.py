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
