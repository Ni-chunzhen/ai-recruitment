"""API tests for post-interview HiringDecision endpoints (Task 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, get_db_session
from app.main import app
from app.models import Permission, Role, User
from app.models.resume import PIPELINE_PENDING_OFFER, PIPELINE_STATUSES
from app.schemas.resume import PipelineStatus
from app.services.hiring_decisions import (
    HiringConflictError,
    HiringDecisionResult,
    HiringStateError,
    HiringValidationError,
)

ENDPOINT = "app.api.v1.endpoints.hiring_decisions"
NOW = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
APP_ID = uuid4()
VERSION_ID = uuid4()
ROUND_ID = uuid4()
DECISION_ID = uuid4()
ACTOR_ID = uuid4()


def _user(*permission_codes: str) -> User:
    user = User(
        id=ACTOR_ID,
        username="hd-api",
        username_normalized="hd-api",
        display_name="HD API",
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


def _result(**overrides) -> HiringDecisionResult:
    payload = dict(
        id=DECISION_ID,
        application_id=APP_ID,
        decision="recommend_hire",
        reason_code="meets_role_bar",
        round_id=ROUND_ID,
        analysis_version_id=VERSION_ID,
        overall_score=4.2,
        analysis_version_no=1,
        from_pipeline_status="interviewing",
        to_pipeline_status="pending_offer",
        lock_version=4,
        created_at=NOW,
        decided_by=ACTOR_ID,
    )
    payload.update(overrides)
    return HiringDecisionResult(**payload)


def _post_body(**overrides) -> dict:
    body = {
        "decision": "recommend_hire",
        "reason_code": "meets_role_bar",
        "analysis_version_id": str(VERSION_ID),
        "lock_version": 3,
        "idempotency_key": "idem-api-1",
    }
    body.update(overrides)
    return body


def test_post_hiring_decision_requires_manage(lifespan_patches) -> None:
    create = AsyncMock(return_value=_result())
    execute_client = _client_for(_user("interview.execute"))
    with patch(f"{ENDPOINT}.create_hiring_decision", new=create):
        denied = execute_client.post(
            f"/api/v1/applications/{APP_ID}/hiring-decisions",
            json=_post_body(),
        )
    assert denied.status_code == 403
    create.assert_not_called()

    manage_client = _client_for(_user("recruitment.manage"))
    with patch(f"{ENDPOINT}.create_hiring_decision", new=create):
        ok = manage_client.post(
            f"/api/v1/applications/{APP_ID}/hiring-decisions",
            json=_post_body(),
        )
    assert ok.status_code == 201
    create.assert_awaited_once()


def test_get_hiring_history_requires_manage(lifespan_patches) -> None:
    listed = AsyncMock(return_value=[_result()])
    execute_client = _client_for(_user("interview.execute"))
    with patch(f"{ENDPOINT}.list_hiring_decisions", new=listed):
        denied = execute_client.get(f"/api/v1/applications/{APP_ID}/hiring-decisions")
    assert denied.status_code == 403
    listed.assert_not_called()

    manage_client = _client_for(_user("recruitment.manage"))
    with patch(f"{ENDPOINT}.list_hiring_decisions", new=listed):
        ok = manage_client.get(f"/api/v1/applications/{APP_ID}/hiring-decisions")
    assert ok.status_code == 200
    assert ok.headers.get("cache-control") == "no-store"
    assert len(ok.json()["items"]) == 1


def test_reason_codes_requires_manage_and_has_twelve(lifespan_patches) -> None:
    execute_client = _client_for(_user("interview.execute"))
    denied = execute_client.get("/api/v1/hiring-decision-reason-codes")
    assert denied.status_code == 403

    manage_client = _client_for(_user("recruitment.manage"))
    ok = manage_client.get("/api/v1/hiring-decision-reason-codes")
    assert ok.status_code == 200
    items = ok.json()["items"]
    assert len(items) == 12
    for item in items:
        assert "requires_description" not in item
        assert set(item.keys()) == {"code", "label", "allowed_decisions"}


def test_post_body_forbids_free_text_reason_field(lifespan_patches) -> None:
    create = AsyncMock(return_value=_result())
    client = _client_for(_user("recruitment.manage"))
    body = _post_body()
    body["reason"] = "自由文本不应出现"
    with patch(f"{ENDPOINT}.create_hiring_decision", new=create):
        response = client.post(
            f"/api/v1/applications/{APP_ID}/hiring-decisions",
            json=body,
        )
    assert response.status_code == 422
    create.assert_not_called()


def test_post_recommend_hire_api_contract(lifespan_patches) -> None:
    create = AsyncMock(return_value=_result())
    client = _client_for(_user("recruitment.manage"))
    with patch(f"{ENDPOINT}.create_hiring_decision", new=create):
        response = client.post(
            f"/api/v1/applications/{APP_ID}/hiring-decisions",
            json=_post_body(),
        )
    assert response.status_code == 201
    data = response.json()
    assert data["to_pipeline_status"] == "pending_offer"
    assert data["decision"] == "recommend_hire"
    assert data["reason_code"] == "meets_role_bar"
    assert "reason" not in data
    assert "quote" not in data
    assert "summary" not in data
    assert "offer_id" not in data
    assert "send_offer" not in data
    assert "hired" not in data
    assert "transcript" not in data
    assert set(data.keys()) == {
        "id",
        "application_id",
        "decision",
        "reason_code",
        "round_id",
        "analysis_version_id",
        "overall_score",
        "analysis_version_no",
        "from_pipeline_status",
        "to_pipeline_status",
        "lock_version",
        "created_at",
        "decided_by",
    }


def test_post_conflict_and_state_return_409(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.create_hiring_decision",
        new=AsyncMock(side_effect=HiringConflictError("refresh and retry")),
    ):
        conflict = client.post(
            f"/api/v1/applications/{APP_ID}/hiring-decisions",
            json=_post_body(),
        )
    assert conflict.status_code == 409

    with patch(
        f"{ENDPOINT}.create_hiring_decision",
        new=AsyncMock(side_effect=HiringStateError("analysis version is stale")),
    ):
        stale = client.post(
            f"/api/v1/applications/{APP_ID}/hiring-decisions",
            json=_post_body(),
        )
    assert stale.status_code == 409


def test_post_validation_returns_422(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.create_hiring_decision",
        new=AsyncMock(side_effect=HiringValidationError("invalid reason_code")),
    ):
        response = client.post(
            f"/api/v1/applications/{APP_ID}/hiring-decisions",
            json=_post_body(),
        )
    assert response.status_code == 422


def test_post_idempotent_replay_returns_same_payload(lifespan_patches) -> None:
    create = AsyncMock(return_value=_result(lock_version=4))
    client = _client_for(_user("recruitment.manage"))
    path = f"/api/v1/applications/{APP_ID}/hiring-decisions"
    body = _post_body(idempotency_key="same-key")
    with patch(f"{ENDPOINT}.create_hiring_decision", new=create):
        first = client.post(path, json=body)
        second = client.post(path, json=body)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"] == str(DECISION_ID)
    assert create.await_count == 2


def test_pipeline_status_literal_accepts_pending_offer() -> None:
    assert PIPELINE_PENDING_OFFER in PIPELINE_STATUSES
    assert "pending_offer" in PipelineStatus.__args__  # type: ignore[attr-defined]


def test_output_schema_has_no_sensitive_or_offer_fields() -> None:
    from app.schemas.hiring_decision import HiringDecisionOut, HiringDecisionRequest

    req_fields = set(HiringDecisionRequest.model_fields)
    assert req_fields == {
        "decision",
        "reason_code",
        "analysis_version_id",
        "lock_version",
        "idempotency_key",
    }
    assert "reason" not in req_fields
    out_fields = set(HiringDecisionOut.model_fields)
    for banned in ("reason", "quote", "summary", "offer_id", "transcript_text"):
        assert banned not in out_fields
