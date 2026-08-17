from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invitation import (
    INVITATION_STATUS_DRAFT,
    INVITATION_STATUS_READY,
    INVITATION_STATUS_RECORDED_SENT,
    INVITATION_STATUS_VOIDED,
    InterviewInvitationMessage,
    InterviewInvitationSendRecord,
    InterviewInvitationVersion,
)

_VOIDABLE_STATUSES = frozenset(
    {
        INVITATION_STATUS_DRAFT,
        INVITATION_STATUS_READY,
        INVITATION_STATUS_RECORDED_SENT,
    }
)


async def get_message_by_id(
    session: AsyncSession, message_id: UUID
) -> InterviewInvitationMessage | None:
    return await session.scalar(
        select(InterviewInvitationMessage).where(
            InterviewInvitationMessage.id == message_id
        )
    )


async def get_message_for_update(
    session: AsyncSession, message_id: UUID
) -> InterviewInvitationMessage | None:
    return await session.scalar(
        select(InterviewInvitationMessage)
        .where(InterviewInvitationMessage.id == message_id)
        .with_for_update()
    )


async def list_messages_for_round(
    session: AsyncSession, round_id: UUID
) -> list[InterviewInvitationMessage]:
    result = await session.scalars(
        select(InterviewInvitationMessage)
        .where(InterviewInvitationMessage.interview_round_id == round_id)
        .order_by(
            InterviewInvitationMessage.created_at.asc(),
            InterviewInvitationMessage.id.asc(),
        )
    )
    return list(result.all())


async def list_messages_for_schedule(
    session: AsyncSession, schedule_id: UUID
) -> list[InterviewInvitationMessage]:
    result = await session.scalars(
        select(InterviewInvitationMessage)
        .where(InterviewInvitationMessage.schedule_id == schedule_id)
        .order_by(
            InterviewInvitationMessage.created_at.asc(),
            InterviewInvitationMessage.id.asc(),
        )
    )
    return list(result.all())


async def find_message(
    session: AsyncSession,
    schedule_id: UUID,
    event_type: str,
    audience_type: str,
    recipient_key: str,
) -> InterviewInvitationMessage | None:
    return await session.scalar(
        select(InterviewInvitationMessage).where(
            InterviewInvitationMessage.schedule_id == schedule_id,
            InterviewInvitationMessage.event_type == event_type,
            InterviewInvitationMessage.audience_type == audience_type,
            InterviewInvitationMessage.recipient_key == recipient_key,
        )
    )


async def add_message(
    session: AsyncSession, message: InterviewInvitationMessage
) -> InterviewInvitationMessage:
    session.add(message)
    await session.flush()
    return message


async def add_version(
    session: AsyncSession, version: InterviewInvitationVersion
) -> InterviewInvitationVersion:
    session.add(version)
    await session.flush()
    return version


async def add_send_record(
    session: AsyncSession, record: InterviewInvitationSendRecord
) -> InterviewInvitationSendRecord:
    session.add(record)
    await session.flush()
    return record


async def list_versions(
    session: AsyncSession, message_id: UUID
) -> list[InterviewInvitationVersion]:
    result = await session.scalars(
        select(InterviewInvitationVersion)
        .where(InterviewInvitationVersion.message_id == message_id)
        .order_by(InterviewInvitationVersion.version_no.asc())
    )
    return list(result.all())


async def get_version(
    session: AsyncSession, version_id: UUID
) -> InterviewInvitationVersion | None:
    return await session.scalar(
        select(InterviewInvitationVersion).where(
            InterviewInvitationVersion.id == version_id
        )
    )


async def next_version_no(session: AsyncSession, message_id: UUID) -> int:
    current = await session.scalar(
        select(func.max(InterviewInvitationVersion.version_no)).where(
            InterviewInvitationVersion.message_id == message_id
        )
    )
    return int(current or 0) + 1


async def void_open_messages_for_schedule(
    session: AsyncSession, schedule_id: UUID
) -> list[InterviewInvitationMessage]:
    """Mark all non-VOIDED messages for a schedule as VOIDED (schedule invalidated)."""
    result = await session.scalars(
        select(InterviewInvitationMessage)
        .where(
            InterviewInvitationMessage.schedule_id == schedule_id,
            InterviewInvitationMessage.status.in_(_VOIDABLE_STATUSES),
        )
        .with_for_update()
    )
    messages = list(result.all())
    for message in messages:
        message.status = INVITATION_STATUS_VOIDED
    if messages:
        await session.flush()
    return messages


async def count_by_status_for_round(
    session: AsyncSession, round_id: UUID
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(
                InterviewInvitationMessage.status,
                func.count(),
            )
            .where(InterviewInvitationMessage.interview_round_id == round_id)
            .group_by(InterviewInvitationMessage.status)
        )
    ).all()
    return {str(status): int(count) for status, count in rows}


async def count_by_status_for_schedule(
    session: AsyncSession, schedule_id: UUID
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(
                InterviewInvitationMessage.status,
                func.count(),
            )
            .where(InterviewInvitationMessage.schedule_id == schedule_id)
            .group_by(InterviewInvitationMessage.status)
        )
    ).all()
    return {str(status): int(count) for status, count in rows}


def status_counts_to_out(raw: dict[str, int]) -> dict[str, int]:
    """Map ORM status keys to API count bucket names."""
    return {
        "generated": int(raw.get(INVITATION_STATUS_DRAFT, 0)),
        "ready": int(raw.get(INVITATION_STATUS_READY, 0)),
        "recorded_sent": int(raw.get(INVITATION_STATUS_RECORDED_SENT, 0)),
        "voided": int(raw.get(INVITATION_STATUS_VOIDED, 0)),
    }
