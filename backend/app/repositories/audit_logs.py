from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, sanitize_audit_changes


async def create_audit_log(
    session: AsyncSession,
    *,
    action: str,
    result: str,
    resource_type: str,
    request_id: str,
    actor_user_id: UUID | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    changes: dict | None = None,
) -> AuditLog:
    audit_log = AuditLog(
        action=action,
        result=result,
        resource_type=resource_type,
        request_id=request_id,
        actor_user_id=actor_user_id,
        resource_id=resource_id,
        ip_address=ip_address,
        changes=sanitize_audit_changes(changes),
    )
    session.add(audit_log)
    await session.flush()
    return audit_log


async def list_audit_logs(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    action: str | None = None,
    resource_type: str | None = None,
    actor_user_id: UUID | None = None,
    result: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> tuple[list[AuditLog], int]:
    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)

    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
        count_query = count_query.where(AuditLog.resource_type == resource_type)
    if actor_user_id:
        query = query.where(AuditLog.actor_user_id == actor_user_id)
        count_query = count_query.where(AuditLog.actor_user_id == actor_user_id)
    if result:
        query = query.where(AuditLog.result == result)
        count_query = count_query.where(AuditLog.result == result)
    if occurred_from:
        query = query.where(AuditLog.occurred_at >= occurred_from)
        count_query = count_query.where(AuditLog.occurred_at >= occurred_from)
    if occurred_to:
        query = query.where(AuditLog.occurred_at <= occurred_to)
        count_query = count_query.where(AuditLog.occurred_at <= occurred_to)

    total = await session.scalar(count_query) or 0
    result_rows = await session.scalars(
        query.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result_rows.all()), int(total)
