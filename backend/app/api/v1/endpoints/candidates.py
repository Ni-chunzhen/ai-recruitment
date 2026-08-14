from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_permission
from app.models import User
from app.repositories.candidates import CandidateNotFoundError
from app.repositories.jobs import JobNotFoundError
from app.schemas.candidate import (
    ClosePreviewResponse,
    CreateCandidateRequest,
    JobApplicationListResponse,
    JobApplicationOut,
    MigrateVersionRequest,
    ResolveCloseRequest,
)
from app.services.audit import RequestContext
from app.services.candidates import (
    CandidateStateError,
    create_job_candidate,
    get_close_preview,
    list_job_candidates,
    migrate_application_version,
    resolve_close_application,
)
from app.services.jobs import JobStateError

router = APIRouter(prefix="/jobs", tags=["candidates"])


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", "unknown"),
        ip_address=request.client.host if request.client else None,
    )


def _map_service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (CandidateNotFoundError, JobNotFoundError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if isinstance(exc, (CandidateStateError, JobStateError)):
        message = str(exc)
        if "in-flight" in message:
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            )
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    )


@router.post(
    "/{job_id}/candidates",
    response_model=JobApplicationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_candidate_endpoint(
    job_id: UUID,
    payload: CreateCandidateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobApplicationOut:
    try:
        return await create_job_candidate(
            session,
            job_id=job_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except (JobNotFoundError, CandidateStateError) as exc:
        raise _map_service_error(exc) from exc


@router.get("/{job_id}/candidates", response_model=JobApplicationListResponse)
async def list_candidates_endpoint(
    job_id: UUID,
    in_flight_only: bool = Query(default=False),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> JobApplicationListResponse:
    try:
        return await list_job_candidates(
            session, job_id=job_id, in_flight_only=in_flight_only
        )
    except JobNotFoundError as exc:
        raise _map_service_error(exc) from exc


@router.get("/{job_id}/close-preview", response_model=ClosePreviewResponse)
async def close_preview_endpoint(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> ClosePreviewResponse:
    try:
        return await get_close_preview(session, job_id=job_id)
    except JobNotFoundError as exc:
        raise _map_service_error(exc) from exc


@router.post(
    "/{job_id}/candidates/{application_id}/resolve-close",
    response_model=JobApplicationOut,
)
async def resolve_close_endpoint(
    job_id: UUID,
    application_id: UUID,
    payload: ResolveCloseRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobApplicationOut:
    try:
        return await resolve_close_application(
            session,
            job_id=job_id,
            application_id=application_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except (JobNotFoundError, CandidateNotFoundError, CandidateStateError) as exc:
        raise _map_service_error(exc) from exc


@router.post(
    "/{job_id}/candidates/{application_id}/migrate-version",
    response_model=JobApplicationOut,
)
async def migrate_version_endpoint(
    job_id: UUID,
    application_id: UUID,
    payload: MigrateVersionRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobApplicationOut:
    try:
        return await migrate_application_version(
            session,
            job_id=job_id,
            application_id=application_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except (JobNotFoundError, CandidateNotFoundError, CandidateStateError) as exc:
        raise _map_service_error(exc) from exc
