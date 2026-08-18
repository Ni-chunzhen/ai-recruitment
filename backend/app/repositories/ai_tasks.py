from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ai_task import AITask, AITaskAttempt


class AITaskNotFoundError(Exception):
    pass


async def get_ai_task_by_id(
    session: AsyncSession,
    task_id: UUID,
    *,
    with_attempts: bool = True,
) -> AITask | None:
    stmt: Select[tuple[AITask]] = select(AITask).where(AITask.id == task_id)
    if with_attempts:
        stmt = stmt.options(selectinload(AITask.attempts))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_ai_tasks_by_business(
    session: AsyncSession,
    *,
    business_type: str,
    business_id: UUID,
    with_attempts: bool = False,
) -> list[AITask]:
    stmt: Select[tuple[AITask]] = (
        select(AITask)
        .where(
            AITask.business_type == business_type,
            AITask.business_id == business_id,
        )
        .order_by(AITask.created_at.desc())
    )
    if with_attempts:
        stmt = stmt.options(selectinload(AITask.attempts))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_ai_tasks_by_business(
    session: AsyncSession,
    *,
    business_type: str,
    business_id: UUID,
) -> int:
    stmt = select(func.count()).select_from(AITask).where(
        AITask.business_type == business_type,
        AITask.business_id == business_id,
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def list_tasks_for_raw_purge(
    session: AsyncSession,
    *,
    cutoff: datetime,
    limit: int = 200,
) -> list[AITask]:
    stmt = (
        select(AITask)
        .where(
            AITask.raw_purged_at.is_(None),
            AITask.created_at < cutoff,
        )
        .order_by(AITask.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def add_ai_task(session: AsyncSession, task: AITask) -> AITask:
    session.add(task)
    await session.flush()
    return task


async def add_ai_task_attempt(
    session: AsyncSession, attempt: AITaskAttempt
) -> AITaskAttempt:
    session.add(attempt)
    await session.flush()
    return attempt


async def find_ai_task_by_idempotency(
    session: AsyncSession,
    *,
    created_by: UUID,
    business_id: UUID,
    task_type: str,
    idempotency_key: str,
) -> AITask | None:
    result = await session.execute(
        select(AITask).where(
            AITask.created_by == created_by,
            AITask.business_id == business_id,
            AITask.task_type == task_type,
            AITask.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


async def find_inflight_task(
    session: AsyncSession,
    *,
    business_type: str,
    business_id: UUID,
    task_type: str,
    inflight_statuses: set[str],
) -> AITask | None:
    result = await session.execute(
        select(AITask)
        .where(
            AITask.business_type == business_type,
            AITask.business_id == business_id,
            AITask.task_type == task_type,
            AITask.status.in_(inflight_statuses),
        )
        .order_by(AITask.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def find_task_by_input_snapshot_hash(
    session: AsyncSession,
    *,
    business_type: str,
    business_id: UUID,
    task_type: str,
    input_snapshot_hash: str,
) -> AITask | None:
    result = await session.execute(
        select(AITask)
        .where(
            AITask.business_type == business_type,
            AITask.business_id == business_id,
            AITask.task_type == task_type,
            AITask.input_snapshot["input_snapshot_hash"].as_string()
            == input_snapshot_hash,
        )
        .order_by(AITask.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_admin_ai_tasks(
    session: AsyncSession,
    *,
    task_type: str | None = None,
    status: str | None = None,
    business_type: str | None = None,
    business_id: UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AITask], int]:
    stmt: Select[tuple[AITask]] = select(AITask)
    count_stmt = select(func.count()).select_from(AITask)

    if task_type:
        stmt = stmt.where(AITask.task_type == task_type)
        count_stmt = count_stmt.where(AITask.task_type == task_type)
    if status:
        stmt = stmt.where(AITask.status == status)
        count_stmt = count_stmt.where(AITask.status == status)
    if business_type:
        stmt = stmt.where(AITask.business_type == business_type)
        count_stmt = count_stmt.where(AITask.business_type == business_type)
    if business_id:
        stmt = stmt.where(AITask.business_id == business_id)
        count_stmt = count_stmt.where(AITask.business_id == business_id)
    if created_from:
        stmt = stmt.where(AITask.created_at >= created_from)
        count_stmt = count_stmt.where(AITask.created_at >= created_from)
    if created_to:
        stmt = stmt.where(AITask.created_at <= created_to)
        count_stmt = count_stmt.where(AITask.created_at <= created_to)
    needle = (keyword or "").strip()
    if needle:
        pattern = f"%{needle}%"
        keyword_filter = or_(
            AITask.error_message.ilike(pattern),
            AITask.error_code.ilike(pattern),
            AITask.task_type.ilike(pattern),
            AITask.business_type.ilike(pattern),
            cast(AITask.id, String).ilike(pattern),
            cast(AITask.business_id, String).ilike(pattern),
        )
        stmt = stmt.where(keyword_filter)
        count_stmt = count_stmt.where(keyword_filter)

    total = int(await session.scalar(count_stmt) or 0)
    result = await session.execute(
        stmt.order_by(AITask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total
