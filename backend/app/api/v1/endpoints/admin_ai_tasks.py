from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_permission
from app.models import User
from app.repositories.ai_tasks import AITaskNotFoundError
from app.schemas.ai_task import (
    AITaskAdminDetailOut,
    AITaskAdminListResponse,
    CancelAITaskRequest,
    MarkStaleFailedAITaskIn,
    MarkStaleFailedAITaskOut,
)
from app.services.ai_tasks import (
    AITaskStateError,
    cancel_ai_task,
    get_admin_ai_task,
    list_admin_ai_tasks,
    mark_stale_failed_ai_task,
    retry_ai_task,
)
from app.services.audit import RequestContext

router = APIRouter(prefix="/admin/ai-tasks", tags=["admin-ai-tasks"])


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", "unknown"),
        ip_address=request.client.host if request.client else None,
    )


def _map_service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AITaskNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if isinstance(exc, AITaskStateError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", response_model=AITaskAdminListResponse)
async def list_admin_ai_tasks_endpoint(
    task_type: str | None = Query(default=None, max_length=64),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    business_type: str | None = Query(default=None, max_length=64),
    business_id: UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    keyword: str | None = Query(default=None, max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("audit.read")),
) -> AITaskAdminListResponse:
    return await list_admin_ai_tasks(
        session,
        task_type=task_type,
        status=status_filter,
        business_type=business_type,
        business_id=business_id,
        created_from=created_from,
        created_to=created_to,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.get("/{task_id}", response_model=AITaskAdminDetailOut)
async def get_admin_ai_task_endpoint(
    task_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("audit.read")),
) -> AITaskAdminDetailOut:
    try:
        return await get_admin_ai_task(session, task_id)
    except AITaskNotFoundError as exc:
        raise _map_service_error(exc) from exc


@router.post("/{task_id}/retry", response_model=AITaskAdminDetailOut)
async def retry_admin_ai_task_endpoint(
    task_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("ai_task.manage")),
) -> AITaskAdminDetailOut:
    try:
        await retry_ai_task(
            session,
            task_id=task_id,
            actor=actor,
            request_context=_request_context(request),
        )
        return await get_admin_ai_task(session, task_id)
    except Exception as exc:
        if isinstance(exc, (AITaskNotFoundError, AITaskStateError)):
            raise _map_service_error(exc) from exc
        raise


@router.post("/{task_id}/cancel", response_model=AITaskAdminDetailOut)
async def cancel_admin_ai_task_endpoint(
    task_id: UUID,
    request: Request,
    payload: CancelAITaskRequest | None = None,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("ai_task.manage")),
) -> AITaskAdminDetailOut:
    try:
        await cancel_ai_task(
            session,
            task_id=task_id,
            actor=actor,
            request_context=_request_context(request),
            reason=(payload.reason if payload else None),
        )
        return await get_admin_ai_task(session, task_id)
    except Exception as exc:
        if isinstance(exc, (AITaskNotFoundError, AITaskStateError)):
            raise _map_service_error(exc) from exc
        raise


@router.post("/{task_id}/mark-stale-failed", response_model=MarkStaleFailedAITaskOut)
async def mark_stale_failed_admin_ai_task_endpoint(
    task_id: UUID,
    payload: MarkStaleFailedAITaskIn,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("ai_task.manage")),
) -> MarkStaleFailedAITaskOut:
    try:
        return await mark_stale_failed_ai_task(
            session,
            task_id=task_id,
            expected_updated_at=payload.expected_updated_at,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:
        if isinstance(exc, (AITaskNotFoundError, AITaskStateError)):
            raise _map_service_error(exc) from exc
        raise
