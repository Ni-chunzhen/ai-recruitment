"""Admin HTTP API for third-party integration configuration."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_permission
from app.models import User
from app.schemas.integration import (
    ConnectivityTestOut,
    DifyUpdateIn,
    IntegrationsSummaryOut,
    MinioUpdateIn,
)
from app.services.audit import RequestContext
from app.services.integrations import (
    IntegrationMailWriteError,
    IntegrationValidationError,
    get_integrations_summary,
    test_dify,
    test_mail,
    test_minio,
    update_dify,
    update_minio,
)

router = APIRouter(prefix="/admin/integrations", tags=["admin-integrations"])

_MANAGE = require_permission("integration.manage")
_NO_STORE = "no-store"
_TestProvider = Literal["dify", "minio", "mail"]


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", "unknown"),
        ip_address=request.client.host if request.client else None,
    )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, IntegrationMailWriteError):
        return HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="mail integration is read-only",
        )
    if isinstance(exc, IntegrationValidationError):
        # Never echo payload values (may contain secrets).
        detail = str(exc)
        if any(
            token in detail.lower()
            for token in ("enc:v1:", "bearer", "password=", "secret=")
        ):
            detail = "validation failed"
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="request failed"
    )


def _payload_dict(model: DifyUpdateIn | MinioUpdateIn) -> dict[str, Any]:
    # exclude_unset: omitted fields keep previous; empty string secrets handled in service
    return model.model_dump(exclude_unset=True)


@router.get("", response_model=IntegrationsSummaryOut)
async def get_integrations_endpoint(
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_MANAGE),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = _NO_STORE
    return await get_integrations_summary(session)


@router.put("/dify", response_model=IntegrationsSummaryOut)
async def put_dify_endpoint(
    body: DifyUpdateIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_MANAGE),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = _NO_STORE
    try:
        return await update_dify(
            session,
            payload=_payload_dict(body),
            actor_user_id=actor.id,
            request_context=_request_context(request),
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.put("/minio", response_model=IntegrationsSummaryOut)
async def put_minio_endpoint(
    body: MinioUpdateIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_MANAGE),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = _NO_STORE
    try:
        return await update_minio(
            session,
            payload=_payload_dict(body),
            actor_user_id=actor.id,
            request_context=_request_context(request),
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/{provider}/test", response_model=ConnectivityTestOut)
async def post_connectivity_test_endpoint(
    provider: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(_MANAGE),
) -> ConnectivityTestOut:
    response.headers["Cache-Control"] = _NO_STORE
    normalized = provider.strip().lower()
    if normalized not in {"dify", "minio", "mail"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not found"
        )
    ctx = _request_context(request)
    try:
        if normalized == "dify":
            result = await test_dify(
                session,
                actor_user_id=actor.id,
                request_context=ctx,
            )
        elif normalized == "minio":
            result = await test_minio(
                session,
                actor_user_id=actor.id,
                request_context=ctx,
            )
        else:
            result = await test_mail(
                session,
                actor_user_id=actor.id,
                request_context=ctx,
            )
    except Exception as exc:
        raise _map_error(exc) from exc

    return ConnectivityTestOut(
        ok=result.ok,
        error_code=result.error_code,
        latency_ms=result.latency_ms,
    )
