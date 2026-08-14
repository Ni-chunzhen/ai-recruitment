from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_permission
from app.models import User
from app.models.resume import list_screening_reason_catalog
from app.repositories.jobs import JobNotFoundError
from app.repositories.resumes import ResumeNotFoundError
from app.schemas.resume import (
    ApplicationOut,
    ConfirmResumeRequest,
    CreateApplicationRequest,
    CreateScoreTaskRequest,
    ResumeListResponse,
    ResumeUploadResponse,
    ResumeVersionOut,
    SaveDraftRequest,
    ScoreHistoryResponse,
    ScoreReportOut,
    ScoreTaskCreated,
    ScreeningDecisionOut,
    ScreeningDecisionRequest,
    ScreeningReasonCodeItem,
    ScreeningReasonCodeListResponse,
)
from app.services.audit import RequestContext, record_audit
from app.services.resumes import (
    ConflictError,
    ResumeStateError,
    ResumeValidationError,
    confirm_resume_version,
    create_application_for_resume,
    create_resume_score_task,
    create_screening_decision,
    get_application_detail,
    get_resume_version,
    get_score_history,
    get_score_history_item,
    get_score_report,
    list_resumes,
    list_workbench_pending,
    retry_resume_parse_manual_text,
    save_draft,
    upload_resumes,
)

router = APIRouter(tags=["resumes"])


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", "unknown"),
        ip_address=request.client.host if request.client else None,
    )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ResumeNotFoundError, JobNotFoundError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (ResumeValidationError, ResumeStateError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/resumes",
    response_model=ResumeUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_resumes_endpoint(
    request: Request,
    files: list[UploadFile] = File(...),
    job_id: UUID | None = Form(default=None),
    link_candidate_id: UUID | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> ResumeUploadResponse:
    prepared: list[tuple[str, str, bytes]] = []
    for upload in files:
        data = await upload.read()
        prepared.append(
            (
                upload.filename or "resume.bin",
                upload.content_type or "application/octet-stream",
                data,
            )
        )
    try:
        return await upload_resumes(
            session,
            files=prepared,
            actor=actor,
            request_context=_request_context(request),
            job_id=job_id,
            link_candidate_id=link_candidate_id,
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(
            exc,
            (
                ResumeValidationError,
                ResumeStateError,
                ResumeNotFoundError,
                JobNotFoundError,
            ),
        ):
            raise _map_error(exc) from exc
        raise


@router.get("/resumes", response_model=ResumeListResponse)
async def list_resumes_endpoint(
    keyword: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    linked: bool | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> ResumeListResponse:
    return await list_resumes(
        session,
        keyword=keyword,
        status=status_filter,
        linked=linked,
        offset=offset,
        limit=limit,
    )


@router.get("/workbench/pending-reviews", response_model=ResumeListResponse)
async def pending_reviews_endpoint(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> ResumeListResponse:
    return await list_workbench_pending(session)


@router.get("/resume-versions/{version_id}", response_model=ResumeVersionOut)
async def get_resume_version_endpoint(
    version_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> ResumeVersionOut:
    try:
        return await get_resume_version(session, version_id)
    except ResumeNotFoundError as exc:
        raise _map_error(exc) from exc


@router.put("/resume-versions/{version_id}/draft", response_model=ResumeVersionOut)
async def save_draft_endpoint(
    version_id: UUID,
    payload: SaveDraftRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> ResumeVersionOut:
    try:
        return await save_draft(
            session,
            version_id=version_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(
            exc,
            (ResumeNotFoundError, ResumeStateError, ResumeValidationError),
        ):
            raise _map_error(exc) from exc
        raise


@router.put(
    "/resume-versions/{version_id}/confirmed-content",
    response_model=ResumeVersionOut,
)
async def confirm_resume_endpoint(
    version_id: UUID,
    payload: ConfirmResumeRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> ResumeVersionOut:
    try:
        return await confirm_resume_version(
            session,
            version_id=version_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(
            exc,
            (ResumeNotFoundError, ResumeStateError, ResumeValidationError),
        ):
            raise _map_error(exc) from exc
        raise


@router.post(
    "/resume-versions/{version_id}/reparse",
    response_model=ResumeVersionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reparse_resume_endpoint(
    version_id: UUID,
    request: Request,
    text: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> ResumeVersionOut:
    try:
        return await retry_resume_parse_manual_text(
            session,
            version_id=version_id,
            text=text,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, (ResumeNotFoundError, ResumeValidationError)):
            raise _map_error(exc) from exc
        raise


@router.get(
    "/screening-reason-codes",
    response_model=ScreeningReasonCodeListResponse,
)
async def list_screening_reason_codes_endpoint(
    _: User = Depends(require_permission("recruitment.manage")),
) -> ScreeningReasonCodeListResponse:
    return ScreeningReasonCodeListResponse(
        items=[
            ScreeningReasonCodeItem.model_validate(item)
            for item in list_screening_reason_catalog()
        ]
    )


@router.post(
    "/applications",
    response_model=ApplicationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_application_endpoint(
    payload: CreateApplicationRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> ApplicationOut:
    try:
        return await create_application_for_resume(
            session,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(
            exc,
            (
                ResumeNotFoundError,
                JobNotFoundError,
                ResumeStateError,
                ResumeValidationError,
            ),
        ):
            raise _map_error(exc) from exc
        raise


@router.get("/applications/{application_id}", response_model=ApplicationOut)
async def get_application_endpoint(
    application_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> ApplicationOut:
    try:
        return await get_application_detail(session, application_id)
    except ResumeNotFoundError as exc:
        raise _map_error(exc) from exc


@router.post(
    "/applications/{application_id}/resume-score-tasks",
    response_model=ScoreTaskCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_score_task_endpoint(
    application_id: UUID,
    payload: CreateScoreTaskRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> ScoreTaskCreated:
    try:
        return await create_resume_score_task(
            session,
            application_id=application_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(
            exc,
            (
                ResumeNotFoundError,
                JobNotFoundError,
                ResumeStateError,
                ResumeValidationError,
                ConflictError,
            ),
        ):
            raise _map_error(exc) from exc
        raise


@router.get(
    "/applications/{application_id}/resume-score-report",
    response_model=ScoreReportOut,
)
async def get_score_report_endpoint(
    application_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> ScoreReportOut:
    try:
        report = await get_score_report(session, application_id)
        await record_audit(
            session,
            action="application.resume_score.view",
            result="success",
            resource_type="job_application",
            request_context=_request_context(request),
            actor_user_id=actor.id,
            resource_id=str(application_id),
            changes={"result_id": str(report.result_id), "current": True},
        )
        await session.commit()
        return report
    except ResumeNotFoundError as exc:
        raise _map_error(exc) from exc


@router.get(
    "/applications/{application_id}/resume-score-history",
    response_model=ScoreHistoryResponse,
)
async def get_score_history_endpoint(
    application_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> ScoreHistoryResponse:
    return await get_score_history(session, application_id)


@router.get(
    "/applications/{application_id}/resume-score-history/{result_id}",
    response_model=ScoreReportOut,
)
async def get_score_history_item_endpoint(
    application_id: UUID,
    result_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> ScoreReportOut:
    try:
        report = await get_score_history_item(
            session, application_id=application_id, result_id=result_id
        )
        await record_audit(
            session,
            action="application.resume_score.view",
            result="success",
            resource_type="job_application",
            request_context=_request_context(request),
            actor_user_id=actor.id,
            resource_id=str(application_id),
            changes={"result_id": str(report.result_id), "current": report.is_current},
        )
        await session.commit()
        return report
    except ResumeNotFoundError as exc:
        raise _map_error(exc) from exc


@router.post(
    "/applications/{application_id}/screening-decisions",
    response_model=ScreeningDecisionOut,
    status_code=status.HTTP_201_CREATED,
)
async def screening_decision_endpoint(
    application_id: UUID,
    payload: ScreeningDecisionRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> ScreeningDecisionOut:
    try:
        return await create_screening_decision(
            session,
            application_id=application_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(
            exc,
            (
                ResumeNotFoundError,
                ResumeStateError,
                ResumeValidationError,
                ConflictError,
            ),
        ):
            raise _map_error(exc) from exc
        raise
