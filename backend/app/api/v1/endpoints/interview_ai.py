"""Interview question-outline and round-analysis API endpoints."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_any_permission
from app.models import User
from app.models.ai_task import AITask
from app.schemas.interview_ai_api import (
    InterviewAIGenerateOut,
    InterviewAIGenerateRequest,
    InterviewAnalysisSetOut,
    InterviewAnalysisVersionDetailOut,
    InterviewQuestionConfirmRequest,
    InterviewQuestionEditRequest,
    InterviewQuestionSetOut,
    InterviewQuestionVersionDetailOut,
)
from app.services.audit import RequestContext
from app.services.interview_analyses import (
    dispatch_persisted_analysis_generation_task,
    get_analysis_version_detail,
    list_analysis_versions,
    request_analysis_generation,
)
from app.services.interview_questions import (
    confirm_question_set,
    create_manual_question_version,
    dispatch_persisted_question_generation_task,
    get_question_version_detail,
    list_question_versions,
    request_question_generation,
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

router = APIRouter(tags=["interview-ai"])
logger = logging.getLogger(__name__)

_ROUND_ACCESS = require_any_permission("recruitment.manage", "interview.execute")
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
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _commit_then_dispatch(
    session: AsyncSession, *, task: AITask, dispatch: DispatchFn
) -> InterviewAIGenerateOut:
    await session.commit()
    dispatch_status: Literal["queued", "pending_dispatch"] = "queued"
    try:
        await dispatch(session=session, task_id=task.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview ai dispatch failed task_id=%s error=%s",
            task.id,
            type(exc).__name__,
        )
        dispatch_status = "pending_dispatch"
    return InterviewAIGenerateOut(
        task_id=task.id,
        task_type=task.task_type,
        status=task.status,
        round_id=task.business_id,
        dispatch_status=dispatch_status,
    )


@router.post(
    "/interview-rounds/{round_id}/question-set/generate",
    response_model=InterviewAIGenerateOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_question_set(
    round_id: UUID,
    payload: InterviewAIGenerateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_ROUND_ACCESS),
) -> InterviewAIGenerateOut:
    try:
        task = await request_question_generation(
            session=session,
            round_id=round_id,
            idempotency_key=payload.idempotency_key,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
    return await _commit_then_dispatch(
        session,
        task=task,
        dispatch=dispatch_persisted_question_generation_task,
    )


@router.get(
    "/interview-rounds/{round_id}/question-set",
    response_model=InterviewQuestionSetOut,
)
async def get_question_set(
    round_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_ROUND_ACCESS),
) -> InterviewQuestionSetOut:
    try:
        summary = await list_question_versions(
            session=session, round_id=round_id, actor=actor
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
    return InterviewQuestionSetOut.model_validate(summary)


@router.get(
    "/interview-rounds/{round_id}/question-set/versions/{version_id}",
    response_model=InterviewQuestionVersionDetailOut,
)
async def get_question_set_version(
    round_id: UUID,
    version_id: UUID,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_ROUND_ACCESS),
) -> InterviewQuestionVersionDetailOut:
    response.headers["Cache-Control"] = _NO_STORE
    try:
        detail = await get_question_version_detail(
            session=session, round_id=round_id, version_id=version_id, actor=actor
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
    return InterviewQuestionVersionDetailOut.model_validate(detail)


@router.post(
    "/interview-rounds/{round_id}/question-set/versions",
    response_model=InterviewQuestionVersionDetailOut,
)
async def create_question_set_version(
    round_id: UUID,
    payload: InterviewQuestionEditRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_ROUND_ACCESS),
) -> InterviewQuestionVersionDetailOut:
    response.headers["Cache-Control"] = _NO_STORE
    try:
        detail = await create_manual_question_version(
            session=session,
            round_id=round_id,
            expected_current_version_id=payload.expected_current_version_id,
            questions=[item.model_dump(mode="json") for item in payload.questions],
            idempotency_key=payload.idempotency_key,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
    await session.commit()
    return InterviewQuestionVersionDetailOut.model_validate(detail)


@router.post(
    "/interview-rounds/{round_id}/question-set/confirm",
    response_model=InterviewQuestionSetOut,
)
async def confirm_question_set_endpoint(
    round_id: UUID,
    payload: InterviewQuestionConfirmRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_ROUND_ACCESS),
) -> InterviewQuestionSetOut:
    try:
        summary = await confirm_question_set(
            session=session,
            round_id=round_id,
            expected_current_version_id=payload.expected_current_version_id,
            idempotency_key=payload.idempotency_key,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
    await session.commit()
    return InterviewQuestionSetOut.model_validate(summary)


@router.post(
    "/interview-rounds/{round_id}/analysis/generate",
    response_model=InterviewAIGenerateOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_analysis(
    round_id: UUID,
    payload: InterviewAIGenerateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_ROUND_ACCESS),
) -> InterviewAIGenerateOut:
    try:
        task = await request_analysis_generation(
            session=session,
            round_id=round_id,
            idempotency_key=payload.idempotency_key,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
    return await _commit_then_dispatch(
        session,
        task=task,
        dispatch=dispatch_persisted_analysis_generation_task,
    )


@router.get(
    "/interview-rounds/{round_id}/analysis",
    response_model=InterviewAnalysisSetOut,
)
async def get_analysis(
    round_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_ROUND_ACCESS),
) -> InterviewAnalysisSetOut:
    try:
        summary = await list_analysis_versions(
            session=session, round_id=round_id, actor=actor
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
    return InterviewAnalysisSetOut.model_validate(summary)


@router.get(
    "/interview-rounds/{round_id}/analysis/versions/{version_id}",
    response_model=InterviewAnalysisVersionDetailOut,
)
async def get_analysis_version(
    round_id: UUID,
    version_id: UUID,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_ROUND_ACCESS),
) -> InterviewAnalysisVersionDetailOut:
    response.headers["Cache-Control"] = _NO_STORE
    try:
        detail = await get_analysis_version_detail(
            session=session, round_id=round_id, version_id=version_id, actor=actor
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
    return InterviewAnalysisVersionDetailOut.model_validate(detail)
