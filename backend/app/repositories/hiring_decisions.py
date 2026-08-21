"""Repository helpers for immutable HiringDecision rows."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resume import HiringDecision


async def add_hiring_decision(
    session: AsyncSession, row: HiringDecision
) -> HiringDecision:
    session.add(row)
    await session.flush()
    return row


async def find_hiring_by_idempotency(
    session: AsyncSession,
    *,
    application_id: UUID,
    idempotency_key: str,
) -> HiringDecision | None:
    result = await session.execute(
        select(HiringDecision).where(
            HiringDecision.application_id == application_id,
            HiringDecision.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


async def list_hiring_by_application(
    session: AsyncSession, *, application_id: UUID
) -> list[HiringDecision]:
    result = await session.execute(
        select(HiringDecision)
        .where(HiringDecision.application_id == application_id)
        .order_by(HiringDecision.created_at.asc(), HiringDecision.id.asc())
    )
    return list(result.scalars().all())
