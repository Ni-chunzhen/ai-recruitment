"""Manual invitation API endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_db_session,
    require_any_permission,
    require_permission,
)
from app.models import User
from app.schemas.invitation import (
    ConfirmInvitationRequest,
    ConfirmInvitationResponse,
    CopyAuditRequest,
    GenerateInvitationsRequest,
    GenerateInvitationsResponse,
    InvitationListResponse,
    InvitationMessageDetailOut,
    InvitationMessageSummaryOut,
    RecordSentRequest,
    RecordSentResponse,
    UpdateInvitationMessageRequest,
)
from app.services.audit import RequestContext
from app.services.interview_state import InterviewStateError
from app.services.interviews import (
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
)
from app.services.invitations import (
    audit_copy,
    confirm_invitation,
    generate_invitations,
    get_invitation_detail,
    list_invitations,
    record_sent,
    update_invitation,
)

router = APIRouter(tags=["interview-invitations"])


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
        ),
    ):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (InterviewValidationError, InterviewStateError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/interview-rounds/{round_id}/invitations/generate",
    response_model=GenerateInvitationsResponse,
)
async def post_generate_invitations(
    round_id: UUID,
    payload: GenerateInvitationsRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> GenerateInvitationsResponse:
    try:
        return await generate_invitations(
            session,
            round_id=round_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get(
    "/interview-rounds/{round_id}/invitations",
    response_model=InvitationListResponse,
)
async def get_round_invitations(
    round_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(
        require_any_permission("recruitment.manage", "interview.execute")
    ),
) -> InvitationListResponse:
    try:
        return await list_invitations(session, round_id=round_id, actor=actor)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get(
    "/interview-invitations/{message_id}",
    response_model=InvitationMessageDetailOut,
)
async def get_invitation(
    message_id: UUID,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(
        require_any_permission("recruitment.manage", "interview.execute")
    ),
) -> InvitationMessageDetailOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await get_invitation_detail(
            session, message_id=message_id, actor=actor
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.put(
    "/interview-invitations/{message_id}",
    response_model=InvitationMessageDetailOut,
)
async def put_invitation(
    message_id: UUID,
    payload: UpdateInvitationMessageRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> InvitationMessageDetailOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await update_invitation(
            session,
            message_id=message_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post(
    "/interview-invitations/{message_id}/copy-audit",
    response_model=InvitationMessageSummaryOut,
)
async def post_copy_audit(
    message_id: UUID,
    payload: CopyAuditRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> InvitationMessageSummaryOut:
    try:
        return await audit_copy(
            session,
            message_id=message_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post(
    "/interview-invitations/{message_id}/record-sent",
    response_model=RecordSentResponse,
)
async def post_record_sent(
    message_id: UUID,
    payload: RecordSentRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> RecordSentResponse:
    try:
        return await record_sent(
            session,
            message_id=message_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post(
    "/interview-rounds/{round_id}/confirm-invitation",
    response_model=ConfirmInvitationResponse,
)
async def post_confirm_invitation(
    round_id: UUID,
    payload: ConfirmInvitationRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> ConfirmInvitationResponse:
    try:
        return await confirm_invitation(
            session,
            round_id=round_id,
            payload=payload,
            actor=actor,
            request_context=_request_context(request),
        )
    except Exception as exc:
        raise _map_error(exc) from exc
