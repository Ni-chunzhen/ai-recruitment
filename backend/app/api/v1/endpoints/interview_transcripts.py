"""Interview transcript preview, import and proofreading API endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_db_session,
    require_any_permission,
    require_permission,
)
from app.models import User
from app.schemas.interview_transcript import (
    CompleteWithoutTranscriptOut,
    CompleteWithoutTranscriptRequest,
    ConfirmRequest,
    DraftCreateRequest,
    DraftSaveRequest,
    DraftSaveResponse,
    TranscriptImportRequest,
    TranscriptListOut,
    TranscriptPreviewOut,
    TranscriptReasonCodeListResponse,
    TranscriptVersionDetailOut,
)
from app.services.audit import RequestContext
from app.services.interview_state import InterviewStateError
from app.services.interview_transcripts import (
    complete_without_transcript,
    confirm_transcript_draft,
    create_transcript_draft,
    get_transcript_version,
    import_transcript,
    list_transcript_reason_codes,
    list_transcript_versions,
    preview_transcript,
    save_transcript_draft,
)
from app.services.interviews import (
    InterviewConflictError,
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
)
from app.services.transcript_parser import MAX_FILE_SIZE, TranscriptParseError

router = APIRouter(tags=["interview-transcripts"])


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", "unknown"),
        ip_address=request.client.host if request.client else None,
    )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InterviewNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if isinstance(exc, InterviewForbiddenError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    if isinstance(
        exc,
        (
            InterviewOptimisticLockError,
            InterviewIdempotencyConflictError,
            InterviewConflictError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(
        exc,
        (InterviewValidationError, InterviewStateError, TranscriptParseError),
    ):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _read_upload_capped(upload: UploadFile, *, limit: int = MAX_FILE_SIZE) -> bytes:
    """Read at most ``limit`` bytes; reject overflow before decode."""
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise InterviewValidationError("file exceeds 2 MiB limit")
    return data


@router.post(
    "/interview-rounds/{round_id}/transcripts/preview",
    response_model=TranscriptPreviewOut,
)
async def preview_transcript_endpoint(
    round_id: UUID,
    request: Request,
    response: Response,
    text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> TranscriptPreviewOut:
    response.headers["Cache-Control"] = "no-store"
    has_text = text is not None and text != ""
    has_file = file is not None and bool(file.filename)
    try:
        if has_text and has_file:
            raise InterviewValidationError(
                "provide either text or file, not both"
            )
        if not has_text and not has_file:
            raise InterviewValidationError("text or file is required")

        if has_file:
            assert file is not None
            data = await _read_upload_capped(file)
            filename = file.filename
        else:
            assert text is not None
            data = text.encode("utf-8")
            if len(data) > MAX_FILE_SIZE:
                raise InterviewValidationError("file exceeds 2 MiB limit")
            filename = None

        return await preview_transcript(
            session,
            round_id=round_id,
            actor=actor,
            request_context=_request_context(request),
            data=data,
            filename=filename,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/interview-rounds/{round_id}/transcripts",
    response_model=TranscriptVersionDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_transcript_endpoint(
    round_id: UUID,
    payload: TranscriptImportRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> TranscriptVersionDetailOut:
    try:
        return await import_transcript(
            session,
            round_id=round_id,
            actor=actor,
            request_context=_request_context(request),
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.get(
    "/interview-rounds/{round_id}/transcripts",
    response_model=TranscriptListOut,
)
async def list_transcripts_endpoint(
    round_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(
        require_any_permission("recruitment.manage", "interview.execute")
    ),
) -> TranscriptListOut:
    try:
        return await list_transcript_versions(
            session, round_id=round_id, actor=actor
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.get(
    "/transcript-versions/{version_id}",
    response_model=TranscriptVersionDetailOut,
)
async def get_transcript_version_endpoint(
    version_id: UUID,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(
        require_any_permission("recruitment.manage", "interview.execute")
    ),
) -> TranscriptVersionDetailOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await get_transcript_version(
            session,
            version_id=version_id,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/interview-transcripts/{transcript_id}/draft",
    response_model=TranscriptVersionDetailOut,
)
async def create_transcript_draft_endpoint(
    transcript_id: UUID,
    payload: DraftCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> TranscriptVersionDetailOut:
    try:
        return await create_transcript_draft(
            session,
            transcript_id=transcript_id,
            actor=actor,
            request_context=_request_context(request),
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.put(
    "/transcript-versions/{draft_id}/draft",
    response_model=DraftSaveResponse,
)
async def save_transcript_draft_endpoint(
    draft_id: UUID,
    payload: DraftSaveRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> DraftSaveResponse:
    try:
        return await save_transcript_draft(
            session,
            draft_id=draft_id,
            actor=actor,
            request_context=_request_context(request),
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/transcript-versions/{draft_id}/confirm",
    response_model=TranscriptVersionDetailOut,
)
async def confirm_transcript_draft_endpoint(
    draft_id: UUID,
    payload: ConfirmRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> TranscriptVersionDetailOut:
    try:
        return await confirm_transcript_draft(
            session,
            draft_id=draft_id,
            actor=actor,
            request_context=_request_context(request),
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/interview-rounds/{round_id}/complete-without-transcript",
    response_model=CompleteWithoutTranscriptOut,
)
async def complete_without_transcript_endpoint(
    round_id: UUID,
    payload: CompleteWithoutTranscriptRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> CompleteWithoutTranscriptOut:
    try:
        return await complete_without_transcript(
            session,
            round_id=round_id,
            actor=actor,
            request_context=_request_context(request),
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.get(
    "/interview-transcript-reason-codes",
    response_model=TranscriptReasonCodeListResponse,
)
async def get_transcript_reason_codes(
    _: User = Depends(
        require_any_permission("recruitment.manage", "interview.execute")
    ),
) -> TranscriptReasonCodeListResponse:
    return list_transcript_reason_codes()
