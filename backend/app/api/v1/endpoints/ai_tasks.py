from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_permission
from app.models import User
from app.repositories.ai_tasks import AITaskNotFoundError
from app.repositories.jobs import JobNotFoundError
from app.schemas.ai_task import (
    AITaskListResponse,
    AITaskSummaryOut,
    CancelAITaskRequest,
    CreateAITaskRequest,
)
from app.schemas.job import JobDetail
from app.services.ai_tasks import (
    AITaskStateError,
    AITaskValidationError,
    apply_ai_task_result,
    cancel_ai_task,
    create_ai_task,
    get_ai_task,
    list_ai_tasks,
    retry_ai_task,
)
from app.services.audit import RequestContext

router = APIRouter(prefix="/ai-tasks", tags=["ai-tasks"])


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", "unknown"),
        ip_address=request.client.host if request.client else None,
    )


def _map_service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (AITaskNotFoundError, JobNotFoundError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if isinstance(exc, AITaskValidationError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    if isinstance(exc, AITaskStateError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    )


@router.post("", response_model=AITaskSummaryOut, status_code=status.HTTP_201_CREATED)
async def create_ai_task_endpoint(
    payload: CreateAITaskRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> AITaskSummaryOut:
    try:
        return await create_ai_task(
            session,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:
        if isinstance(
            exc,
            (
                AITaskNotFoundError,
                JobNotFoundError,
                AITaskValidationError,
                AITaskStateError,
            ),
        ):
            raise _map_service_error(exc) from exc
        raise


@router.get("", response_model=AITaskListResponse)
async def list_ai_tasks_endpoint(
    business_type: str = Query(..., min_length=1, max_length=64),
    business_id: UUID = Query(...),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> AITaskListResponse:
    return await list_ai_tasks(
        session,
        business_type=business_type,
        business_id=business_id,
    )


@router.get("/{task_id}", response_model=AITaskSummaryOut)
async def get_ai_task_endpoint(
    task_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> AITaskSummaryOut:
    try:
        return await get_ai_task(session, task_id)
    except AITaskNotFoundError as exc:
        raise _map_service_error(exc) from exc


@router.post("/{task_id}/retry", response_model=AITaskSummaryOut)
async def retry_ai_task_endpoint(
    task_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> AITaskSummaryOut:
    try:
        return await retry_ai_task(
            session,
            task_id=task_id,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:
        if isinstance(
            exc,
            (AITaskNotFoundError, AITaskStateError, AITaskValidationError),
        ):
            raise _map_service_error(exc) from exc
        raise


@router.post("/{task_id}/cancel", response_model=AITaskSummaryOut)
async def cancel_ai_task_endpoint(
    task_id: UUID,
    request: Request,
    payload: CancelAITaskRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> AITaskSummaryOut:
    try:
        return await cancel_ai_task(
            session,
            task_id=task_id,
            actor=actor,
            request_context=_request_context(request),
            reason=(payload.reason if payload else None),
        )
    except Exception as exc:
        if isinstance(
            exc,
            (AITaskNotFoundError, AITaskStateError, AITaskValidationError),
        ):
            raise _map_service_error(exc) from exc
        raise


@router.post("/{task_id}/apply", response_model=JobDetail)
async def apply_ai_task_endpoint(
    task_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> JobDetail:
    try:
        return await apply_ai_task_result(
            session,
            task_id=task_id,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:
        if isinstance(
            exc,
            (
                AITaskNotFoundError,
                JobNotFoundError,
                AITaskStateError,
                AITaskValidationError,
            ),
        ):
            raise _map_service_error(exc) from exc
        raise
