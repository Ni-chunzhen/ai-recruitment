from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    with (
        patch("app.main.create_database_engine") as mock_create_engine,
        patch("app.main.create_session_factory"),
        patch("app.main.create_redis_client") as mock_create_redis,
        patch("app.main.close_redis", new_callable=AsyncMock),
        patch("app.main.dispose_database", new_callable=AsyncMock),
    ):
        mock_create_engine.return_value = AsyncMock(name="db_engine")
        mock_create_redis.return_value = AsyncMock(name="redis")
        with TestClient(app) as test_client:
            yield test_client


def test_health_returns_standard_response(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "ok",
        "data": {
            "status": "ok",
            "service": "ai-recruitment-api",
        },
    }


def test_health_live_returns_alive_without_external_checks(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "alive",
        "data": {"status": "alive"},
    }


def test_health_ready_returns_200_when_all_up(client: TestClient) -> None:
    with patch(
        "app.api.v1.endpoints.health.run_readiness_checks",
        new_callable=AsyncMock,
        return_value={"postgresql": "up", "redis": "up"},
    ):
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "ready",
        "data": {
            "status": "ready",
            "checks": {"postgresql": "up", "redis": "up"},
        },
    }


def test_health_ready_returns_503_when_redis_down(client: TestClient) -> None:
    with patch(
        "app.api.v1.endpoints.health.run_readiness_checks",
        new_callable=AsyncMock,
        return_value={"postgresql": "up", "redis": "down"},
    ):
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "code": 50300,
        "message": "service not ready",
        "data": {
            "status": "not_ready",
            "checks": {"postgresql": "up", "redis": "down"},
        },
    }


def test_health_ready_returns_503_when_postgresql_down(client: TestClient) -> None:
    with patch(
        "app.api.v1.endpoints.health.run_readiness_checks",
        new_callable=AsyncMock,
        return_value={"postgresql": "down", "redis": "up"},
    ):
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["data"]["checks"]["postgresql"] == "down"


def test_health_ready_response_does_not_leak_secrets(client: TestClient) -> None:
    with patch(
        "app.api.v1.endpoints.health.run_readiness_checks",
        new_callable=AsyncMock,
        return_value={"postgresql": "down", "redis": "down"},
    ):
        response = client.get("/api/v1/health/ready")

    body = response.text.lower()
    assert "password" not in body
    assert "database_url" not in body
    assert "redis_url" not in body
    assert "connection failed" not in body
