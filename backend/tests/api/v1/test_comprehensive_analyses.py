"""API tests for comprehensive interview analysis endpoints (Task 4)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, get_db_session
from app.main import app
from app.models import Permission, Role, User
from app.models.ai_task import (
    AI_TASK_STATUS_PENDING,
    TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
)
from app.services.interviews import (
    InterviewConflictError,
    InterviewNotFoundError,
    InterviewValidationError,
)

ENDPOINT = "app.api.v1.endpoints.comprehensive_analyses"
NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)
APP_ID = uuid4()
ANALYSIS_ID = uuid4()
VERSION_ID = uuid4()
TASK_ID = uuid4()
ACTOR_ID = uuid4()


def _user(*permission_codes: str) -> User:
    user = User(
        id=ACTOR_ID,
        username="comp-api",
        username_normalized="comp-api",
        display_name="Comp API",
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


def _task():
    return SimpleNamespace(
        id=TASK_ID,
        task_type=TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
        status=AI_TASK_STATUS_PENDING,
        business_id=APP_ID,
    )


def _set_summary():
    return SimpleNamespace(
        analysis_id=ANALYSIS_ID,
        application_id=APP_ID,
        current_version_id=VERSION_ID,
        versions=[
            SimpleNamespace(
                analysis_id=ANALYSIS_ID,
                version_id=VERSION_ID,
                version_no=1,
                version_label="C1",
                ai_task_id=TASK_ID,
                overall_score=Decimal("4.00"),
                coverage_report={
                    "eligible_round_count": 1,
                    "total_round_count": 1,
                    "included_rounds": [],
                    "gaps": [],
                    "coverage_insufficient": False,
                    "single_round_only": True,
                    "missing_round_count": 0,
                },
                created_by=ACTOR_ID,
                created_at=NOW,
                is_current=True,
                is_stale=False,
            )
        ],
    )


def _detail():
    return SimpleNamespace(
        analysis_id=ANALYSIS_ID,
        version_id=VERSION_ID,
        version_no=1,
        version_label="C1",
        ai_task_id=TASK_ID,
        overall_score=Decimal("4.00"),
        overall_summary="辅助综合短评",
        round_refs=[
            {
                "round_id": str(uuid4()),
                "sequence_no": 1,
                "analysis_version_id": str(uuid4()),
                "analysis_version_no": 1,
                "overall_score": 4.0,
                "dimensions": [
                    {
                        "dimension_key": "collab",
                        "dimension_name": "协作",
                        "weight": 100.0,
                        "score": 4,
                        "insufficient_information": False,
                    }
                ],
                "evidence_refs": [],
            }
        ],
        coverage_report={
            "eligible_round_count": 1,
            "total_round_count": 1,
            "included_rounds": [],
            "gaps": [],
            "coverage_insufficient": False,
            "single_round_only": True,
            "missing_round_count": 0,
        },
        created_by=ACTOR_ID,
        created_at=NOW,
        is_current=True,
        is_stale=False,
    )


def test_generate_requires_manage(lifespan_patches) -> None:
    request_gen = AsyncMock(return_value=_task())
    dispatch = AsyncMock()
    execute_client = _client_for(_user("interview.execute"))
    with (
        patch(f"{ENDPOINT}.request_comprehensive_analysis_generation", new=request_gen),
        patch(
            f"{ENDPOINT}.dispatch_persisted_comprehensive_analysis_task",
            new=dispatch,
        ),
    ):
        denied = execute_client.post(
            f"/api/v1/applications/{APP_ID}/comprehensive-analysis/generate",
            json={"idempotency_key": "idem-1"},
        )
    assert denied.status_code == 403
    request_gen.assert_not_called()
    dispatch.assert_not_called()


def test_get_requires_manage(lifespan_patches) -> None:
    list_fn = AsyncMock(return_value=_set_summary())
    detail_fn = AsyncMock(return_value=_detail())
    execute_client = _client_for(_user("interview.execute"))
    with (
        patch(f"{ENDPOINT}.list_comprehensive_analysis", new=list_fn),
        patch(
            f"{ENDPOINT}.get_comprehensive_analysis_version_detail",
            new=detail_fn,
        ),
    ):
        listed = execute_client.get(
            f"/api/v1/applications/{APP_ID}/comprehensive-analysis"
        )
        detail = execute_client.get(
            f"/api/v1/applications/{APP_ID}/comprehensive-analysis/versions/{VERSION_ID}"
        )
    assert listed.status_code == 403
    assert detail.status_code == 403
    list_fn.assert_not_called()
    detail_fn.assert_not_called()


def test_generate_rejects_pending_offer(lifespan_patches) -> None:
    request_gen = AsyncMock(
        side_effect=InterviewConflictError(
            "only interviewing applications can generate comprehensive analysis"
        )
    )
    client = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.request_comprehensive_analysis_generation", new=request_gen
    ):
        response = client.post(
            f"/api/v1/applications/{APP_ID}/comprehensive-analysis/generate",
            json={"idempotency_key": "idem-po"},
        )
    assert response.status_code in {400, 409}
    assert "interviewing" in response.json()["detail"].lower()


def test_generate_accepted_for_interviewing(lifespan_patches) -> None:
    request_gen = AsyncMock(return_value=_task())
    dispatch = AsyncMock()
    client = _client_for(_user("recruitment.manage"))
    with (
        patch(f"{ENDPOINT}.request_comprehensive_analysis_generation", new=request_gen),
        patch(
            f"{ENDPOINT}.dispatch_persisted_comprehensive_analysis_task",
            new=dispatch,
        ),
    ):
        response = client.post(
            f"/api/v1/applications/{APP_ID}/comprehensive-analysis/generate",
            json={"idempotency_key": "idem-ok"},
        )
    assert response.status_code == 202
    data = response.json()
    assert data["task_id"] == str(TASK_ID)
    assert data["task_type"] == TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE
    assert data["status"] == AI_TASK_STATUS_PENDING
    assert data["application_id"] == str(APP_ID)
    assert data["dispatch_status"] == "queued"
    assert "offer" not in data
    assert "hiring" not in str(data).lower()
    dispatch.assert_awaited_once()


def test_detail_sets_no_store(lifespan_patches) -> None:
    list_fn = AsyncMock(return_value=_set_summary())
    detail_fn = AsyncMock(return_value=_detail())
    client = _client_for(_user("recruitment.manage"))
    with (
        patch(f"{ENDPOINT}.list_comprehensive_analysis", new=list_fn),
        patch(
            f"{ENDPOINT}.get_comprehensive_analysis_version_detail",
            new=detail_fn,
        ),
    ):
        listed = client.get(f"/api/v1/applications/{APP_ID}/comprehensive-analysis")
        detail = client.get(
            f"/api/v1/applications/{APP_ID}/comprehensive-analysis/versions/{VERSION_ID}"
        )
    assert listed.status_code == 200
    assert listed.headers.get("cache-control") == "no-store"
    assert detail.status_code == 200
    assert detail.headers.get("cache-control") == "no-store"
    body = detail.json()
    assert "overall_summary" in body
    assert "coverage_report" in body
    assert "round_refs" in body
    assert "is_stale" in body
    assert "is_current" in body
    blob = str(body).lower()
    assert "quote" not in blob or all(
        "quote" not in (ref.keys() if isinstance(ref, dict) else [])
        for ref in body.get("round_refs") or []
    )
    assert "hiring_decision" not in blob
    assert "offer" not in blob
    assert "notification" not in blob
    assert "jd_text" not in blob
    assert "resume_text" not in blob
    assert "transcript" not in blob


def test_generate_maps_validation_and_not_found(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.request_comprehensive_analysis_generation",
        new=AsyncMock(side_effect=InterviewValidationError("no eligible")),
    ):
        bad = client.post(
            f"/api/v1/applications/{APP_ID}/comprehensive-analysis/generate",
            json={"idempotency_key": "idem-bad"},
        )
    assert bad.status_code in {400, 409}

    with patch(
        f"{ENDPOINT}.list_comprehensive_analysis",
        new=AsyncMock(side_effect=InterviewNotFoundError("missing")),
    ):
        missing = client.get(
            f"/api/v1/applications/{APP_ID}/comprehensive-analysis"
        )
    assert missing.status_code == 404
