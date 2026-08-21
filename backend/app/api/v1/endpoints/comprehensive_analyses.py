"""Application-level comprehensive interview analysis endpoints (manage-only)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_permission
from app.models import User
from app.models.ai_task import AITask
from app.schemas.comprehensive_analysis_api import (
    ComprehensiveGenerateOut,
    ComprehensiveGenerateRequest,
    ComprehensiveSetOut,
    ComprehensiveVersionDetailOut,
)
from app.services.audit import RequestContext
from app.services.comprehensive_analyses import (
    dispatch_persisted_comprehensive_analysis_task,
    get_comprehensive_analysis_version_detail,
    list_comprehensive_analysis,
    request_comprehensive_analysis_generation,
)
from app.services.interview_state import InterviewStateError
from app.services.interviews import (
    InterviewConflictError,
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
)

router = APIRouter(tags=["comprehensive-analysis"])
logger = logging.getLogger(__name__)

_MANAGE_ONLY = require_permission("recruitment.manage")
_NO_STORE = "no-store"
DispatchFn = Callable[..., Awaitable[None]]


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
    if isinstance(exc, (InterviewValidationError, InterviewStateError)):
        message = str(exc)
        # pending_offer / non-interviewing generate → prefer 409 business state
        if "interviewing" in message.lower() or "pending_offer" in message.lower():
            return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _commit_then_dispatch(
    session: AsyncSession, *, task: AITask, dispatch: DispatchFn
) -> ComprehensiveGenerateOut:
    await session.commit()
    dispatch_status: Literal["queued", "pending_dispatch"] = "queued"
    try:
        await dispatch(session=session, task_id=task.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "comprehensive analysis dispatch failed task_id=%s error=%s",
            task.id,
            type(exc).__name__,
        )
        dispatch_status = "pending_dispatch"
    return ComprehensiveGenerateOut(
        task_id=task.id,
        task_type=task.task_type,
        status=task.status,
        application_id=task.business_id,
        dispatch_status=dispatch_status,
    )


@router.post(
    "/applications/{application_id}/comprehensive-analysis/generate",
    response_model=ComprehensiveGenerateOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_comprehensive_analysis(
    application_id: UUID,
    payload: ComprehensiveGenerateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_MANAGE_ONLY),
) -> ComprehensiveGenerateOut:
    try:
        task = await request_comprehensive_analysis_generation(
            session=session,
            application_id=application_id,
            idempotency_key=payload.idempotency_key,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
    return await _commit_then_dispatch(
        session,
        task=task,
        dispatch=dispatch_persisted_comprehensive_analysis_task,
    )


@router.get(
    "/applications/{application_id}/comprehensive-analysis",
    response_model=ComprehensiveSetOut,
)
async def get_comprehensive_analysis_set(
    application_id: UUID,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_MANAGE_ONLY),
) -> ComprehensiveSetOut:
    response.headers["Cache-Control"] = _NO_STORE
    try:
        summary = await list_comprehensive_analysis(
            session=session, application_id=application_id, actor=actor
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
    return ComprehensiveSetOut.model_validate(summary)


@router.get(
    "/applications/{application_id}/comprehensive-analysis/versions/{version_id}",
    response_model=ComprehensiveVersionDetailOut,
)
async def get_comprehensive_analysis_version(
    application_id: UUID,
    version_id: UUID,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_MANAGE_ONLY),
) -> ComprehensiveVersionDetailOut:
    response.headers["Cache-Control"] = _NO_STORE
    try:
        detail = await get_comprehensive_analysis_version_detail(
            session=session,
            application_id=application_id,
            version_id=version_id,
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
    return ComprehensiveVersionDetailOut.model_validate(detail)
