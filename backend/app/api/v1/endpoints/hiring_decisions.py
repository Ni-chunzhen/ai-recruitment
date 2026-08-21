"""HTTP API for post-interview hiring decisions."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_permission
from app.models import User
from app.schemas.hiring_decision import (
    HiringDecisionListResponse,
    HiringDecisionOut,
    HiringDecisionRequest,
    HiringReasonCodeItem,
    HiringReasonCodeListResponse,
)
from app.services.audit import RequestContext
from app.services.hiring_decisions import (
    HiringConflictError,
    HiringDecisionRequestData,
    HiringDecisionResult,
    HiringNotFoundError,
    HiringStateError,
    HiringValidationError,
    create_hiring_decision,
    list_hiring_decisions,
    list_hiring_reason_catalog,
)

router = APIRouter(tags=["hiring-decisions"])


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", "unknown"),
        ip_address=request.client.host if request.client else None,
    )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HiringNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if isinstance(exc, HiringConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, HiringStateError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, HiringValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _to_out(result: HiringDecisionResult) -> HiringDecisionOut:
    return HiringDecisionOut(
        id=result.id,
        application_id=result.application_id,
        decision=result.decision,  # type: ignore[arg-type]
        reason_code=result.reason_code,
        round_id=result.round_id,
        analysis_version_id=result.analysis_version_id,
        overall_score=result.overall_score,
        analysis_version_no=result.analysis_version_no,
        from_pipeline_status=result.from_pipeline_status,  # type: ignore[arg-type]
        to_pipeline_status=result.to_pipeline_status,  # type: ignore[arg-type]
        lock_version=result.lock_version,
        created_at=result.created_at,
        decided_by=result.decided_by,
    )


@router.post(
    "/applications/{application_id}/hiring-decisions",
    response_model=HiringDecisionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_hiring_decision_endpoint(
    application_id: UUID,
    payload: HiringDecisionRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> HiringDecisionOut:
    try:
        result = await create_hiring_decision(
            session,
            application_id=application_id,
            payload=HiringDecisionRequestData(
                decision=payload.decision,
                reason_code=payload.reason_code,
                analysis_version_id=payload.analysis_version_id,
                lock_version=payload.lock_version,
                idempotency_key=payload.idempotency_key,
            ),
            actor=actor,
            request_context=_request_context(request),
        )
    except (
        HiringNotFoundError,
        HiringConflictError,
        HiringStateError,
        HiringValidationError,
    ) as exc:
        raise _map_error(exc) from exc
    return _to_out(result)


@router.get(
    "/applications/{application_id}/hiring-decisions",
    response_model=HiringDecisionListResponse,
)
async def list_hiring_decisions_endpoint(
    application_id: UUID,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_permission("recruitment.manage")),
) -> HiringDecisionListResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        items = await list_hiring_decisions(session, application_id=application_id)
    except HiringNotFoundError as exc:
        raise _map_error(exc) from exc
    return HiringDecisionListResponse(items=[_to_out(item) for item in items])


@router.get(
    "/hiring-decision-reason-codes",
    response_model=HiringReasonCodeListResponse,
)
async def list_hiring_reason_codes_endpoint(
    _: User = Depends(require_permission("recruitment.manage")),
) -> HiringReasonCodeListResponse:
    return HiringReasonCodeListResponse(
        items=[
            HiringReasonCodeItem(
                code=str(item["code"]),
                label=str(item["label"]),
                allowed_decisions=list(item["allowed_decisions"]),  # type: ignore[arg-type]
            )
            for item in list_hiring_reason_catalog()
        ]
    )
