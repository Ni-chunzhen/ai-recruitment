from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def mock_engine() -> MagicMock:
    return MagicMock(name="db_engine")


@pytest.fixture
def mock_session_factory() -> MagicMock:
    return MagicMock(name="db_session_factory")


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock(name="redis")


def test_lifespan_creates_and_disposes_resources(
    mock_engine: MagicMock,
    mock_session_factory: MagicMock,
    mock_redis: AsyncMock,
) -> None:
    with (
        patch("app.main.get_settings") as mock_get_settings,
        patch(
            "app.main.create_database_engine",
            return_value=mock_engine,
        ) as mock_create_engine,
        patch(
            "app.main.create_session_factory",
            return_value=mock_session_factory,
        ) as mock_create_session_factory,
        patch(
            "app.main.create_redis_client",
            return_value=mock_redis,
        ) as mock_create_redis,
        patch(
            "app.main.close_redis",
            new_callable=AsyncMock,
        ) as mock_close_redis,
        patch(
            "app.main.dispose_database",
            new_callable=AsyncMock,
        ) as mock_dispose_database,
    ):
        mock_get_settings.return_value = MagicMock(
            APP_NAME="ai-recruitment-api",
            API_V1_PREFIX="/api/v1",
            database_url="postgresql+asyncpg://recruit:secret@127.0.0.1:5432/recruit",
            redis_url="redis://127.0.0.1:6379/0",
        )

        with TestClient(app) as client:
            assert client.app.state.db_engine is mock_engine
            assert client.app.state.db_session_factory is mock_session_factory
            assert client.app.state.redis is mock_redis

        mock_create_engine.assert_called_once()
        mock_create_session_factory.assert_called_once_with(mock_engine)
        mock_create_redis.assert_called_once()
        mock_close_redis.assert_awaited_once_with(mock_redis)
        mock_dispose_database.assert_awaited_once_with(mock_engine)
