from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, get_db_session
from app.main import app
from app.models import User
from app.models.resume import (
    SCREENING_REASON_CODES,
    SCREENING_REASON_OTHER,
    SCREENING_REJECT,
    SCREENING_TALENT_POOL,
)


@pytest.fixture
def api_client() -> TestClient:
    user = User(
        id=uuid4(),
        username="hr",
        username_normalized="hr",
        display_name="HR",
        password_hash="x",
        is_active=True,
        must_change_password=False,
        token_version=1,
    )

    async def override_user() -> User:
        return user

    async def override_db():
        yield AsyncMock()

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_db_session] = override_db
    try:
        with (
            patch(
                "app.api.dependencies.auth.user_has_permission",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("app.main.create_database_engine"),
            patch("app.main.create_session_factory"),
            patch("app.main.create_redis_client", return_value=AsyncMock()),
            patch("app.main.close_redis", new_callable=AsyncMock),
            patch("app.main.dispose_database", new_callable=AsyncMock),
        ):
            with TestClient(app) as client:
                yield client
    finally:
        app.dependency_overrides.clear()


def test_screening_reason_codes_returns_eight_backend_constants(
    api_client: TestClient,
) -> None:
    response = api_client.get("/api/v1/screening-reason-codes")

    assert response.status_code == 200
    body = response.json()
    items = body["items"]
    codes = {item["code"] for item in items}
    assert codes == set(SCREENING_REASON_CODES)
    assert len(items) == 8

    other = next(item for item in items if item["code"] == SCREENING_REASON_OTHER)
    assert other["requires_description"] is True
    assert other["label"]

    reject_codes = {
        item["code"]
        for item in items
        if SCREENING_REJECT in item["allowed_decisions"]
    }
    talent_pool_codes = {
        item["code"]
        for item in items
        if SCREENING_TALENT_POOL in item["allowed_decisions"]
    }
    assert reject_codes
    assert talent_pool_codes
    assert SCREENING_REASON_OTHER in reject_codes
    assert SCREENING_REASON_OTHER in talent_pool_codes

    for item in items:
        assert item["code"]
        assert item["label"]
        assert isinstance(item["allowed_decisions"], list)
        assert isinstance(item["requires_description"], bool)
        if item["code"] != SCREENING_REASON_OTHER:
            assert item["requires_description"] is False
