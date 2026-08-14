from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, get_db_session
from app.main import app
from app.models import User
from app.schemas.ai_task import AITaskAttemptOut, AITaskListResponse, AITaskOut

FORBIDDEN_TASK_KEYS = {
    "input_snapshot",
    "raw_request",
    "raw_response",
    "result_payload",
    "provider_run_id",
    "request_id",
}
FORBIDDEN_ATTEMPT_KEYS = {
    "raw_response",
    "raw_request",
    "provider_run_id",
    "request_id",
}
RESUME_SECRET = "COMPLETE_RESUME_TEXT_SHOULD_NOT_LEAK"
STACK_TRACE = "Traceback (most recent call last): File app/workers"
API_KEY_VALUE = "sk-dify-secret-should-not-leak"


def _task_out() -> AITaskOut:
    now = datetime.now(UTC)
    return AITaskOut(
        id=uuid4(),
        task_type="RESUME_SCORE",
        status="succeeded",
        business_type="application",
        business_id=uuid4(),
        version_id=None,
        created_by=None,
        input_snapshot={"resume_text": RESUME_SECRET, "api_key": API_KEY_VALUE},
        result_payload={"total_score": 88, "stack": STACK_TRACE},
        error_code=None,
        error_message=None,
        error_category=None,
        attempt_count=2,
        retry_cycle_no=1,
        cycle_attempt_count=2,
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
        raw_purged_at=None,
        attempts=[
            AITaskAttemptOut(
                id=uuid4(),
                attempt_no=1,
                retry_cycle_no=1,
                cycle_attempt_no=1,
                status="succeeded",
                started_at=now,
                finished_at=now,
                duration_ms=1200,
                http_status=200,
                error_category=None,
                error_message=None,
                created_at=now,
            )
        ],
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


def _assert_ordinary_task_payload(payload: dict) -> None:
    for key in FORBIDDEN_TASK_KEYS:
        assert key not in payload
    body_text = str(payload)
    assert RESUME_SECRET not in body_text
    assert API_KEY_VALUE not in body_text
    assert STACK_TRACE not in body_text
    for attempt in payload.get("attempts") or []:
        for key in FORBIDDEN_ATTEMPT_KEYS:
            assert key not in attempt
    for required in (
        "id",
        "task_type",
        "business_type",
        "business_id",
        "status",
        "attempt_count",
        "retry_cycle_no",
        "cycle_attempt_count",
        "error_code",
        "error_message",
        "created_at",
        "started_at",
        "finished_at",
        "attempts",
    ):
        assert required in payload


def test_get_ai_task_ordinary_response_omits_sensitive_fields(
    api_client: TestClient,
) -> None:
    task = _task_out()
    with patch(
        "app.api.v1.endpoints.ai_tasks.get_ai_task",
        new_callable=AsyncMock,
        return_value=task,
    ):
        response = api_client.get(f"/api/v1/ai-tasks/{task.id}")

    assert response.status_code == 200
    _assert_ordinary_task_payload(response.json())


def test_list_ai_tasks_ordinary_response_omits_sensitive_fields(
    api_client: TestClient,
) -> None:
    task = _task_out()
    listing = AITaskListResponse(items=[task], total=1)
    with patch(
        "app.api.v1.endpoints.ai_tasks.list_ai_tasks",
        new_callable=AsyncMock,
        return_value=listing,
    ):
        response = api_client.get(
            "/api/v1/ai-tasks",
            params={
                "business_type": "application",
                "business_id": str(task.business_id),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    _assert_ordinary_task_payload(body["items"][0])
