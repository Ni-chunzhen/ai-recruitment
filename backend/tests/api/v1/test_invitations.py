"""API acceptance tests for manual invitation RBAC and response boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, get_db_session
from app.main import app
from app.models import Permission, Role, User
from app.schemas.invitation import (
    InvitationListResponse,
    InvitationMessageDetailOut,
    InvitationMessageSummaryOut,
    InvitationStatusCountsOut,
)
from app.services.interviews import InterviewNotFoundError, InterviewValidationError

MSG_ID = uuid4()
ROUND_ID = uuid4()
NOW = datetime.now(UTC)


def _user(*permission_codes: str) -> User:
    user = User(
        id=uuid4(),
        username="invite-tester",
        username_normalized="invite-tester",
        display_name="Invite Tester",
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


def _summary(**overrides) -> InvitationMessageSummaryOut:
    data = {
        "id": MSG_ID,
        "interview_round_id": ROUND_ID,
        "schedule_id": uuid4(),
        "schedule_version": 1,
        "event_type": "INITIAL",
        "audience_type": "INTERVIEWER",
        "recipient_user_id": uuid4(),
        "recipient_key": "u1",
        "recipient_name": "王面试",
        "recipient_email_masked": "w***@example.com",
        "status": "READY",
        "current_version_id": uuid4(),
        "current_version_no": 1,
        "template_code": "interviewer_initial",
        "version": 1,
        "missing_fields": [],
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return InvitationMessageSummaryOut.model_validate(data)


def _detail(**overrides) -> InvitationMessageDetailOut:
    base = _summary().model_dump()
    base.update(
        {
            "subject": "主题",
            "body_html": "<p>正文</p>",
            "body_text": "正文",
            "template_code": "interviewer_initial",
            "template_version": "1",
            "content_hash": "abc",
            "current_version_no": 1,
        }
    )
    base.update(overrides)
    return InvitationMessageDetailOut.model_validate(base)


def test_manage_can_read_invitation_detail(lifespan_patches) -> None:
    user = _user("recruitment.manage")
    client = _client_for(user)
    with patch(
        "app.api.v1.endpoints.invitations.get_invitation_detail",
        new=AsyncMock(return_value=_detail()),
    ):
        response = client.get(f"/api/v1/interview-invitations/{MSG_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "主题"
    assert "enc:v1:" not in str(body)
    assert "subject_encrypted" not in body
    assert response.headers.get("cache-control") == "no-store"


def test_list_invitations_does_not_return_body(lifespan_patches) -> None:
    user = _user("recruitment.manage")
    client = _client_for(user)
    payload = InvitationListResponse(
        items=[_summary()],
        counts=InvitationStatusCountsOut(generated=0, ready=1, recorded_sent=0, voided=0),
    )
    with patch(
        "app.api.v1.endpoints.invitations.list_invitations",
        new=AsyncMock(return_value=payload),
    ):
        response = client.get(f"/api/v1/interview-rounds/{ROUND_ID}/invitations")
    assert response.status_code == 200
    body = response.json()
    assert "subject" not in body["items"][0]
    assert "body_html" not in body["items"][0]
    assert "body_text" not in body["items"][0]
    assert "enc:v1:" not in str(body)


def test_execute_forbidden_on_generate(lifespan_patches) -> None:
    user = _user("interview.execute")
    client = _client_for(user)
    response = client.post(
        f"/api/v1/interview-rounds/{ROUND_ID}/invitations/generate",
        json={"idempotency_key": "k1"},
    )
    assert response.status_code == 403


def test_unassigned_or_candidate_mail_returns_404(lifespan_patches) -> None:
    user = _user("interview.execute")
    client = _client_for(user)
    with patch(
        "app.api.v1.endpoints.invitations.get_invitation_detail",
        new=AsyncMock(side_effect=InterviewNotFoundError("invitation not found")),
    ):
        response = client.get(f"/api/v1/interview-invitations/{MSG_ID}")
    assert response.status_code == 404


def test_detail_decryption_failure_maps_to_400(lifespan_patches) -> None:
    user = _user("recruitment.manage")
    client = _client_for(user)
    with patch(
        "app.api.v1.endpoints.invitations.get_invitation_detail",
        new=AsyncMock(
            side_effect=InterviewValidationError("invitation content decryption failed")
        ),
    ):
        response = client.get(f"/api/v1/interview-invitations/{MSG_ID}")
    assert response.status_code == 400
    assert "decryption failed" in response.json()["detail"]
