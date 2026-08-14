from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_permission
from app.models import User
from app.repositories.jobs import JobNotFoundError
from app.schemas.job import (
    CreateJobRequest,
    JobDetail,
    JobListResponse,
    JobVersionListResponse,
    JobVersionOut,
    PublishJobRequest,
    ReasonRequest,
    SaveDraftRequest,
    VersionDiffResponse,
)
from app.services.audit import RequestContext
from app.services.candidates import CandidateStateError
from app.services.jobs import (
    JobStateError,
    JobValidationError,
    close_job,
    copy_job,
    copy_version_to_draft,
    create_job,
    diff_job_versions,
    get_job_detail,
    get_job_version_detail,
    list_job_details,
    list_job_versions,
    pause_job,
    publish_job,
    remove_job,
    resume_job,
    save_job_draft,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", "unknown"),
        ip_address=request.client.host if request.client else None,
    )


def _map_service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, JobNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if isinstance(exc, JobValidationError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_error", "errors": exc.errors},
        )
    if isinstance(exc, (JobStateError, CandidateStateError)):
        message = str(exc)
        if "in-flight" in message:
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            )
        code = (
            status.HTTP_403_FORBIDDEN
            if "only draft" in message or "cannot be deleted" in message
            else status.HTTP_400_BAD_REQUEST
        )
        return HTTPException(status_code=code, detail=message)
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    )


@router.get("", response_model=JobListResponse)
async def get_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    code: str | None = Query(default=None, max_length=32),
    name: str | None = Query(default=None, max_length=255),
    keyword: str | None = Query(default=None, max_length=255),
    department: str | None = Query(default=None, max_length=128),
    owner: str | None = Query(default=None, max_length=128),
    status_filter: str | None = Query(default=None, alias="status", max_length=16),
    updated_from: datetime | None = None,
    updated_to: datetime | None = None,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> JobListResponse:
    return await list_job_details(
        session,
        page=page,
        page_size=page_size,
        code=code,
        name=name,
        keyword=keyword,
        department=department,
        owner=owner,
        status=status_filter,
        updated_from=updated_from,
        updated_to=updated_to,
    )


@router.post("", response_model=JobDetail, status_code=status.HTTP_201_CREATED)
async def create_job_endpoint(
    payload: CreateJobRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobDetail:
    return await create_job(
        session,
        payload=payload,
        actor=actor,
        request_context=_request_context(request),
    )


@router.get("/{job_id}", response_model=JobDetail)
async def get_job_endpoint(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> JobDetail:
    try:
        return await get_job_detail(session, job_id)
    except JobNotFoundError as exc:
        raise _map_service_error(exc) from exc


@router.get("/{job_id}/versions", response_model=JobVersionListResponse)
async def list_versions_endpoint(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> JobVersionListResponse:
    try:
        return await list_job_versions(session, job_id=job_id)
    except JobNotFoundError as exc:
        raise _map_service_error(exc) from exc


@router.get("/{job_id}/versions/diff", response_model=VersionDiffResponse)
async def diff_versions_endpoint(
    job_id: UUID,
    from_version_id: UUID = Query(..., alias="from"),
    to_version_id: UUID = Query(..., alias="to"),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> VersionDiffResponse:
    try:
        return await diff_job_versions(
            session,
            job_id=job_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
        )
    except JobNotFoundError as exc:
        raise _map_service_error(exc) from exc


@router.get("/{job_id}/versions/{version_id}", response_model=JobVersionOut)
async def get_version_endpoint(
    job_id: UUID,
    version_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> JobVersionOut:
    try:
        return await get_job_version_detail(
            session, job_id=job_id, version_id=version_id
        )
    except JobNotFoundError as exc:
        raise _map_service_error(exc) from exc


@router.post(
    "/{job_id}/versions/{version_id}/copy-to-draft",
    response_model=JobDetail,
)
async def copy_version_to_draft_endpoint(
    job_id: UUID,
    version_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobDetail:
    try:
        return await copy_version_to_draft(
            session,
            job_id=job_id,
            version_id=version_id,
            actor=actor,
            request_context=_request_context(request),
        )
    except (JobNotFoundError, JobStateError) as exc:
        raise _map_service_error(exc) from exc


@router.patch("/{job_id}/draft", response_model=JobDetail)
async def save_draft_endpoint(
    job_id: UUID,
    payload: SaveDraftRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobDetail:
    try:
        return await save_job_draft(
            session,
            job_id=job_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except (JobNotFoundError, JobStateError) as exc:
        raise _map_service_error(exc) from exc


@router.post("/{job_id}/publish", response_model=JobDetail)
async def publish_job_endpoint(
    job_id: UUID,
    request: Request,
    payload: PublishJobRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobDetail:
    try:
        return await publish_job(
            session,
            job_id=job_id,
            payload=payload or PublishJobRequest(),
            actor=actor,
            request_context=_request_context(request),
        )
    except (JobNotFoundError, JobStateError, JobValidationError) as exc:
        raise _map_service_error(exc) from exc


@router.post("/{job_id}/pause", response_model=JobDetail)
async def pause_job_endpoint(
    job_id: UUID,
    payload: ReasonRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobDetail:
    try:
        return await pause_job(
            session,
            job_id=job_id,
            reason=payload.reason,
            actor=actor,
            request_context=_request_context(request),
        )
    except (JobNotFoundError, JobStateError) as exc:
        raise _map_service_error(exc) from exc


@router.post("/{job_id}/resume", response_model=JobDetail)
async def resume_job_endpoint(
    job_id: UUID,
    payload: ReasonRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobDetail:
    try:
        return await resume_job(
            session,
            job_id=job_id,
            reason=payload.reason,
            actor=actor,
            request_context=_request_context(request),
        )
    except (JobNotFoundError, JobStateError) as exc:
        raise _map_service_error(exc) from exc


@router.post("/{job_id}/close", response_model=JobDetail)
async def close_job_endpoint(
    job_id: UUID,
    payload: ReasonRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobDetail:
    try:
        return await close_job(
            session,
            job_id=job_id,
            reason=payload.reason,
            actor=actor,
            request_context=_request_context(request),
        )
    except (JobNotFoundError, JobStateError, CandidateStateError) as exc:
        raise _map_service_error(exc) from exc


@router.post(
    "/{job_id}/copy",
    response_model=JobDetail,
    status_code=status.HTTP_201_CREATED,
)
async def copy_job_endpoint(
    job_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobDetail:
    try:
        return await copy_job(
            session,
            job_id=job_id,
            actor=actor,
            request_context=_request_context(request),
        )
    except (JobNotFoundError, JobStateError) as exc:
        raise _map_service_error(exc) from exc


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job_endpoint(
    job_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> None:
    try:
        await remove_job(
            session,
            job_id=job_id,
            actor=actor,
            request_context=_request_context(request),
        )
    except (JobNotFoundError, JobStateError) as exc:
        raise _map_service_error(exc) from exc
