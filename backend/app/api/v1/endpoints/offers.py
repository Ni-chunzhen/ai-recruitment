"""HTTP API for Offer console delivery (manage-only; Task 4)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_permission
from app.models import User
from app.schemas.offer import (
    MarkStaleOfferAttemptIn,
    MarkStaleOfferAttemptOut,
    OfferAttemptListResponse,
    OfferAttemptOut,
    OfferCreateRequest,
    OfferDetailOut,
    OfferListResponse,
    OfferReadyRequest,
    OfferRetryRequest,
    OfferSendOut,
    OfferSendRequest,
    OfferSummaryOut,
    OfferUpdateRequest,
    OfferVoidRequest,
)
from app.services.audit import RequestContext
from app.services.offers import (
    OfferAttemptSummary,
    OfferConflictError,
    OfferDetail,
    OfferNotFoundError,
    OfferResult,
    OfferSendResult,
    OfferStateError,
    OfferSummary,
    OfferValidationError,
    confirm_offer_send,
    create_offer,
    get_offer_detail,
    list_offer_attempts,
    list_offers_for_application,
    mark_offer_ready,
    mark_stale_failed_offer_send_attempt,
    retry_offer_send,
    update_offer_draft,
    void_offer,
)

router = APIRouter(tags=["offers"])


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", "unknown"),
        ip_address=request.client.host if request.client else None,
    )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OfferNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if isinstance(exc, OfferConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, OfferStateError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, OfferValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _to_summary_out(item: OfferSummary | OfferResult) -> OfferSummaryOut:
    return OfferSummaryOut(
        id=item.id,
        application_id=item.application_id,
        status=item.status,
        hiring_decision_id=item.hiring_decision_id,
        recipient_email_masked=item.recipient_email_masked,
        recipient_name=item.recipient_name,
        lock_version=item.lock_version,
        version_no=item.version_no,
        content_hash=item.content_hash,
        frozen=item.frozen,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _to_detail_out(item: OfferDetail) -> OfferDetailOut:
    return OfferDetailOut(
        id=item.id,
        application_id=item.application_id,
        status=item.status,
        hiring_decision_id=item.hiring_decision_id,
        recipient_email_masked=item.recipient_email_masked,
        recipient_name=item.recipient_name,
        lock_version=item.lock_version,
        version_id=item.version_id,
        version_no=item.version_no,
        content_hash=item.content_hash,
        frozen=item.frozen,
        subject=item.subject,
        body_html=item.body_html,
        body_text=item.body_text,
        template_code=item.template_code,
        template_version=item.template_version,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _to_result_out(item: OfferResult) -> OfferSummaryOut:
    return _to_summary_out(item)


def _to_send_out(item: OfferSendResult) -> OfferSendOut:
    return OfferSendOut(
        offer_id=item.offer_id,
        attempt_id=item.attempt_id,
        status=item.status,
        attempt_status=item.attempt_status,
        attempt_no=item.attempt_no,
        lock_version=item.lock_version,
        version_id=item.version_id,
        provider=item.provider,
    )


def _to_attempt_out(item: OfferAttemptSummary) -> OfferAttemptOut:
    return OfferAttemptOut(
        id=item.id,
        offer_id=item.offer_id,
        offer_version_id=item.offer_version_id,
        provider=item.provider,
        status=item.status,
        attempt_no=item.attempt_no,
        error_code=item.error_code,
        error_message_safe=item.error_message_safe,
        started_at=item.started_at,
        finished_at=item.finished_at,
        next_retry_at=item.next_retry_at,
        created_at=item.created_at,
    )


@router.post(
    "/applications/{application_id}/offers",
    response_model=OfferSummaryOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_offer_endpoint(
    application_id: UUID,
    payload: OfferCreateRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> OfferSummaryOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await create_offer(
            session,
            application_id=application_id,
            actor=actor,
            request_context=_request_context(request),
            idempotency_key=payload.idempotency_key,
        )
    except (
        OfferNotFoundError,
        OfferConflictError,
        OfferStateError,
        OfferValidationError,
    ) as exc:
        raise _map_error(exc) from exc
    return _to_result_out(result)


@router.get(
    "/applications/{application_id}/offers",
    response_model=OfferListResponse,
)
async def list_offers_endpoint(
    application_id: UUID,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> OfferListResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        items = await list_offers_for_application(
            session, application_id=application_id
        )
    except OfferNotFoundError as exc:
        raise _map_error(exc) from exc
    return OfferListResponse(items=[_to_summary_out(item) for item in items])


@router.get(
    "/offers/{offer_id}",
    response_model=OfferDetailOut,
)
async def get_offer_endpoint(
    offer_id: UUID,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> OfferDetailOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        detail = await get_offer_detail(session, offer_id=offer_id)
    except (OfferNotFoundError, OfferValidationError) as exc:
        raise _map_error(exc) from exc
    return _to_detail_out(detail)


@router.patch(
    "/offers/{offer_id}",
    response_model=OfferSummaryOut,
)
async def update_offer_endpoint(
    offer_id: UUID,
    payload: OfferUpdateRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> OfferSummaryOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await update_offer_draft(
            session,
            offer_id=offer_id,
            subject=payload.subject,
            body_html=payload.body_html,
            body_text=payload.body_text,
            lock_version=payload.lock_version,
            actor=actor,
            request_context=_request_context(request),
            idempotency_key=payload.idempotency_key,
        )
    except (
        OfferNotFoundError,
        OfferConflictError,
        OfferStateError,
        OfferValidationError,
    ) as exc:
        raise _map_error(exc) from exc
    return _to_result_out(result)


@router.post(
    "/offers/{offer_id}/ready",
    response_model=OfferSummaryOut,
)
async def ready_offer_endpoint(
    offer_id: UUID,
    payload: OfferReadyRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> OfferSummaryOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await mark_offer_ready(
            session,
            offer_id=offer_id,
            lock_version=payload.lock_version,
            actor=actor,
            request_context=_request_context(request),
            idempotency_key=payload.idempotency_key,
        )
    except (
        OfferNotFoundError,
        OfferConflictError,
        OfferStateError,
        OfferValidationError,
    ) as exc:
        raise _map_error(exc) from exc
    return _to_result_out(result)


@router.post(
    "/offers/{offer_id}/send",
    response_model=OfferSendOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_offer_endpoint(
    offer_id: UUID,
    payload: OfferSendRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> OfferSendOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await confirm_offer_send(
            session,
            offer_id=offer_id,
            offer_version_id=payload.offer_version_id,
            lock_version=payload.lock_version,
            idempotency_key=payload.idempotency_key,
            actor=actor,
            request_context=_request_context(request),
        )
    except (
        OfferNotFoundError,
        OfferConflictError,
        OfferStateError,
        OfferValidationError,
    ) as exc:
        raise _map_error(exc) from exc
    return _to_send_out(result)


@router.post(
    "/offers/{offer_id}/retry",
    response_model=OfferSendOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_offer_endpoint(
    offer_id: UUID,
    payload: OfferRetryRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> OfferSendOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await retry_offer_send(
            session,
            offer_id=offer_id,
            lock_version=payload.lock_version,
            idempotency_key=payload.idempotency_key,
            actor=actor,
            request_context=_request_context(request),
        )
    except (
        OfferNotFoundError,
        OfferConflictError,
        OfferStateError,
        OfferValidationError,
    ) as exc:
        raise _map_error(exc) from exc
    return _to_send_out(result)


@router.post(
    "/offers/{offer_id}/void",
    response_model=OfferSummaryOut,
)
async def void_offer_endpoint(
    offer_id: UUID,
    payload: OfferVoidRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> OfferSummaryOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await void_offer(
            session,
            offer_id=offer_id,
            lock_version=payload.lock_version,
            void_reason_code=payload.void_reason_code,
            actor=actor,
            request_context=_request_context(request),
            idempotency_key=payload.idempotency_key,
        )
    except (
        OfferNotFoundError,
        OfferConflictError,
        OfferStateError,
        OfferValidationError,
    ) as exc:
        raise _map_error(exc) from exc
    return _to_result_out(result)


@router.post(
    "/offers/{offer_id}/attempts/{attempt_id}/mark-stale-failed",
    response_model=MarkStaleOfferAttemptOut,
)
async def mark_stale_offer_attempt_endpoint(
    offer_id: UUID,
    attempt_id: UUID,
    payload: MarkStaleOfferAttemptIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> MarkStaleOfferAttemptOut:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await mark_stale_failed_offer_send_attempt(
            session,
            offer_id=offer_id,
            attempt_id=attempt_id,
            expected_updated_at=payload.expected_updated_at,
            actor=actor,
            request_context=_request_context(request),
        )
    except (
        OfferNotFoundError,
        OfferConflictError,
        OfferStateError,
        OfferValidationError,
    ) as exc:
        raise _map_error(exc) from exc
    return MarkStaleOfferAttemptOut(
        offer_id=result.offer_id,
        attempt_id=result.attempt_id,
        offer_status=result.offer_status,
        attempt_status=result.attempt_status,
        error_code=result.error_code,
        updated_at=result.updated_at,
        finished_at=result.finished_at,
    )


@router.get(
    "/offers/{offer_id}/attempts",
    response_model=OfferAttemptListResponse,
)
async def list_offer_attempts_endpoint(
    offer_id: UUID,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> OfferAttemptListResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        items = await list_offer_attempts(session, offer_id=offer_id)
    except OfferNotFoundError as exc:
        raise _map_error(exc) from exc
    return OfferAttemptListResponse(items=[_to_attempt_out(item) for item in items])
