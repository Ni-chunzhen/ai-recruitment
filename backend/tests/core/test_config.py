from pathlib import Path

from pydantic import SecretStr

from app.core.config import Settings, get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE_PATH = BACKEND_ROOT / ".env.example"

_INTERVIEW_QUESTION_ENV_KEYS = (
    "DIFY_INTERVIEW_QUESTION_LIVE_ENABLED",
    "DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY",
    "DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID",
)


def test_settings_reads_database_and_redis_urls(monkeypatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://recruit:test-secret-password@127.0.0.1:5432/recruit",
    )
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.database_url == (
        "postgresql+asyncpg://recruit:test-secret-password@127.0.0.1:5432/recruit"
    )
    assert settings.redis_url == "redis://127.0.0.1:6379/0"

    get_settings.cache_clear()


def test_settings_repr_does_not_expose_password(monkeypatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://recruit:test-secret-password@127.0.0.1:5432/recruit",
    )
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    get_settings.cache_clear()

    settings = get_settings()
    settings_text = repr(settings)

    assert "test-secret-password" not in settings_text
    assert isinstance(settings.database_url_secret, SecretStr)

    get_settings.cache_clear()


def test_interview_question_settings_defaults(monkeypatch) -> None:
    for key in _INTERVIEW_QUESTION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)
    used_cached_settings = settings is get_settings()
    key_is_blank = not bool(
        settings.dify_interview_question_generate_api_key_secret.get_secret_value().strip()
    )
    workflow_is_blank = not bool(
        settings.dify_interview_question_generate_workflow_id.strip()
    )
    live_is_off = settings.dify_interview_question_live_enabled is False

    assert used_cached_settings is False
    assert live_is_off is True
    assert key_is_blank is True
    assert workflow_is_blank is True


def test_interview_question_settings_read_env_aliases(monkeypatch) -> None:
    monkeypatch.setenv("DIFY_INTERVIEW_QUESTION_LIVE_ENABLED", "true")
    monkeypatch.setenv(
        "DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY", "test-interview-question-key"
    )
    monkeypatch.setenv(
        "DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID",
        "test-interview-question-workflow-id",
    )
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.dify_interview_question_live_enabled is True
    assert (
        settings.dify_interview_question_generate_api_key_secret.get_secret_value()
        == "test-interview-question-key"
    )
    assert (
        settings.dify_interview_question_generate_workflow_id
        == "test-interview-question-workflow-id"
    )

    get_settings.cache_clear()


def _env_example_assignment(text: str, key: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith(f"{key}="):
            return stripped.split("=", 1)[1]
    raise AssertionError(f"missing {key} assignment in .env.example")


def test_env_example_interview_question_vars_are_empty() -> None:
    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

    assert _env_example_assignment(text, "DIFY_INTERVIEW_QUESTION_LIVE_ENABLED") == (
        "false"
    )
    assert (
        _env_example_assignment(text, "DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY") == ""
    )
    assert (
        _env_example_assignment(
            text, "DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID"
        )
        == ""
    )
    assert "禁止复用 DIFY_API_KEY" in text
