from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, get_db_session
from app.api.v1.endpoints import candidate_center
from app.main import app
from app.models import Permission, Role, User
from app.repositories.candidates import CandidateNotFoundError
from app.schemas.candidate_center import (
    CandidateCenterDetailOut,
    CandidateCenterListItem,
    CandidateCenterListResponse,
)


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


def _list_response() -> CandidateCenterListResponse:
    return CandidateCenterListResponse(
        items=[
            CandidateCenterListItem(
                application_id=uuid4(),
                candidate_id=uuid4(),
                name="张三",
                phone="13800000000",
                email="zhang@example.com",
                job_id=uuid4(),
                job_name="后端工程师",
                job_code="JOB-202608-0001",
                job_version_id=uuid4(),
                job_version_label="V1.0",
                status="in_progress",
                pipeline_status="interviewing",
                round_id=uuid4(),
                round_name="一轮",
                sequence_no=1,
                round_status="CANCELLED",
                schedule_status="none",
                invitation_status="none",
                transcript_status="none",
                question_status="none",
                analysis_status="none",
            )
        ],
        total=1,
        page=1,
        page_size=20,
    )


def _detail_out(*, candidate_id, application_id) -> CandidateCenterDetailOut:
    return CandidateCenterDetailOut(
        application_id=application_id,
        candidate_id=candidate_id,
        name="张三",
        phone="13800000000",
        email=None,
        job_id=uuid4(),
        job_name="后端工程师",
        job_code="JOB-202608-0001",
        job_version_id=uuid4(),
        job_version_label="V1.0",
        status="in_progress",
        pipeline_status="interviewing",
        close_action=None,
        interview_started=True,
        resume_summary=None,
        score_summary=None,
        rounds=[],
        other_applications=[],
    )


def test_list_requires_recruitment_manage(lifespan_patches) -> None:
    mocked = AsyncMock(return_value=_list_response())
    with patch(
        "app.api.v1.endpoints.candidate_center.list_candidate_center_applications",
        new=mocked,
    ):
        none_client = _client_for(_user("profile.read"))
        none_response = none_client.get("/api/v1/candidate-center/applications")
        execute_client = _client_for(_user("interview.execute"))
        execute_response = execute_client.get("/api/v1/candidate-center/applications")
    assert none_response.status_code == 403
    assert none_response.json()["detail"] == "forbidden"
    assert execute_response.status_code == 403
    assert execute_response.json()["detail"] == "forbidden"
    mocked.assert_not_called()


def test_list_manage_ok_defaults_assigned_true(lifespan_patches) -> None:
    mocked = AsyncMock(return_value=_list_response())
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.candidate_center.list_candidate_center_applications",
        new=mocked,
    ):
        response = client.get("/api/v1/candidate-center/applications")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    query = mocked.await_args.kwargs["query"]
    assert query.assigned is True
    assert query.page == 1
    assert query.page_size == 20


def test_list_assigned_false_forwards_flag(lifespan_patches) -> None:
    mocked = AsyncMock(return_value=_list_response())
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.candidate_center.list_candidate_center_applications",
        new=mocked,
    ):
        response = client.get(
            "/api/v1/candidate-center/applications",
            params={"assigned": "false"},
        )
    assert response.status_code == 200
    assert mocked.await_args.kwargs["query"].assigned is False


def test_list_rejects_unknown_query_param(lifespan_patches) -> None:
    mocked = AsyncMock(return_value=_list_response())
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.candidate_center.list_candidate_center_applications",
        new=mocked,
    ):
        response = client.get("/api/v1/candidate-center/applications?foo=1")
    assert response.status_code == 400
    mocked.assert_not_called()


def test_list_rejects_unknown_query_param_with_valid_fields(lifespan_patches) -> None:
    mocked = AsyncMock(return_value=_list_response())
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.candidate_center.list_candidate_center_applications",
        new=mocked,
    ):
        response = client.get(
            "/api/v1/candidate-center/applications?assigned=true&foo=1"
        )
    assert response.status_code == 400
    mocked.assert_not_called()


def test_list_rejects_invalid_status(lifespan_patches) -> None:
    mocked = AsyncMock(return_value=_list_response())
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.candidate_center.list_candidate_center_applications",
        new=mocked,
    ):
        response = client.get(
            "/api/v1/candidate-center/applications?status=interviewing"
        )
    assert response.status_code == 400
    mocked.assert_not_called()


def test_list_rejects_invalid_pipeline_status(lifespan_patches) -> None:
    mocked = AsyncMock(return_value=_list_response())
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.candidate_center.list_candidate_center_applications",
        new=mocked,
    ):
        response = client.get(
            "/api/v1/candidate-center/applications?pipeline_status=in_progress"
        )
    assert response.status_code == 400
    mocked.assert_not_called()


def test_list_rejects_invalid_sort(lifespan_patches) -> None:
    mocked = AsyncMock(return_value=_list_response())
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.candidate_center.list_candidate_center_applications",
        new=mocked,
    ):
        response = client.get(
            "/api/v1/candidate-center/applications?sort=score_desc"
        )
    assert response.status_code == 400
    mocked.assert_not_called()


def test_list_does_not_set_no_store(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.candidate_center.list_candidate_center_applications",
        new=AsyncMock(return_value=_list_response()),
    ):
        response = client.get("/api/v1/candidate-center/applications")
    assert response.status_code == 200
    assert response.headers.get("cache-control") != "no-store"


def test_list_body_has_no_sensitive_keys(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.candidate_center.list_candidate_center_applications",
        new=AsyncMock(return_value=_list_response()),
    ):
        response = client.get("/api/v1/candidate-center/applications")
    assert response.status_code == 200
    raw = response.text
    assert "extracted_text" not in raw
    assert "raw_output" not in raw
    assert "question_encrypted" not in raw
    assert "quote" not in raw


def test_detail_requires_recruitment_manage(lifespan_patches) -> None:
    candidate_id = uuid4()
    application_id = uuid4()
    mocked = AsyncMock(
        return_value=_detail_out(
            candidate_id=candidate_id, application_id=application_id
        )
    )
    client = _client_for(_user("interview.execute"))
    with patch(
        "app.api.v1.endpoints.candidate_center.get_candidate_center_application_detail",
        new=mocked,
    ):
        response = client.get(
            f"/api/v1/candidate-center/candidates/{candidate_id}/applications/{application_id}"
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "forbidden"
    mocked.assert_not_called()


def test_detail_mismatch_is_404_not_found(lifespan_patches) -> None:
    candidate_id = uuid4()
    application_id = uuid4()
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.candidate_center.get_candidate_center_application_detail",
        new=AsyncMock(side_effect=CandidateNotFoundError("not found")),
    ):
        response = client.get(
            f"/api/v1/candidate-center/candidates/{candidate_id}/applications/{application_id}"
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "not found"


def test_detail_sets_no_store(lifespan_patches) -> None:
    candidate_id = uuid4()
    application_id = uuid4()
    mocked = AsyncMock(
        return_value=_detail_out(
            candidate_id=candidate_id, application_id=application_id
        )
    )
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.candidate_center.get_candidate_center_application_detail",
        new=mocked,
    ):
        response = client.get(
            f"/api/v1/candidate-center/candidates/{candidate_id}/applications/{application_id}"
        )
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    assert mocked.await_args.kwargs["candidate_id"] == candidate_id
    assert mocked.await_args.kwargs["application_id"] == application_id


def test_router_registers_two_get_routes() -> None:
    routes = [
        route
        for route in candidate_center.router.routes
        if isinstance(route, APIRoute)
    ]
    assert len(routes) == 2
    assert all("GET" in route.methods for route in routes)
    assert all(
        method in {"GET", "HEAD"}
        for route in routes
        for method in route.methods
    )
    paths = {route.path for route in routes}
    assert any(path.endswith("/applications") for path in paths)
    assert any(
        "/candidates/{candidate_id}/applications/{application_id}" in path
        for path in paths
    )
    source = inspect.getsource(candidate_center)
    assert source.count('require_permission("recruitment.manage")') >= 2


def test_router_included_in_api_v1() -> None:
    from app.api.v1 import router as v1

    source = inspect.getsource(v1)
    assert "candidate_center" in source
