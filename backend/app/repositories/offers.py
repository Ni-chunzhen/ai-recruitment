"""Repository helpers for Offer draft / version rows."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.offer import (
    OFFER_STATUS_VOIDED,
    Offer,
    OfferSendAttempt,
    OfferVersion,
)
from app.models.resume import HIRING_RECOMMEND_HIRE, HiringDecision


async def add_offer(session: AsyncSession, row: Offer) -> Offer:
    session.add(row)
    await session.flush()
    return row


async def add_offer_version(session: AsyncSession, row: OfferVersion) -> OfferVersion:
    session.add(row)
    await session.flush()
    return row


async def get_offer_by_id(session: AsyncSession, offer_id: UUID) -> Offer | None:
    return await session.scalar(select(Offer).where(Offer.id == offer_id))


async def get_offer_by_id_for_update(
    session: AsyncSession, offer_id: UUID
) -> Offer | None:
    return await session.scalar(
        select(Offer).where(Offer.id == offer_id).with_for_update()
    )


async def get_offer_version(
    session: AsyncSession, version_id: UUID
) -> OfferVersion | None:
    return await session.scalar(
        select(OfferVersion).where(OfferVersion.id == version_id)
    )


async def find_active_offer_for_application(
    session: AsyncSession, *, application_id: UUID
) -> Offer | None:
    return await session.scalar(
        select(Offer).where(
            Offer.application_id == application_id,
            Offer.status != OFFER_STATUS_VOIDED,
        )
    )


async def list_offers_by_application(
    session: AsyncSession, *, application_id: UUID
) -> list[Offer]:
    result = await session.scalars(
        select(Offer)
        .where(Offer.application_id == application_id)
        .order_by(Offer.created_at.desc(), Offer.id.desc())
    )
    return list(result.all())


async def list_recommend_hire_decisions(
    session: AsyncSession, *, application_id: UUID
) -> list[HiringDecision]:
    result = await session.scalars(
        select(HiringDecision)
        .where(
            HiringDecision.application_id == application_id,
            HiringDecision.decision == HIRING_RECOMMEND_HIRE,
        )
        .order_by(HiringDecision.created_at.desc(), HiringDecision.id.desc())
    )
    return list(result.all())


async def next_version_no(session: AsyncSession, *, offer_id: UUID) -> int:
    current = await session.scalar(
        select(func.coalesce(func.max(OfferVersion.version_no), 0)).where(
            OfferVersion.offer_id == offer_id
        )
    )
    return int(current or 0) + 1


async def add_offer_send_attempt(
    session: AsyncSession, row: OfferSendAttempt
) -> OfferSendAttempt:
    session.add(row)
    await session.flush()
    return row


async def get_offer_send_attempt_for_update(
    session: AsyncSession, attempt_id: UUID
) -> OfferSendAttempt | None:
    return await session.scalar(
        select(OfferSendAttempt)
        .where(OfferSendAttempt.id == attempt_id)
        .with_for_update()
    )


async def find_attempt_by_idempotency(
    session: AsyncSession,
    *,
    offer_id: UUID,
    idempotency_key: str,
) -> OfferSendAttempt | None:
    return await session.scalar(
        select(OfferSendAttempt).where(
            OfferSendAttempt.offer_id == offer_id,
            OfferSendAttempt.idempotency_key == idempotency_key,
        )
    )


async def list_offer_send_attempts(
    session: AsyncSession, *, offer_id: UUID
) -> list[OfferSendAttempt]:
    result = await session.scalars(
        select(OfferSendAttempt)
        .where(OfferSendAttempt.offer_id == offer_id)
        .order_by(
            OfferSendAttempt.attempt_no.asc(),
            OfferSendAttempt.created_at.asc(),
        )
    )
    return list(result)
