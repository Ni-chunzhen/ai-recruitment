from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_db_session,
    require_any_permission,
    require_permission,
)
from app.models import User
from app.schemas.interview import (
    InterviewAbnormalEndRequest,
    InterviewCancelRequest,
    InterviewConflictCheckRequest,
    InterviewConflictOut,
    InterviewReasonCodeListResponse,
    InterviewRescheduleRequest,
    InterviewRoundActionOut,
    InterviewRoundActionRequest,
    InterviewRoundCreate,
    InterviewRoundOut,
    InterviewRoundReorderRequest,
    InterviewRoundUpdate,
    InterviewScheduleCreate,
    InterviewStaffListResponse,
    InterviewTimelineOut,
)
from app.services.audit import RequestContext
from app.services.interview_state import InterviewStateError
from app.services.interviews import (
    InterviewConflictError,
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
    cancel_interview_round,
    check_interview_conflicts,
    complete_interview_round,
    create_interview_round,
    end_interview_abnormally,
    finish_interview_round,
    get_interview_timeline,
    list_interview_reason_codes,
    list_interview_staff,
    reorder_interview_rounds,
    reschedule_interview_round,
    schedule_interview_round,
    start_interview_round,
    update_interview_round,
)

router = APIRouter(tags=["interviews"])


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


@router.get(
    "/interview-reason-codes",
    response_model=InterviewReasonCodeListResponse,
)
async def get_interview_reason_codes(
    _: User = Depends(
        require_any_permission("recruitment.manage", "interview.execute")
    ),
) -> InterviewReasonCodeListResponse:
    return list_interview_reason_codes()


@router.get("/interview-staff", response_model=InterviewStaffListResponse)
async def get_interview_staff(
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> InterviewStaffListResponse:
    try:
        return await list_interview_staff(session, actor)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.get(
    "/applications/{application_id}/interview-rounds",
    response_model=InterviewTimelineOut,
)
async def list_interview_rounds_endpoint(
    application_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(
        require_any_permission("recruitment.manage", "interview.execute")
    ),
) -> InterviewTimelineOut:
    try:
        return await get_interview_timeline(
            session, application_id=application_id, actor=actor
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/applications/{application_id}/interview-rounds",
    response_model=InterviewRoundOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_interview_round_endpoint(
    application_id: UUID,
    payload: InterviewRoundCreate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> InterviewRoundOut:
    try:
        return await create_interview_round(
            session,
            application_id=application_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.put("/interview-rounds/{round_id}", response_model=InterviewRoundOut)
async def update_interview_round_endpoint(
    round_id: UUID,
    payload: InterviewRoundUpdate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> InterviewRoundOut:
    try:
        return await update_interview_round(
            session,
            round_id=round_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/interview-rounds/{round_id}/schedule",
    response_model=InterviewRoundActionOut,
)
async def schedule_interview_round_endpoint(
    round_id: UUID,
    payload: InterviewScheduleCreate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> InterviewRoundActionOut:
    try:
        return await schedule_interview_round(
            session,
            round_id=round_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/interview-rounds/{round_id}/reschedule",
    response_model=InterviewRoundActionOut,
)
async def reschedule_interview_round_endpoint(
    round_id: UUID,
    payload: InterviewRescheduleRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> InterviewRoundActionOut:
    try:
        return await reschedule_interview_round(
            session,
            round_id=round_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/interview-rounds/{round_id}/cancel",
    response_model=InterviewRoundActionOut,
)
async def cancel_interview_round_endpoint(
    round_id: UUID,
    payload: InterviewCancelRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> InterviewRoundActionOut:
    try:
        return await cancel_interview_round(
            session,
            round_id=round_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/interview-rounds/{round_id}/start",
    response_model=InterviewRoundActionOut,
)
async def start_interview_round_endpoint(
    round_id: UUID,
    payload: InterviewRoundActionRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(
        require_any_permission("recruitment.manage", "interview.execute")
    ),
) -> InterviewRoundActionOut:
    try:
        return await start_interview_round(
            session,
            round_id=round_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/interview-rounds/{round_id}/finish",
    response_model=InterviewRoundActionOut,
)
async def finish_interview_round_endpoint(
    round_id: UUID,
    payload: InterviewRoundActionRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(
        require_any_permission("recruitment.manage", "interview.execute")
    ),
) -> InterviewRoundActionOut:
    try:
        return await finish_interview_round(
            session,
            round_id=round_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/interview-rounds/{round_id}/complete",
    response_model=InterviewRoundActionOut,
)
async def complete_interview_round_endpoint(
    round_id: UUID,
    payload: InterviewRoundActionRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> InterviewRoundActionOut:
    try:
        return await complete_interview_round(
            session,
            round_id=round_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/interview-rounds/{round_id}/end-abnormally",
    response_model=InterviewRoundActionOut,
)
async def end_interview_abnormally_endpoint(
    round_id: UUID,
    payload: InterviewAbnormalEndRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(
        require_any_permission("recruitment.manage", "interview.execute")
    ),
) -> InterviewRoundActionOut:
    try:
        return await end_interview_abnormally(
            session,
            round_id=round_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/applications/{application_id}/interview-rounds/reorder",
    response_model=InterviewTimelineOut,
)
async def reorder_interview_rounds_endpoint(
    application_id: UUID,
    payload: InterviewRoundReorderRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> InterviewTimelineOut:
    try:
        return await reorder_interview_rounds(
            session,
            application_id=application_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc


@router.post(
    "/interview-rounds/conflicts/check",
    response_model=InterviewConflictOut,
)
async def check_interview_conflicts_endpoint(
    payload: InterviewConflictCheckRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> InterviewConflictOut:
    try:
        return await check_interview_conflicts(
            session,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from exc
