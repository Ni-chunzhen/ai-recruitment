from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, get_db_session
from app.main import app
from app.models import Permission, Role, User
from app.schemas.interview import (
    InterviewConflictOut,
    InterviewReasonCodeListResponse,
    InterviewRoundActionOut,
    InterviewRoundOut,
    InterviewTimelineOut,
)
from app.services.interviews import (
    InterviewConflictError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
)

APP_ID = uuid4()
ROUND_ID = uuid4()
NOW = datetime.now(UTC)


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


def _round_out(**overrides) -> dict:
    payload = {
        "id": str(ROUND_ID),
        "application_id": str(APP_ID),
        "job_version_id": str(uuid4()),
        "name": "第一轮专业面",
        "sequence_no": 1,
        "status": "SCHEDULED",
        "format": "ONLINE",
        "owner_id": str(uuid4()),
        "owner_name": "王磊",
        "interviewers": [
            {
                "interviewer_id": str(uuid4()),
                "display_name": "王磊",
                "is_primary": True,
            }
        ],
        "current_schedule": {
            "id": str(uuid4()),
            "schedule_version": 1,
            "status": "ACTIVE",
            "start_at_utc": NOW.isoformat(),
            "end_at_utc": NOW.isoformat(),
            "timezone": "Asia/Shanghai",
            "format": "ONLINE",
            "meeting_mode": "MANUAL",
            "meeting_provider": None,
            "meeting_url": "https://meet.example.com/abc",
            "meeting_no": "123",
            "has_meeting_password": False,
            "location": None,
            "contact_name": None,
            "contact_phone_masked": None,
            "reschedule_reason": None,
            "created_at": NOW.isoformat(),
        },
        "schedule_history": [],
        "version": 1,
        "allowed_actions": ["edit", "reschedule", "cancel", "start"],
        "cancellation_reason_code": None,
        "cancellation_description": None,
        "abnormal_reason_code": None,
        "abnormal_description": None,
        "started_at": None,
        "finished_at": None,
        "cancelled_at": None,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    payload.update(overrides)
    return payload


def _timeline() -> InterviewTimelineOut:
    return InterviewTimelineOut.model_validate(
        {
            "application_id": str(APP_ID),
            "candidate_id": str(uuid4()),
            "candidate_name": "张三",
            "job_id": str(uuid4()),
            "job_name": "前端工程师",
            "job_version_id": str(uuid4()),
            "job_version_label": "V1.0",
            "pipeline_status": "interviewing",
            "application_status": "in_progress",
            "completed_round_count": 0,
            "total_round_count": 1,
            "rounds": [_round_out()],
        }
    )


def test_reason_codes_endpoint(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    catalog = InterviewReasonCodeListResponse.model_validate(
        {
            "items": [
                {
                    "code": "CANDIDATE_RESCHEDULE",
                    "label": "候选人改期",
                    "category": "cancel",
                    "requires_description": False,
                },
                {
                    "code": "OTHER",
                    "label": "其他",
                    "category": "cancel",
                    "requires_description": True,
                },
            ]
        }
    )
    with patch(
        "app.api.v1.endpoints.interviews.list_interview_reason_codes",
        return_value=catalog,
    ):
        response = client.get("/api/v1/interview-reason-codes")
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()["items"]}
    assert "CANDIDATE_RESCHEDULE" in codes
    assert "OTHER" in codes


def test_list_timeline_requires_manage_or_execute(lifespan_patches) -> None:
    client = _client_for(_user("profile.read"))
    response = client.get(f"/api/v1/applications/{APP_ID}/interview-rounds")
    assert response.status_code == 403


def test_list_timeline_ok_for_recruiter(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interviews.get_interview_timeline",
        new_callable=AsyncMock,
        return_value=_timeline(),
    ):
        response = client.get(f"/api/v1/applications/{APP_ID}/interview-rounds")
    assert response.status_code == 200
    body = response.json()
    assert body["pipeline_status"] == "interviewing"
    assert body["rounds"][0]["sequence_no"] == 1
    dumped = str(body)
    assert "meeting_password_encrypted" not in dumped
    assert '"meeting_password"' not in dumped
    assert "has_meeting_password" in dumped


def test_create_round_forbidden_for_interviewer_only(lifespan_patches) -> None:
    client = _client_for(_user("interview.execute"))
    response = client.post(
        f"/api/v1/applications/{APP_ID}/interview-rounds",
        json={
            "name": "一轮",
            "format": "ONLINE",
            "owner_id": str(uuid4()),
            "interviewers": [{"interviewer_id": str(uuid4()), "is_primary": True}],
            "idempotency_key": "k1",
        },
    )
    assert response.status_code == 403


def test_create_round_ok(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    created = InterviewRoundOut.model_validate(_round_out(status="DRAFT"))
    with patch(
        "app.api.v1.endpoints.interviews.create_interview_round",
        new_callable=AsyncMock,
        return_value=created,
    ):
        response = client.post(
            f"/api/v1/applications/{APP_ID}/interview-rounds",
            json={
                "name": "一轮",
                "format": "ONLINE",
                "owner_id": str(uuid4()),
                "interviewers": [{"interviewer_id": str(uuid4()), "is_primary": True}],
                "idempotency_key": "k1",
            },
        )
    assert response.status_code == 201
    assert response.json()["status"] == "DRAFT"


def test_update_optimistic_lock_409(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interviews.update_interview_round",
        new_callable=AsyncMock,
        side_effect=InterviewOptimisticLockError(
            "面试信息已被其他人员更新，请刷新后重试"
        ),
    ):
        response = client.put(
            f"/api/v1/interview-rounds/{ROUND_ID}",
            json={
                "name": "改名",
                "version": 1,
                "interviewers": [{"interviewer_id": str(uuid4()), "is_primary": True}],
            },
        )
    assert response.status_code == 409
    assert "刷新" in response.json()["detail"]


def test_idempotency_conflict_409(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interviews.create_interview_round",
        new_callable=AsyncMock,
        side_effect=InterviewIdempotencyConflictError("idempotency conflict"),
    ):
        response = client.post(
            f"/api/v1/applications/{APP_ID}/interview-rounds",
            json={
                "name": "一轮",
                "format": "ONLINE",
                "owner_id": str(uuid4()),
                "interviewers": [{"interviewer_id": str(uuid4()), "is_primary": True}],
                "idempotency_key": "k1",
            },
        )
    assert response.status_code == 409


def test_interviewer_forbidden_on_reschedule(lifespan_patches) -> None:
    client = _client_for(_user("interview.execute"))
    response = client.post(
        f"/api/v1/interview-rounds/{ROUND_ID}/reschedule",
        json={
            "start_at_utc": NOW.isoformat(),
            "end_at_utc": NOW.isoformat(),
            "timezone": "Asia/Shanghai",
            "format": "ONLINE",
            "meeting_mode": "MANUAL",
            "meeting_url": "https://meet.example.com/x",
            "reschedule_reason": "改期",
            "version": 1,
        },
    )
    assert response.status_code == 403


def test_schedule_start_finish_complete_cancel_abnormal(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage", "interview.execute"))
    action = InterviewRoundActionOut.model_validate(_round_out())
    mapping = [
        (f"/api/v1/interview-rounds/{ROUND_ID}/schedule", "schedule_interview_round", {
            "start_at_utc": NOW.isoformat(),
            "end_at_utc": NOW.isoformat(),
            "timezone": "Asia/Shanghai",
            "format": "ONLINE",
            "meeting_mode": "MANUAL",
            "meeting_url": "https://meet.example.com/x",
            "version": 1,
            "idempotency_key": "s1",
        }),
        (f"/api/v1/interview-rounds/{ROUND_ID}/start", "start_interview_round", {
            "version": 1,
            "idempotency_key": "st1",
        }),
        (f"/api/v1/interview-rounds/{ROUND_ID}/finish", "finish_interview_round", {
            "version": 1,
            "idempotency_key": "f1",
        }),
        (f"/api/v1/interview-rounds/{ROUND_ID}/complete", "complete_interview_round", {
            "version": 1,
            "idempotency_key": "c1",
        }),
        (f"/api/v1/interview-rounds/{ROUND_ID}/cancel", "cancel_interview_round", {
            "reason_code": "CANDIDATE_WITHDRAWAL",
            "version": 1,
            "idempotency_key": "ca1",
        }),
        (
            f"/api/v1/interview-rounds/{ROUND_ID}/end-abnormally",
            "end_interview_abnormally",
            {
            "reason_code": "CANDIDATE_NO_SHOW",
            "version": 1,
            "idempotency_key": "e1",
        }),
    ]
    for path, fn, payload in mapping:
        with patch(
            f"app.api.v1.endpoints.interviews.{fn}",
            new_callable=AsyncMock,
            return_value=action,
        ):
            response = client.post(path, json=payload)
        assert response.status_code == 200, path


def test_unassigned_interviewer_gets_404(lifespan_patches) -> None:
    client = _client_for(_user("interview.execute"))
    with patch(
        "app.api.v1.endpoints.interviews.get_interview_timeline",
        new_callable=AsyncMock,
        side_effect=InterviewNotFoundError("not found"),
    ):
        response = client.get(f"/api/v1/applications/{APP_ID}/interview-rounds")
    assert response.status_code == 404


def test_conflict_check_candidate_blocks(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interviews.check_interview_conflicts",
        new_callable=AsyncMock,
        side_effect=InterviewConflictError("candidate conflict"),
    ):
        response = client.post(
            "/api/v1/interview-rounds/conflicts/check",
            json={
                "application_id": str(APP_ID),
                "interviewer_ids": [str(uuid4())],
                "start_at_utc": NOW.isoformat(),
                "end_at_utc": NOW.isoformat(),
                "timezone": "Asia/Shanghai",
            },
        )
    assert response.status_code == 409


def test_conflict_check_ok_payload_hides_unrelated_candidate(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    out = InterviewConflictOut.model_validate(
        {
            "has_candidate_conflict": False,
            "has_interviewer_conflict": True,
            "candidate_conflicts": [],
            "interviewer_conflicts": [
                {
                    "interviewer_id": str(uuid4()),
                    "interviewer_name": "李四",
                    "round_id": str(uuid4()),
                    "round_name": "其他轮次",
                    "start_at_utc": NOW.isoformat(),
                    "end_at_utc": NOW.isoformat(),
                }
            ],
        }
    )
    with patch(
        "app.api.v1.endpoints.interviews.check_interview_conflicts",
        new_callable=AsyncMock,
        return_value=out,
    ):
        response = client.post(
            "/api/v1/interview-rounds/conflicts/check",
            json={
                "application_id": str(APP_ID),
                "interviewer_ids": [str(uuid4())],
                "start_at_utc": NOW.isoformat(),
                "end_at_utc": NOW.isoformat(),
                "timezone": "Asia/Shanghai",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert "phone" not in str(body).lower()
    assert "email" not in str(body).lower()


def test_reorder_endpoint(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interviews.reorder_interview_rounds",
        new_callable=AsyncMock,
        return_value=_timeline(),
    ):
        response = client.post(
            f"/api/v1/applications/{APP_ID}/interview-rounds/reorder",
            json={"round_ids": [str(ROUND_ID)]},
        )
    assert response.status_code == 200


def test_validation_error_400(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interviews.create_interview_round",
        new_callable=AsyncMock,
        side_effect=InterviewValidationError("application must be interviewing"),
    ):
        response = client.post(
            f"/api/v1/applications/{APP_ID}/interview-rounds",
            json={
                "name": "一轮",
                "format": "ONLINE",
                "owner_id": str(uuid4()),
                "interviewers": [{"interviewer_id": str(uuid4()), "is_primary": True}],
            },
        )
    assert response.status_code == 400
