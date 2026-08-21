"""Static security review for integration configuration (Task 5)."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = BACKEND_ROOT.parent / "frontend"

PROTECTED_RUNNING = (
    "dde1470f-d9ef-458c-a29d-e7a8c9f5bcca",
    "3556206d-138b-40f6-9b23-97fce178a32e",
)

INTEGRATION_BACKEND_PATHS = [
    BACKEND_ROOT / "app" / "services" / "integrations.py",
    BACKEND_ROOT / "app" / "services" / "integration_config.py",
    BACKEND_ROOT / "app" / "services" / "integration_connectivity.py",
    BACKEND_ROOT / "app" / "api" / "v1" / "endpoints" / "admin_integrations.py",
    BACKEND_ROOT / "app" / "schemas" / "integration.py",
    BACKEND_ROOT / "app" / "models" / "integration_secret.py",
    BACKEND_ROOT / "app" / "services" / "readiness.py",
]


def _read(path: Path) -> str:
    assert path.is_file(), path
    return path.read_text(encoding="utf-8")


def test_integration_sources_forbid_smtp_transport_and_enqueue() -> None:
    for path in INTEGRATION_BACKEND_PATHS:
        text = _read(path).lower()
        assert "smtplib" not in text
        assert "apply_async" not in text
        assert "put_object" not in text
        assert "get_object" not in text
        assert "presigned" not in text
        assert "run_workflow" not in text
        assert "workflows/run" not in text
        # API/schema/model must not expose SMTP transport fields as writable API.
        if path.name in {
            "admin_integrations.py",
            "integration.py",
            "integration_secret.py",
            "integration_config.py",
            "integration_connectivity.py",
            "readiness.py",
        }:
            assert "smtp_host" not in text
            assert "smtp_password" not in text


def test_ready_checks_unchanged_postgres_redis_only() -> None:
    text = _read(BACKEND_ROOT / "app" / "services" / "readiness.py")
    assert '"postgresql"' in text or "'postgresql'" in text
    assert '"redis"' in text or "'redis'" in text
    assert "dify" not in text.lower()
    assert "minio" not in text.lower()
    assert "smtp" not in text.lower()
    # returned checks dict keys only pg+redis
    assert "return {" in text
    assert text.count('"postgresql"') + text.count("'postgresql'") >= 1


def test_live_switch_not_writable_via_integrations_service() -> None:
    text = _read(BACKEND_ROOT / "app" / "services" / "integrations.py")
    assert "live_enabled" in text
    assert "forbidden" in text.lower()


def test_frontend_integrations_page_has_no_smtp_or_secret_echo() -> None:
    view = _read(FRONTEND_ROOT / "src" / "views" / "SystemIntegrationsView.vue")
    api = _read(FRONTEND_ROOT / "src" / "api" / "integrations.ts")
    lower = (view + api).lower()
    assert "smtp_host" not in lower
    assert "发送测试邮件" not in view
    assert "自动开启" not in view
    assert 'type="password"' in view
    assert "console.log" not in view
    assert "value_encrypted" not in lower
    assert "enc:v1:" not in lower
    assert "mail-block" in view
    assert "integration-config-page" in view
    assert "test-mail" not in lower
    assert "testIntegrationProvider(provider: 'dify' | 'minio')" in api


def test_protected_running_ids_not_in_integration_code() -> None:
    paths = list(INTEGRATION_BACKEND_PATHS) + [
        FRONTEND_ROOT / "src" / "views" / "SystemIntegrationsView.vue",
        FRONTEND_ROOT / "src" / "api" / "integrations.ts",
    ]
    for path in paths:
        text = _read(path)
        for task_id in PROTECTED_RUNNING:
            assert task_id not in text


def test_env_example_still_without_smtp() -> None:
    text = (BACKEND_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "MINIO_ENDPOINT=" in text
    assert "smtp" not in text.lower()
