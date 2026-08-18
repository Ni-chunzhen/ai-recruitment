"""API tests for stage 8 interview question and analysis endpoints."""

from __future__ import annotations

import inspect
import json
from contextlib import ExitStack
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
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
)
from app.models.interview_ai import (
    QUESTION_SET_STATUS_READY,
    QUESTION_SOURCE_AI_GENERATED,
    QUESTION_SOURCE_MANUAL_EDIT,
)
from app.services.interview_analyses import (
    AnalysisDimensionDetail,
    AnalysisEvidenceDetail,
    AnalysisSetSummary,
    AnalysisVersionDetail,
    AnalysisVersionSummary,
)
from app.services.interview_questions import (
    QuestionItemDetail,
    QuestionSetSummary,
    QuestionVersionDetail,
    QuestionVersionSummary,
)
from app.services.interviews import (
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
)

ROUND_ID = uuid4()
VERSION_ID = uuid4()
SET_ID = uuid4()
ANALYSIS_ID = uuid4()
TASK_ID = uuid4()
NOW = datetime.now(UTC)
SECRET_QUESTION = "SECRET_QUESTION_BODY"
SECRET_ANALYSIS = "SECRET_ANALYSIS_BODY"
SECRET_QUOTE = "SECRET_EVIDENCE_QUOTE"
CIPHER = "enc:v1:not-for-http"

ENDPOINT = "app.api.v1.endpoints.interview_ai"
FORBIDDEN_BODY_MARKERS = (
    SECRET_QUESTION,
    SECRET_ANALYSIS,
    SECRET_QUOTE,
    CIPHER,
    "raw_request",
    "raw_response",
    "sensitive_request",
    "sensitive_response",
)


def _user(*permission_codes: str) -> User:
    user = User(
        id=uuid4(),
        username="ai-api-tester",
        username_normalized="ai-api-tester",
        display_name="AI API Tester",
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


def _client_for(user: User) -> tuple[TestClient, AsyncMock]:
    session = AsyncMock()

    async def override_user() -> User:
        return user

    async def override_db():
        yield session

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_db_session] = override_db
    return TestClient(app), session


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


def _task(*, task_type: str, round_id=ROUND_ID, status: str = AI_TASK_STATUS_PENDING):
    return SimpleNamespace(
        id=TASK_ID,
        task_type=task_type,
        status=status,
        business_id=round_id,
    )


def _question_summary(**overrides) -> QuestionVersionSummary:
    payload = dict(
        id=VERSION_ID,
        question_set_id=SET_ID,
        round_id=ROUND_ID,
        version_no=1,
        version_label="Q1",
        source_type=QUESTION_SOURCE_AI_GENERATED,
        job_version_id=uuid4(),
        resume_version_id=uuid4(),
        input_snapshot_hash="abc",
        question_count=1,
        is_current=True,
        created_at=NOW,
        created_by=uuid4(),
        ai_task_id=TASK_ID,
    )
    payload.update(overrides)
    return QuestionVersionSummary(**payload)


def _question_set(**overrides) -> QuestionSetSummary:
    payload = dict(
        id=SET_ID,
        round_id=ROUND_ID,
        status=QUESTION_SET_STATUS_READY,
        current_version_id=VERSION_ID,
        confirmed_by=uuid4(),
        confirmed_at=NOW,
        versions=[_question_summary()],
    )
    payload.update(overrides)
    return QuestionSetSummary(**payload)


def _question_detail(**overrides) -> QuestionVersionDetail:
    item = QuestionItemDetail(
        id=uuid4(),
        dimension_key="D001",
        question=SECRET_QUESTION,
        purpose="考察协作",
        evidence_source="JOB_REQUIREMENT",
        resume_evidence=None,
        follow_up_prompts=["请补充结果"],
        risk_flags=["细节不足"],
        display_order=1,
    )
    payload = dict(
        id=VERSION_ID,
        question_set_id=SET_ID,
        round_id=ROUND_ID,
        version_no=1,
        version_label="Q1",
        source_type=QUESTION_SOURCE_MANUAL_EDIT,
        job_version_id=uuid4(),
        resume_version_id=uuid4(),
        input_snapshot_hash="abc",
        question_count=1,
        is_current=True,
        created_at=NOW,
        created_by=uuid4(),
        ai_task_id=None,
        items=[item],
    )
    payload.update(overrides)
    return QuestionVersionDetail(**payload)


def _analysis_summary(*, stale: bool = False) -> AnalysisVersionSummary:
    return AnalysisVersionSummary(
        analysis_id=ANALYSIS_ID,
        version_id=VERSION_ID,
        version_no=1,
        version_label="A1",
        transcript_version_id=uuid4(),
        job_version_id=uuid4(),
        ai_task_id=TASK_ID,
        overall_score=Decimal("4.60"),
        dimension_count=1,
        evidence_count=1,
        created_by=uuid4(),
        created_at=NOW,
        is_current=True,
        is_stale=stale,
    )


def _analysis_set(*, stale: bool = False) -> AnalysisSetSummary:
    return AnalysisSetSummary(
        analysis_id=ANALYSIS_ID,
        round_id=ROUND_ID,
        current_version_id=VERSION_ID,
        versions=[_analysis_summary(stale=stale)],
    )


def _analysis_detail(*, stale: bool = False) -> AnalysisVersionDetail:
    return AnalysisVersionDetail(
        analysis_id=ANALYSIS_ID,
        version_id=VERSION_ID,
        version_no=1,
        version_label="A1",
        transcript_version_id=uuid4(),
        job_version_id=uuid4(),
        ai_task_id=TASK_ID,
        overall_score=Decimal("4.60"),
        overall_summary="整体稳定",
        dimension_count=1,
        evidence_count=1,
        created_by=uuid4(),
        created_at=NOW,
        is_current=True,
        is_stale=stale,
        dimensions=[
            AnalysisDimensionDetail(
                id=uuid4(),
                dimension_key="D001",
                dimension_name="协作",
                weight=Decimal("40.00"),
                score=4,
                analysis=SECRET_ANALYSIS,
                strengths=["目标对齐"],
                risks=["细节偏少"],
                insufficient_information=None,
                suggested_follow_ups=["请补充结果"],
                display_order=1,
                evidence=[
                    AnalysisEvidenceDetail(
                        id=uuid4(),
                        transcript_segment_id=uuid4(),
                        segment_no=1,
                        quote=SECRET_QUOTE,
                    )
                ],
            )
        ],
    )


def _edit_body() -> dict:
    return {
        "idempotency_key": "edit-1",
        "expected_current_version_id": str(VERSION_ID),
        "questions": [
            {
                "dimension_key": "D001",
                "question": "请描述一次协作。",
                "purpose": "考察协作",
                "evidence_source": "JOB_REQUIREMENT",
                "resume_evidence": None,
                "follow_up_prompts": ["结果是什么？"],
                "risk_flags": ["回避责任"],
                "display_order": 1,
            }
        ],
    }


def _assert_no_sensitive(payload) -> None:
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    for marker in FORBIDDEN_BODY_MARKERS:
        assert marker not in blob
    assert "enc:v1:" not in blob


def test_api_module_has_no_hire_offer_or_dify_raw(lifespan_patches) -> None:
    from app.api.v1.endpoints import interview_ai
    from app.schemas import interview_ai_api

    blob = inspect.getsource(interview_ai) + inspect.getsource(interview_ai_api)
    lowered = blob.lower()
    for needle in (
        "hire",
        "reject",
        "offer",
        "decision",
        "system-send",
        "smtp",
        "raw_request",
        "raw_response",
        "sensitive_request",
        "sensitive_response",
    ):
        assert needle not in lowered


@pytest.mark.parametrize("code", ("recruitment.manage",))
def test_question_generate_202_queued_after_commit(lifespan_patches, code) -> None:
    client, session = _client_for(_user(code))
    calls: list[str] = []
    task = _task(task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE)

    async def fake_request(**kwargs):
        calls.append("request")
        return task

    async def fake_commit():
        calls.append("commit")

    async def fake_dispatch(**kwargs):
        assert calls[-1] == "commit"
        calls.append("dispatch")

    session.commit.side_effect = fake_commit
    with (
        patch(f"{ENDPOINT}.request_question_generation", side_effect=fake_request),
        patch(
            f"{ENDPOINT}.dispatch_persisted_question_generation_task",
            side_effect=fake_dispatch,
        ),
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/generate",
            json={"idempotency_key": "gen-1"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == str(TASK_ID)
    assert body["task_type"] == TASK_TYPE_INTERVIEW_QUESTION_GENERATE
    assert body["status"] == "pending"
    assert body["round_id"] == str(ROUND_ID)
    assert body["dispatch_status"] == "queued"
    _assert_no_sensitive(body)
    assert calls == ["request", "commit", "dispatch"]


def test_question_generate_dispatch_failure_is_pending_dispatch(
    lifespan_patches,
) -> None:
    client, session = _client_for(_user("recruitment.manage"))
    task = _task(task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE)

    async def boom(**kwargs):
        raise RuntimeError(f"Dify exploded {SECRET_QUESTION} {CIPHER}")

    with (
        patch(
            f"{ENDPOINT}.request_question_generation",
            new_callable=AsyncMock,
            return_value=task,
        ),
        patch(
            f"{ENDPOINT}.dispatch_persisted_question_generation_task",
            side_effect=boom,
        ),
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/generate",
            json={"idempotency_key": "gen-fail"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["dispatch_status"] == "pending_dispatch"
    assert body["task_id"] == str(TASK_ID)
    session.commit.assert_awaited()
    _assert_no_sensitive(body)
    assert SECRET_QUESTION not in (response.text or "")
    assert "Dify exploded" not in (response.text or "")


def test_unassigned_execute_generate_is_403_not_404(lifespan_patches) -> None:
    client, session = _client_for(_user("interview.execute"))
    with patch(
        f"{ENDPOINT}.request_question_generation",
        new_callable=AsyncMock,
        side_effect=InterviewNotFoundError("interview round not found"),
    ) as mocked:
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/generate",
            json={"idempotency_key": "gen-404"},
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "forbidden"
    mocked.assert_not_awaited()
    mocked.assert_not_called()
    session.commit.assert_not_called()
    _assert_no_sensitive(response.json())


def test_cross_round_question_version_is_404(lifespan_patches) -> None:
    client, _session = _client_for(_user("recruitment.manage"))
    other = uuid4()
    with patch(
        f"{ENDPOINT}.get_question_version_detail",
        side_effect=InterviewNotFoundError("question version not found"),
    ):
        response = client.get(
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/versions/{other}"
        )
    assert response.status_code == 404


@pytest.mark.parametrize("code", ("recruitment.manage", "interview.execute"))
def test_question_list_has_no_body(lifespan_patches, code) -> None:
    client, _session = _client_for(_user(code))
    with patch(
        f"{ENDPOINT}.list_question_versions",
        new_callable=AsyncMock,
        return_value=_question_set(),
    ):
        response = client.get(f"/api/v1/interview-rounds/{ROUND_ID}/question-set")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == QUESTION_SET_STATUS_READY
    assert body["versions"][0]["question_count"] == 1
    assert "items" not in body
    assert "question" not in body["versions"][0]
    _assert_no_sensitive(body)


def test_question_detail_decrypts_without_ciphertext_and_no_store(
    lifespan_patches,
) -> None:
    client, _session = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.get_question_version_detail",
        new_callable=AsyncMock,
        return_value=_question_detail(),
    ):
        response = client.get(
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/versions/{VERSION_ID}"
        )
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    body = response.json()
    assert body["items"][0]["question"] == SECRET_QUESTION
    blob = json.dumps(body, ensure_ascii=False)
    assert CIPHER not in blob
    assert "_encrypted" not in blob


def test_edit_stale_current_is_409(lifespan_patches) -> None:
    client, _session = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.create_manual_question_version",
        side_effect=InterviewOptimisticLockError("面试题纲已被其他人员更新，请刷新后重新编辑"),
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/versions",
            json=_edit_body(),
        )
    assert response.status_code == 409


def test_edit_same_key_replays_without_new_version(lifespan_patches) -> None:
    client, session = _client_for(_user("recruitment.manage"))
    detail = _question_detail()
    with patch(
        f"{ENDPOINT}.create_manual_question_version",
        new_callable=AsyncMock,
        return_value=detail,
    ) as mocked:
        first = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/versions",
            json=_edit_body(),
        )
        second = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/versions",
            json=_edit_body(),
        )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert mocked.await_count == 2
    assert session.commit.await_count == 2


def test_confirm_ready_metadata(lifespan_patches) -> None:
    client, session = _client_for(_user("recruitment.manage"))
    confirmed_by = uuid4()
    summary = _question_set(confirmed_by=confirmed_by, confirmed_at=NOW)
    with patch(
        f"{ENDPOINT}.confirm_question_set",
        new_callable=AsyncMock,
        return_value=summary,
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/confirm",
            json={
                "idempotency_key": "confirm-1",
                "expected_current_version_id": str(VERSION_ID),
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == QUESTION_SET_STATUS_READY
    assert body["confirmed_by"] == str(confirmed_by)
    assert body["confirmed_at"] is not None
    session.commit.assert_awaited()
    _assert_no_sensitive(body)


def test_confirm_stale_current_is_409(lifespan_patches) -> None:
    client, _session = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.confirm_question_set",
        side_effect=InterviewOptimisticLockError("stale"),
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/confirm",
            json={
                "idempotency_key": "confirm-stale",
                "expected_current_version_id": str(uuid4()),
            },
        )
    assert response.status_code == 409


@pytest.mark.parametrize("code", ("recruitment.manage",))
def test_analysis_generate_202_commit_before_dispatch(lifespan_patches, code) -> None:
    client, session = _client_for(_user(code))
    calls: list[str] = []
    task = _task(task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE)

    async def fake_request(**kwargs):
        calls.append("request")
        return task

    async def fake_commit():
        calls.append("commit")

    async def fake_dispatch(**kwargs):
        assert "commit" in calls
        calls.append("dispatch")

    session.commit.side_effect = fake_commit
    with (
        patch(f"{ENDPOINT}.request_analysis_generation", side_effect=fake_request),
        patch(
            f"{ENDPOINT}.dispatch_persisted_analysis_generation_task",
            side_effect=fake_dispatch,
        ),
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/analysis/generate",
            json={"idempotency_key": "an-1"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["task_type"] == TASK_TYPE_INTERVIEW_ROUND_ANALYZE
    assert body["dispatch_status"] == "queued"
    _assert_no_sensitive(body)
    assert calls == ["request", "commit", "dispatch"]


@pytest.mark.parametrize(
    "message",
    (
        "仅已完成的面试轮次可以生成单轮分析",
        "该轮无转写，不能生成单轮分析",
        "请先确认转写版本后再生成单轮分析",
        "岗位评分维度缺少完整锚点",
    ),
)
def test_analysis_generate_validation_is_400(lifespan_patches, message) -> None:
    client, session = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.request_analysis_generation",
        side_effect=InterviewValidationError(message),
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/analysis/generate",
            json={"idempotency_key": "an-bad"},
        )
    assert response.status_code == 400
    session.commit.assert_not_called()
    _assert_no_sensitive(response.json())


def test_analysis_generate_idempotency_conflict_is_409(lifespan_patches) -> None:
    client, _session = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.request_analysis_generation",
        side_effect=InterviewIdempotencyConflictError("idempotency conflict"),
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/analysis/generate",
            json={"idempotency_key": "dup"},
        )
    assert response.status_code == 409


def test_analysis_list_has_no_quote_and_stale_flag(lifespan_patches) -> None:
    client, _session = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.list_analysis_versions",
        new_callable=AsyncMock,
        return_value=_analysis_set(stale=True),
    ):
        response = client.get(f"/api/v1/interview-rounds/{ROUND_ID}/analysis")
    assert response.status_code == 200
    body = response.json()
    item = body["versions"][0]
    assert item["is_stale"] is True
    assert item["is_current"] is True
    assert "overall_summary" not in item
    assert "quote" not in json.dumps(body)
    assert "analysis" not in item
    _assert_no_sensitive(body)


def test_analysis_detail_authorized_body_without_ciphertext(
    lifespan_patches,
) -> None:
    client, _session = _client_for(_user("interview.execute"))
    with patch(
        f"{ENDPOINT}.get_analysis_version_detail",
        new_callable=AsyncMock,
        return_value=_analysis_detail(stale=True),
    ):
        response = client.get(
            f"/api/v1/interview-rounds/{ROUND_ID}/analysis/versions/{VERSION_ID}"
        )
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    body = response.json()
    assert body["is_stale"] is True
    assert body["dimensions"][0]["analysis"] == SECRET_ANALYSIS
    assert body["dimensions"][0]["evidence"][0]["quote"] == SECRET_QUOTE
    blob = json.dumps(body, ensure_ascii=False)
    assert CIPHER not in blob
    assert "_encrypted" not in blob


def test_analysis_cross_round_version_is_404(lifespan_patches) -> None:
    client, _session = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.get_analysis_version_detail",
        side_effect=InterviewNotFoundError("analysis version not found"),
    ):
        response = client.get(
            f"/api/v1/interview-rounds/{ROUND_ID}/analysis/versions/{uuid4()}"
        )
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "body", "service_name", "dispatch_name"),
    (
        (
            "post",
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/generate",
            {"idempotency_key": "exec-gen"},
            "request_question_generation",
            "dispatch_persisted_question_generation_task",
        ),
        (
            "post",
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/versions",
            _edit_body(),
            "create_manual_question_version",
            None,
        ),
        (
            "post",
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/confirm",
            {
                "idempotency_key": "exec-confirm",
                "expected_current_version_id": str(VERSION_ID),
            },
            "confirm_question_set",
            None,
        ),
        (
            "post",
            f"/api/v1/interview-rounds/{ROUND_ID}/analysis/generate",
            {"idempotency_key": "exec-an"},
            "request_analysis_generation",
            "dispatch_persisted_analysis_generation_task",
        ),
    ),
)
def test_assigned_execute_cannot_write_stage8_ai(
    lifespan_patches,
    method: str,
    path: str,
    body: dict,
    service_name: str,
    dispatch_name: str | None,
) -> None:
    client, session = _client_for(_user("interview.execute"))
    service = AsyncMock(
        return_value=_task(task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE)
    )
    dispatch = AsyncMock() if dispatch_name is not None else None
    with ExitStack() as stack:
        stack.enter_context(patch(f"{ENDPOINT}.{service_name}", new=service))
        if dispatch_name is not None:
            stack.enter_context(patch(f"{ENDPOINT}.{dispatch_name}", new=dispatch))
        response = getattr(client, method)(path, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "forbidden"
    service.assert_not_awaited()
    service.assert_not_called()
    session.commit.assert_not_called()
    if dispatch is not None:
        dispatch.assert_not_awaited()
        dispatch.assert_not_called()
    _assert_no_sensitive(response.json())


def test_assigned_execute_can_read_question_and_analysis(lifespan_patches) -> None:
    client, _session = _client_for(_user("interview.execute"))
    with (
        patch(
            f"{ENDPOINT}.list_question_versions",
            new_callable=AsyncMock,
            return_value=_question_set(),
        ),
        patch(
            f"{ENDPOINT}.get_question_version_detail",
            new_callable=AsyncMock,
            return_value=_question_detail(),
        ),
        patch(
            f"{ENDPOINT}.list_analysis_versions",
            new_callable=AsyncMock,
            return_value=_analysis_set(stale=True),
        ),
        patch(
            f"{ENDPOINT}.get_analysis_version_detail",
            new_callable=AsyncMock,
            return_value=_analysis_detail(stale=True),
        ),
    ):
        list_q = client.get(f"/api/v1/interview-rounds/{ROUND_ID}/question-set")
        detail_q = client.get(
            f"/api/v1/interview-rounds/{ROUND_ID}/question-set/versions/{VERSION_ID}"
        )
        list_a = client.get(f"/api/v1/interview-rounds/{ROUND_ID}/analysis")
        detail_a = client.get(
            f"/api/v1/interview-rounds/{ROUND_ID}/analysis/versions/{VERSION_ID}"
        )
    assert list_q.status_code == 200
    assert detail_q.status_code == 200
    assert list_a.status_code == 200
    assert detail_a.status_code == 200
    assert list_q.json()["versions"][0]["question_count"] == 1
    assert "items" not in list_q.json()
    assert detail_q.json()["items"][0]["question"] == SECRET_QUESTION
    assert list_a.json()["versions"][0]["is_stale"] is True
    assert "overall_summary" not in list_a.json()["versions"][0]
    assert detail_a.json()["dimensions"][0]["analysis"] == SECRET_ANALYSIS


@pytest.mark.parametrize(
    "path",
    (
        f"/api/v1/interview-rounds/{ROUND_ID}/question-set",
        f"/api/v1/interview-rounds/{ROUND_ID}/question-set/versions/{VERSION_ID}",
        f"/api/v1/interview-rounds/{ROUND_ID}/analysis",
        f"/api/v1/interview-rounds/{ROUND_ID}/analysis/versions/{VERSION_ID}",
    ),
)
def test_unassigned_execute_get_is_object_404(lifespan_patches, path: str) -> None:
    client, _session = _client_for(_user("interview.execute"))
    with (
        patch(
            f"{ENDPOINT}.list_question_versions",
            side_effect=InterviewNotFoundError("interview round not found"),
        ),
        patch(
            f"{ENDPOINT}.get_question_version_detail",
            side_effect=InterviewNotFoundError("interview round not found"),
        ),
        patch(
            f"{ENDPOINT}.list_analysis_versions",
            side_effect=InterviewNotFoundError("interview round not found"),
        ),
        patch(
            f"{ENDPOINT}.get_analysis_version_detail",
            side_effect=InterviewNotFoundError("interview round not found"),
        ),
    ):
        response = client.get(path)
    assert response.status_code == 404
    assert response.json()["detail"] == "not found"
    _assert_no_sensitive(response.json())
