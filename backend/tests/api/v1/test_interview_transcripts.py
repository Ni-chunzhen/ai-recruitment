"""API acceptance tests for interview transcript workflow endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, get_db_session
from app.api.v1.endpoints.interview_transcripts import _read_upload_capped
from app.main import app
from app.models import Permission, Role, User
from app.schemas.interview_transcript import (
    ChangeCountsOut,
    CompleteWithoutTranscriptOut,
    DraftSaveResponse,
    TranscriptListOut,
    TranscriptPreviewOut,
    TranscriptReasonCodeListResponse,
    TranscriptVersionDetailOut,
)
from app.services.interviews import (
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
)
from app.services.transcript_parser import MAX_FILE_SIZE, TranscriptParseError

ROUND_ID = uuid4()
TRANSCRIPT_ID = uuid4()
VERSION_ID = uuid4()
DRAFT_ID = uuid4()
NOW = datetime.now(UTC)


def _user(*permission_codes: str) -> User:
    user = User(
        id=uuid4(),
        username="transcript-tester",
        username_normalized="transcript-tester",
        display_name="Transcript Tester",
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


def _preview_out(**overrides) -> TranscriptPreviewOut:
    payload = {
        "encoding": "utf-8",
        "sha256": "a" * 64,
        "char_count": 20,
        "segment_count": 1,
        "matched_rules": ["interviewer"],
        "source_method": "PASTE",
        "filename": None,
        "size": 20,
        "mime": None,
        "segments": [
            {
                "segment_no": 1,
                "speaker_key": "interviewer",
                "speaker_name": "面试官",
                "speaker_role": "INTERVIEWER",
                "start_time_ms": None,
                "end_time_ms": None,
                "text": "你好",
                "matched_rule": "interviewer",
            }
        ],
    }
    payload.update(overrides)
    return TranscriptPreviewOut.model_validate(payload)


def _version_detail(**overrides) -> TranscriptVersionDetailOut:
    payload = {
        "id": str(VERSION_ID),
        "transcript_id": str(TRANSCRIPT_ID),
        "interview_round_id": str(ROUND_ID),
        "version_type": "ORIGINAL",
        "version_no": 1,
        "version_label": "T1",
        "status": "IMMUTABLE",
        "source_method": "PASTE",
        "source_filename": None,
        "source_size": 20,
        "source_mime": None,
        "source_encoding": "utf-8",
        "source_sha256": "a" * 64,
        "based_on_version_id": None,
        "confirmed_by": None,
        "confirmed_at": None,
        "version": 1,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "segments": [
            {
                "id": str(uuid4()),
                "segment_no": 1,
                "speaker_key": "interviewer",
                "speaker_name": "面试官",
                "speaker_role": "INTERVIEWER",
                "start_time_ms": None,
                "end_time_ms": None,
                "text": "你好",
                "source_type": "ORIGINAL",
                "source_segment_refs": [],
                "is_included_in_analysis": True,
                "is_unclear": False,
            }
        ],
        "raw_text": "面试官：你好",
    }
    payload.update(overrides)
    return TranscriptVersionDetailOut.model_validate(payload)


def _list_out() -> TranscriptListOut:
    return TranscriptListOut.model_validate(
        {
            "transcript": {
                "id": str(TRANSCRIPT_ID),
                "interview_round_id": str(ROUND_ID),
                "original_version_id": str(VERSION_ID),
                "current_draft_version_id": None,
                "current_confirmed_version_id": None,
                "version": 1,
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            },
            "versions": [
                {
                    "id": str(VERSION_ID),
                    "transcript_id": str(TRANSCRIPT_ID),
                    "version_type": "ORIGINAL",
                    "version_no": 1,
                    "version_label": "T1",
                    "status": "IMMUTABLE",
                    "source_method": "PASTE",
                    "source_filename": None,
                    "source_size": 20,
                    "source_mime": None,
                    "source_encoding": "utf-8",
                    "based_on_version_id": None,
                    "segment_count": 1,
                    "confirmed_by": None,
                    "confirmed_at": None,
                    "version": 1,
                    "created_at": NOW.isoformat(),
                    "updated_at": NOW.isoformat(),
                }
            ],
        }
    )


def _import_body(**overrides) -> dict:
    payload = {
        "idempotency_key": "import-1",
        "source_method": "PASTE",
        "raw_text": "面试官：你好",
        "filename": None,
        "source_sha256": "a" * 64,
        "segments": [
            {
                "speaker_key": "interviewer",
                "speaker_name": "面试官",
                "speaker_role": "INTERVIEWER",
                "text": "你好",
                "start_time_ms": None,
                "end_time_ms": None,
                "is_unclear": False,
                "is_included_in_analysis": True,
                "source_segment_refs": [1],
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_preview_paste_ok(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.preview_transcript",
        new_callable=AsyncMock,
        return_value=_preview_out(),
    ) as mocked:
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/transcripts/preview",
            data={"text": "面试官：你好"},
        )
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    assert response.json()["segment_count"] == 1
    mocked.assert_awaited_once()
    kwargs = mocked.await_args.kwargs
    assert kwargs["data"] == "面试官：你好".encode("utf-8")
    assert kwargs["filename"] is None


def test_preview_file_ok(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    content = b"\xe9\x9d\xa2\xe8\xaf\x95\xe5\xae\x98\xef\xbc\x9a\xe4\xbd\xa0\xe5\xa5\xbd"
    with patch(
        "app.api.v1.endpoints.interview_transcripts.preview_transcript",
        new_callable=AsyncMock,
        return_value=_preview_out(source_method="TXT", filename="notes.txt"),
    ) as mocked:
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/transcripts/preview",
            files={"file": ("notes.txt", content, "text/plain")},
        )
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    kwargs = mocked.await_args.kwargs
    assert kwargs["data"] == content
    assert kwargs["filename"] == "notes.txt"


def test_preview_rejects_both_text_and_file(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    response = client.post(
        f"/api/v1/interview-rounds/{ROUND_ID}/transcripts/preview",
        data={"text": "面试官：你好"},
        files={"file": ("notes.txt", b"x", "text/plain")},
    )
    assert response.status_code == 400


def test_preview_rejects_oversized_file(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    oversized = b"a" * (2 * 1024 * 1024 + 1)
    with patch(
        "app.api.v1.endpoints.interview_transcripts.preview_transcript",
        new_callable=AsyncMock,
    ) as mocked:
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/transcripts/preview",
            files={"file": ("big.txt", oversized, "text/plain")},
        )
    assert response.status_code == 400
    mocked.assert_not_awaited()
    detail = str(response.json().get("detail", ""))
    assert "2 MiB" in detail or "exceeds" in detail.lower()
    assert "a" * 200 not in detail


@pytest.mark.asyncio
async def test_read_upload_capped_hard_limit() -> None:
    limit = 16
    upload = MagicMock()
    upload.read = AsyncMock(return_value=b"x" * (limit + 1))
    with pytest.raises(InterviewValidationError, match="2 MiB"):
        await _read_upload_capped(upload, limit=limit)
    upload.read.assert_awaited_once_with(limit + 1)


@pytest.mark.asyncio
async def test_read_upload_capped_accepts_exact_limit() -> None:
    limit = 16
    upload = MagicMock()
    upload.read = AsyncMock(return_value=b"y" * limit)
    data = await _read_upload_capped(upload, limit=limit)
    assert data == b"y" * limit
    upload.read.assert_awaited_once_with(limit + 1)


def test_oversized_preview_error_excludes_multipart_body(lifespan_patches) -> None:
    """API-layer safety: oversized rejection must not leak upload body in detail."""
    client = _client_for(_user("recruitment.manage"))
    marker = b"SENSITIVE_MULTIPART_BODY_MARKER_SHOULD_NOT_LEAK"
    oversized = marker + (b"z" * (MAX_FILE_SIZE + 1 - len(marker)))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.preview_transcript",
        new_callable=AsyncMock,
    ) as mocked:
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/transcripts/preview",
            files={"file": ("secret.txt", oversized, "text/plain")},
        )
    assert response.status_code == 400
    mocked.assert_not_awaited()
    payload = response.text
    assert "SENSITIVE_MULTIPART_BODY_MARKER_SHOULD_NOT_LEAK" not in payload
    assert "file exceeds" in payload.lower() or "2 MiB" in payload


def test_preview_requires_manage(lifespan_patches) -> None:
    client = _client_for(_user("interview.execute"))
    response = client.post(
        f"/api/v1/interview-rounds/{ROUND_ID}/transcripts/preview",
        data={"text": "面试官：你好"},
    )
    assert response.status_code == 403


def test_preview_maps_not_found(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.preview_transcript",
        new_callable=AsyncMock,
        side_effect=InterviewNotFoundError("interview round not found"),
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/transcripts/preview",
            data={"text": "面试官：你好"},
        )
    assert response.status_code == 404


def test_preview_maps_parse_error(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.preview_transcript",
        new_callable=AsyncMock,
        side_effect=TranscriptParseError("unsupported encoding"),
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/transcripts/preview",
            data={"text": "面试官：你好"},
        )
    assert response.status_code == 400


def test_import_ok(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.import_transcript",
        new_callable=AsyncMock,
        return_value=_version_detail(),
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/transcripts",
            json=_import_body(),
        )
    assert response.status_code == 201
    body = response.json()
    assert body["version_label"] == "T1"
    dumped = str(body)
    assert "encrypted" not in dumped
    assert "ciphertext" not in dumped


def test_import_idempotency_conflict_409(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.import_transcript",
        new_callable=AsyncMock,
        side_effect=InterviewIdempotencyConflictError("idempotency conflict"),
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/transcripts",
            json=_import_body(),
        )
    assert response.status_code == 409


def test_list_summaries_no_ciphertext(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.list_transcript_versions",
        new_callable=AsyncMock,
        return_value=_list_out(),
    ):
        response = client.get(f"/api/v1/interview-rounds/{ROUND_ID}/transcripts")
    assert response.status_code == 200
    body = response.json()
    assert "raw_text" not in body
    assert "segments" not in str(body.get("versions", []))
    dumped = str(body)
    assert "encrypted" not in dumped
    assert body["versions"][0]["version_label"] == "T1"


def test_list_ok_for_execute(lifespan_patches) -> None:
    client = _client_for(_user("interview.execute"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.list_transcript_versions",
        new_callable=AsyncMock,
        return_value=_list_out(),
    ):
        response = client.get(f"/api/v1/interview-rounds/{ROUND_ID}/transcripts")
    assert response.status_code == 200


def test_list_requires_manage_or_execute(lifespan_patches) -> None:
    client = _client_for(_user("profile.read"))
    response = client.get(f"/api/v1/interview-rounds/{ROUND_ID}/transcripts")
    assert response.status_code == 403


def test_detail_no_store_and_no_ciphertext(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.get_transcript_version",
        new_callable=AsyncMock,
        return_value=_version_detail(),
    ):
        response = client.get(f"/api/v1/transcript-versions/{VERSION_ID}")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    body = response.json()
    assert body["raw_text"] == "面试官：你好"
    dumped = str(body)
    assert "encrypted" not in dumped
    assert "ciphertext" not in dumped


def test_detail_forbidden_maps_403(lifespan_patches) -> None:
    client = _client_for(_user("interview.execute"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.get_transcript_version",
        new_callable=AsyncMock,
        side_effect=InterviewForbiddenError("forbidden"),
    ):
        response = client.get(f"/api/v1/transcript-versions/{VERSION_ID}")
    assert response.status_code == 403


def test_detail_not_found_maps_404(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.get_transcript_version",
        new_callable=AsyncMock,
        side_effect=InterviewNotFoundError("transcript version not found"),
    ):
        response = client.get(f"/api/v1/transcript-versions/{VERSION_ID}")
    assert response.status_code == 404


def test_create_draft_ok(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    draft = _version_detail(
        id=str(DRAFT_ID),
        version_type="DRAFT",
        version_no=1,
        version_label="D1",
        status="EDITING",
    )
    with patch(
        "app.api.v1.endpoints.interview_transcripts.create_transcript_draft",
        new_callable=AsyncMock,
        return_value=draft,
    ):
        response = client.post(
            f"/api/v1/interview-transcripts/{TRANSCRIPT_ID}/draft",
            json={"idempotency_key": "draft-1"},
        )
    assert response.status_code == 200
    assert response.json()["version_label"] == "D1"


def test_save_draft_ok(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    draft = _version_detail(
        id=str(DRAFT_ID),
        version_type="DRAFT",
        version_no=1,
        version_label="D1",
        status="EDITING",
        version=2,
    )
    result = DraftSaveResponse(
        version=draft,
        change_counts=ChangeCountsOut(text_corrections=1),
    )
    with patch(
        "app.api.v1.endpoints.interview_transcripts.save_transcript_draft",
        new_callable=AsyncMock,
        return_value=result,
    ):
        response = client.put(
            f"/api/v1/transcript-versions/{DRAFT_ID}/draft",
            json={
                "draft_version_id": str(DRAFT_ID),
                "version": 1,
                "idempotency_key": "save-1",
                "segments": [
                    {
                        "speaker_key": "interviewer",
                        "speaker_name": "面试官",
                        "speaker_role": "INTERVIEWER",
                        "text": "你好啊",
                        "source_segment_refs": [1],
                    }
                ],
            },
        )
    assert response.status_code == 200
    assert response.json()["change_counts"]["text_corrections"] == 1


def test_save_draft_optimistic_lock_409(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.save_transcript_draft",
        new_callable=AsyncMock,
        side_effect=InterviewOptimisticLockError(
            "转写草稿已被其他人员更新，请刷新后重新检查修改"
        ),
    ):
        response = client.put(
            f"/api/v1/transcript-versions/{DRAFT_ID}/draft",
            json={
                "draft_version_id": str(DRAFT_ID),
                "version": 1,
                "idempotency_key": "save-2",
                "segments": [
                    {
                        "speaker_key": "interviewer",
                        "speaker_name": "面试官",
                        "speaker_role": "INTERVIEWER",
                        "text": "你好",
                        "source_segment_refs": [1],
                    }
                ],
            },
        )
    assert response.status_code == 409


def test_confirm_ok(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    confirmed = _version_detail(
        version_type="CONFIRMED",
        version_no=1,
        version_label="C1",
        status="IMMUTABLE",
    )
    with patch(
        "app.api.v1.endpoints.interview_transcripts.confirm_transcript_draft",
        new_callable=AsyncMock,
        return_value=confirmed,
    ):
        response = client.post(
            f"/api/v1/transcript-versions/{DRAFT_ID}/confirm",
            json={"idempotency_key": "confirm-1", "version": 1},
        )
    assert response.status_code == 200
    assert response.json()["version_label"] == "C1"


def test_confirm_validation_400(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        "app.api.v1.endpoints.interview_transcripts.confirm_transcript_draft",
        new_callable=AsyncMock,
        side_effect=InterviewValidationError("draft must contain at least one segment"),
    ):
        response = client.post(
            f"/api/v1/transcript-versions/{DRAFT_ID}/confirm",
            json={"idempotency_key": "confirm-2", "version": 1},
        )
    assert response.status_code == 400


def test_complete_without_transcript_ok(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    result = CompleteWithoutTranscriptOut.model_validate(
        {
            "round_id": str(ROUND_ID),
            "status": "COMPLETED",
            "version": 2,
            "transcript_completion_mode": "WITHOUT_TRANSCRIPT",
            "transcript_completion_reason_code": "CONTENT_UNUSABLE",
            "transcript_completion_reason_description": None,
            "transcript_completed_by": str(uuid4()),
            "transcript_completed_at": NOW.isoformat(),
        }
    )
    with patch(
        "app.api.v1.endpoints.interview_transcripts.complete_without_transcript",
        new_callable=AsyncMock,
        return_value=result,
    ):
        response = client.post(
            f"/api/v1/interview-rounds/{ROUND_ID}/complete-without-transcript",
            json={
                "reason_code": "CONTENT_UNUSABLE",
                "description": None,
                "version": 1,
                "idempotency_key": "without-1",
            },
        )
    assert response.status_code == 200
    assert response.json()["transcript_completion_mode"] == "WITHOUT_TRANSCRIPT"


def test_complete_without_requires_manage(lifespan_patches) -> None:
    client = _client_for(_user("interview.execute"))
    response = client.post(
        f"/api/v1/interview-rounds/{ROUND_ID}/complete-without-transcript",
        json={
            "reason_code": "CONTENT_UNUSABLE",
            "version": 1,
            "idempotency_key": "without-2",
        },
    )
    assert response.status_code == 403


def test_reason_codes_endpoint(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    catalog = TranscriptReasonCodeListResponse.model_validate(
        {
            "items": [
                {
                    "code": "CONTENT_UNUSABLE",
                    "label": "内容不可用",
                    "requires_description": False,
                },
                {
                    "code": "OTHER",
                    "label": "其他",
                    "requires_description": True,
                },
            ]
        }
    )
    with patch(
        "app.api.v1.endpoints.interview_transcripts.list_transcript_reason_codes",
        return_value=catalog,
    ):
        response = client.get("/api/v1/interview-transcript-reason-codes")
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()["items"]}
    assert "CONTENT_UNUSABLE" in codes
    assert "OTHER" in codes
