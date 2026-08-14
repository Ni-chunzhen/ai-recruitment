from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, get_db_session
from app.main import app
from app.models import Permission, Role, User
from app.schemas.ai_task import AITaskAdminDetailOut, AITaskAdminListResponse
from app.services.ai_tasks import AITaskStateError

AUDIT_PERM = "audit.read"
MANAGE_PERM = "ai_task.manage"
SYSTEM_ADMIN_PERMS = (AUDIT_PERM, MANAGE_PERM)
RECRUITER_PERMS = (
    "profile.read",
    "profile.change_password",
    "recruitment.manage",
    "interview.execute",
)
ORDINARY_PERMS = ("profile.read", "profile.change_password")
FORBIDDEN_KEYS = {
    "input_snapshot",
    "result_payload",
    "raw_request",
    "raw_response",
    "response_purged_at",
    "api_key",
}


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


def _admin_detail_payload() -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "id": str(uuid4()),
        "task_type": "RESUME_SCORE",
        "business_type": "application",
        "business_id": str(uuid4()),
        "status": "failed",
        "attempt_count": 3,
        "retry_cycle_no": 1,
        "cycle_attempt_count": 1,
        "error_category": "retryable",
        "error_code": "provider_error",
        "error_message": "provider timeout",
        "created_by": str(uuid4()),
        "created_at": now,
        "started_at": now,
        "finished_at": now,
        "duration_ms": 1200,
        "attempts": [
            {
                "id": str(uuid4()),
                "attempt_no": 3,
                "retry_cycle_no": 1,
                "cycle_attempt_no": 1,
                "status": "failed",
                "started_at": now,
                "finished_at": now,
                "duration_ms": 1200,
                "http_status": 502,
                "error_category": "retryable",
                "error_message": "provider timeout",
                "provider_run_id": "run-abc",
                "request_id": "req-xyz",
            }
        ],
    }


def _admin_detail(overrides: dict | None = None) -> AITaskAdminDetailOut:
    payload = _admin_detail_payload()
    if overrides:
        payload.update(overrides)
    return AITaskAdminDetailOut.model_validate(payload)


def _empty_list() -> AITaskAdminListResponse:
    return AITaskAdminListResponse(items=[], total=0, page=1, page_size=20)


def test_system_admin_can_list_admin_ai_tasks(lifespan_patches) -> None:
    listing = _empty_list()
    with (
        _client_for(_user(*SYSTEM_ADMIN_PERMS)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.list_admin_ai_tasks",
            new_callable=AsyncMock,
            return_value=listing,
        ),
    ):
        response = client.get("/api/v1/admin/ai-tasks")
    assert response.status_code == 200


def test_audit_read_only_can_get_but_cannot_mutate(lifespan_patches) -> None:
    listing = _empty_list()
    detail = _admin_detail()
    task_id = detail.id
    with (
        _client_for(_user(AUDIT_PERM)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.list_admin_ai_tasks",
            new_callable=AsyncMock,
            return_value=listing,
        ),
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.get_admin_ai_task",
            new_callable=AsyncMock,
            return_value=detail,
        ),
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.retry_ai_task",
            new_callable=AsyncMock,
        ) as retry,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.cancel_ai_task",
            new_callable=AsyncMock,
        ) as cancel,
    ):
        assert client.get("/api/v1/admin/ai-tasks").status_code == 200
        assert client.get(f"/api/v1/admin/ai-tasks/{task_id}").status_code == 200
        retry_url = f"/api/v1/admin/ai-tasks/{task_id}/retry"
        cancel_url = f"/api/v1/admin/ai-tasks/{task_id}/cancel"
        assert client.post(retry_url).status_code == 403
        assert client.post(cancel_url).status_code == 403
    retry.assert_not_awaited()
    cancel.assert_not_awaited()


def test_system_admin_can_get_and_mutate(lifespan_patches) -> None:
    listing = _empty_list()
    detail = _admin_detail()
    task_id = detail.id
    with (
        _client_for(_user(*SYSTEM_ADMIN_PERMS)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.list_admin_ai_tasks",
            new_callable=AsyncMock,
            return_value=listing,
        ),
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.get_admin_ai_task",
            new_callable=AsyncMock,
            return_value=detail,
        ),
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.retry_ai_task",
            new_callable=AsyncMock,
        ) as retry,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.cancel_ai_task",
            new_callable=AsyncMock,
        ) as cancel,
    ):
        assert client.get("/api/v1/admin/ai-tasks").status_code == 200
        assert client.get(f"/api/v1/admin/ai-tasks/{task_id}").status_code == 200
        assert client.post(f"/api/v1/admin/ai-tasks/{task_id}/retry").status_code == 200
        assert (
            client.post(
                f"/api/v1/admin/ai-tasks/{task_id}/cancel",
                json={"reason": "ops cancel"},
            ).status_code
            == 200
        )
    retry.assert_awaited()
    cancel.assert_awaited()


def test_recruiter_admin_all_admin_ai_task_endpoints_403(lifespan_patches) -> None:
    task_id = uuid4()
    with (
        _client_for(_user(*RECRUITER_PERMS)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.retry_ai_task",
            new_callable=AsyncMock,
        ) as retry,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.cancel_ai_task",
            new_callable=AsyncMock,
        ) as cancel,
    ):
        assert client.get("/api/v1/admin/ai-tasks").status_code == 403
        assert client.get(f"/api/v1/admin/ai-tasks/{task_id}").status_code == 403
        retry_url = f"/api/v1/admin/ai-tasks/{task_id}/retry"
        cancel_url = f"/api/v1/admin/ai-tasks/{task_id}/cancel"
        assert client.post(retry_url).status_code == 403
        assert client.post(cancel_url).status_code == 403
    retry.assert_not_awaited()
    cancel.assert_not_awaited()


def test_ordinary_user_all_admin_ai_task_endpoints_403(lifespan_patches) -> None:
    task_id = uuid4()
    with _client_for(_user(*ORDINARY_PERMS)) as client:
        assert client.get("/api/v1/admin/ai-tasks").status_code == 403
        assert client.get(f"/api/v1/admin/ai-tasks/{task_id}").status_code == 403
        retry_url = f"/api/v1/admin/ai-tasks/{task_id}/retry"
        cancel_url = f"/api/v1/admin/ai-tasks/{task_id}/cancel"
        assert client.post(retry_url).status_code == 403
        assert client.post(cancel_url).status_code == 403


def test_admin_list_filters_status_and_task_type(lifespan_patches) -> None:
    listing = _empty_list()
    mocked = AsyncMock(return_value=listing)
    with (
        _client_for(_user(*SYSTEM_ADMIN_PERMS)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.list_admin_ai_tasks",
            mocked,
        ),
    ):
        response = client.get(
            "/api/v1/admin/ai-tasks",
            params={"status": "failed", "task_type": "RESUME_SCORE"},
        )
    assert response.status_code == 200
    kwargs = mocked.await_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["task_type"] == "RESUME_SCORE"


def test_admin_detail_returns_cycle_attempt_and_tech_ids(
    lifespan_patches,
) -> None:
    detail = _admin_detail()
    with (
        _client_for(_user(*SYSTEM_ADMIN_PERMS)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.get_admin_ai_task",
            new_callable=AsyncMock,
            return_value=detail,
        ),
    ):
        response = client.get(f"/api/v1/admin/ai-tasks/{detail.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["retry_cycle_no"] == 1
    assert body["cycle_attempt_count"] == 1
    attempt = body["attempts"][0]
    assert attempt["retry_cycle_no"] == 1
    assert attempt["cycle_attempt_no"] == 1
    assert attempt["provider_run_id"] == "run-abc"
    assert attempt["request_id"] == "req-xyz"


def test_admin_detail_omits_raw_response_and_input_snapshot(
    lifespan_patches,
) -> None:
    detail = _admin_detail()
    with (
        _client_for(_user(*SYSTEM_ADMIN_PERMS)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.get_admin_ai_task",
            new_callable=AsyncMock,
            return_value=detail,
        ),
    ):
        response = client.get(f"/api/v1/admin/ai-tasks/{detail.id}")
    assert response.status_code == 200
    body = response.json()
    body_text = str(body)
    for key in FORBIDDEN_KEYS:
        assert key not in body
        assert key not in (body.get("attempts") or [{}])[0]
    assert "SECRET_RESUME" not in body_text
    assert "RAW_ATTEMPT" not in body_text


def test_failed_allows_admin_retry(lifespan_patches) -> None:
    detail = _admin_detail({"status": "failed"})
    with (
        _client_for(_user(*SYSTEM_ADMIN_PERMS)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.retry_ai_task",
            new_callable=AsyncMock,
        ) as retry,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.get_admin_ai_task",
            new_callable=AsyncMock,
            return_value=detail,
        ),
    ):
        response = client.post(f"/api/v1/admin/ai-tasks/{detail.id}/retry")
    assert response.status_code == 200
    retry.assert_awaited()


def test_output_invalid_allows_admin_retry(lifespan_patches) -> None:
    detail = _admin_detail({"status": "output_invalid"})
    with (
        _client_for(_user(*SYSTEM_ADMIN_PERMS)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.retry_ai_task",
            new_callable=AsyncMock,
        ),
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.get_admin_ai_task",
            new_callable=AsyncMock,
            return_value=detail,
        ),
    ):
        response = client.post(f"/api/v1/admin/ai-tasks/{detail.id}/retry")
    assert response.status_code == 200


def test_succeeded_forbids_admin_retry(lifespan_patches) -> None:
    task_id = uuid4()
    with (
        _client_for(_user(*SYSTEM_ADMIN_PERMS)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.retry_ai_task",
            new_callable=AsyncMock,
            side_effect=AITaskStateError(
                "only failed or output_invalid tasks can be retried"
            ),
        ),
    ):
        response = client.post(f"/api/v1/admin/ai-tasks/{task_id}/retry")
    assert response.status_code == 409


def test_pending_allows_admin_cancel(lifespan_patches) -> None:
    detail = _admin_detail({"status": "cancelled"})
    with (
        _client_for(_user(*SYSTEM_ADMIN_PERMS)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.cancel_ai_task",
            new_callable=AsyncMock,
        ) as cancel,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.get_admin_ai_task",
            new_callable=AsyncMock,
            return_value=detail,
        ),
    ):
        response = client.post(
            f"/api/v1/admin/ai-tasks/{detail.id}/cancel",
            json={"reason": "ops cancel"},
        )
    assert response.status_code == 200
    cancel.assert_awaited()


def test_running_forbids_admin_cancel(lifespan_patches) -> None:
    task_id = uuid4()
    with (
        _client_for(_user(*SYSTEM_ADMIN_PERMS)) as client,
        patch(
            "app.api.v1.endpoints.admin_ai_tasks.cancel_ai_task",
            new_callable=AsyncMock,
            side_effect=AITaskStateError("only pending tasks can be cancelled"),
        ),
    ):
        response = client.post(f"/api/v1/admin/ai-tasks/{task_id}/cancel")
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_retry_and_cancel_write_audit_logs(monkeypatch) -> None:
    from app.services import ai_tasks as svc

    audits: list[dict] = []

    async def fake_audit(*_args, **kwargs) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(svc, "record_audit", fake_audit)
    monkeypatch.setattr(svc, "enqueue_ai_task", lambda *_a, **_k: None)

    now = datetime.now(UTC)

    def _mock_task(*, status: str):
        task = MagicMock()
        task.id = uuid4()
        task.status = status
        task.task_type = "RESUME_SCORE"
        task.business_type = "application"
        task.business_id = uuid4()
        task.version_id = None
        task.created_by = None
        task.error_code = None
        task.error_message = None
        task.error_category = None
        task.attempt_count = 2
        task.retry_cycle_no = 0
        task.cycle_attempt_count = 2
        task.started_at = now
        task.finished_at = now
        task.created_at = now
        task.updated_at = now
        task.attempts = []
        return task

    failed = _mock_task(status="failed")
    pending = _mock_task(status="pending")

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = failed
    execute_result.rowcount = 1

    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    actor = _user(*SYSTEM_ADMIN_PERMS)
    ctx = svc.RequestContext(request_id="audit-test")

    async def fake_get(_session, task_id, **_kwargs):
        if task_id == failed.id:
            return failed
        return pending

    monkeypatch.setattr(svc, "get_ai_task_by_id", fake_get)

    await svc.retry_ai_task(
        session, task_id=failed.id, actor=actor, request_context=ctx
    )
    execute_result.scalar_one_or_none.return_value = pending
    await svc.cancel_ai_task(
        session, task_id=pending.id, actor=actor, request_context=ctx
    )
    actions = [item["action"] for item in audits]
    assert "job.ai_task.retry" in actions
    assert "ai_task.cancel" in actions


def test_role_matrix_grants_ai_task_manage_only_to_system_admin() -> None:
    from app.services.bootstrap import PERMISSION_DEFINITIONS, ROLE_PERMISSION_MATRIX

    assert "ai_task.manage" in PERMISSION_DEFINITIONS
    assert "ai_task.manage" in ROLE_PERMISSION_MATRIX["system_admin"]
    assert "audit.read" in ROLE_PERMISSION_MATRIX["system_admin"]
    assert "ai_task.manage" not in ROLE_PERMISSION_MATRIX["recruiter_admin"]
    assert "audit.read" not in ROLE_PERMISSION_MATRIX["recruiter_admin"]
    assert "ai_task.manage" not in ROLE_PERMISSION_MATRIX["interviewer"]
    assert "audit.read" not in ROLE_PERMISSION_MATRIX["interviewer"]
    assert "recruitment.manage" in ROLE_PERMISSION_MATRIX["recruiter_admin"]


def test_to_admin_detail_strips_snapshots_and_raw_bodies() -> None:
    from app.services.ai_tasks import to_admin_detail

    now = datetime.now(UTC)
    attempt = MagicMock()
    attempt.id = uuid4()
    attempt.attempt_no = 1
    attempt.retry_cycle_no = 1
    attempt.cycle_attempt_no = 1
    attempt.status = "failed"
    attempt.started_at = now
    attempt.finished_at = now
    attempt.duration_ms = 10
    attempt.http_status = 500
    attempt.error_category = "retryable"
    attempt.error_message = "api_key=sk-secret Traceback (most recent call last): boom"
    attempt.provider_run_id = "run-abc"
    attempt.request_id = "req-xyz"
    attempt.raw_response = {"dump": "RAW_ATTEMPT"}
    attempt.response_purged_at = now

    task = MagicMock()
    task.id = uuid4()
    task.task_type = "RESUME_SCORE"
    task.business_type = "application"
    task.business_id = uuid4()
    task.status = "failed"
    task.attempt_count = 1
    task.retry_cycle_no = 1
    task.cycle_attempt_count = 1
    task.error_category = "retryable"
    task.error_code = "provider_error"
    task.error_message = "api_key=sk-secret"
    task.created_by = None
    task.created_at = now
    task.started_at = now
    task.finished_at = now
    task.input_snapshot = {"resume_text": "SECRET_RESUME"}
    task.result_payload = {"raw": "nope"}
    task.raw_request = {"authorization": "Bearer x"}
    task.raw_response = {"dump": "RAW"}
    task.attempts = [attempt]

    dumped = to_admin_detail(task).model_dump()
    dumped_text = str(dumped)
    assert "input_snapshot" not in dumped
    assert "result_payload" not in dumped
    assert "raw_response" not in dumped
    assert "raw_response" not in dumped["attempts"][0]
    assert "response_purged_at" not in dumped["attempts"][0]
    assert "SECRET_RESUME" not in dumped_text
    assert "RAW_ATTEMPT" not in dumped_text
    assert "sk-secret" not in dumped_text
    assert dumped["attempts"][0]["provider_run_id"] == "run-abc"
