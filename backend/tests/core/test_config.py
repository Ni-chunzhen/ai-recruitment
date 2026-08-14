from pydantic import SecretStr

from app.core.config import get_settings


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
