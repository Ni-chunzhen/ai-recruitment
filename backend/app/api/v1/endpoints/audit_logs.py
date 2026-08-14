from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_permission
from app.models import User
from app.repositories.audit_logs import list_audit_logs
from app.schemas.audit_log import AuditLogItem, AuditLogListResponse

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])


@router.get("", response_model=AuditLogListResponse)
async def get_audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    action: str | None = Query(default=None, max_length=64),
    resource_type: str | None = Query(default=None, max_length=64),
    actor_user_id: UUID | None = None,
    result: str | None = Query(default=None, max_length=32),
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("audit.read")),
) -> AuditLogListResponse:
    items, total = await list_audit_logs(
        session,
        page=page,
        page_size=page_size,
        action=action,
        resource_type=resource_type,
        actor_user_id=actor_user_id,
        result=result,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return AuditLogListResponse(
        items=[
            AuditLogItem(
                id=item.id,
                occurred_at=item.occurred_at,
                actor_user_id=item.actor_user_id,
                action=item.action,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                result=item.result,
                ip_address=item.ip_address,
                request_id=item.request_id,
                changes=item.changes,
            )
            for item in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.api_route("", methods=["PUT", "PATCH", "DELETE"])
@router.api_route("/{log_id}", methods=["PUT", "PATCH", "DELETE"])
async def audit_logs_mutations_disabled() -> None:
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="not allowed",
    )
