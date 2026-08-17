"""Repository helpers for interview transcript locking and lookups."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.interview_transcript import (
    InterviewTranscript,
    InterviewTranscriptSegment,
    InterviewTranscriptVersion,
    TranscriptVersionStatus,
    TranscriptVersionType,
)


async def get_transcript_by_round_id(
    session: AsyncSession, round_id: UUID
) -> InterviewTranscript | None:
    return await session.scalar(
        select(InterviewTranscript)
        .options(selectinload(InterviewTranscript.versions))
        .where(InterviewTranscript.interview_round_id == round_id)
    )


async def get_transcript_for_update_by_round(
    session: AsyncSession, round_id: UUID
) -> InterviewTranscript | None:
    return await session.scalar(
        select(InterviewTranscript)
        .options(selectinload(InterviewTranscript.versions))
        .where(InterviewTranscript.interview_round_id == round_id)
        .with_for_update()
    )


async def get_transcript_by_id(
    session: AsyncSession, transcript_id: UUID
) -> InterviewTranscript | None:
    return await session.scalar(
        select(InterviewTranscript)
        .options(selectinload(InterviewTranscript.versions))
        .where(InterviewTranscript.id == transcript_id)
    )


async def get_transcript_for_update(
    session: AsyncSession, transcript_id: UUID
) -> InterviewTranscript | None:
    return await session.scalar(
        select(InterviewTranscript)
        .options(selectinload(InterviewTranscript.versions))
        .where(InterviewTranscript.id == transcript_id)
        .with_for_update()
    )


async def get_version_by_id(
    session: AsyncSession, version_id: UUID
) -> InterviewTranscriptVersion | None:
    return await session.scalar(
        select(InterviewTranscriptVersion)
        .options(
            selectinload(InterviewTranscriptVersion.segments),
            selectinload(InterviewTranscriptVersion.transcript),
        )
        .where(InterviewTranscriptVersion.id == version_id)
    )


async def get_version_for_update(
    session: AsyncSession, version_id: UUID
) -> InterviewTranscriptVersion | None:
    return await session.scalar(
        select(InterviewTranscriptVersion)
        .options(
            selectinload(InterviewTranscriptVersion.segments),
            selectinload(InterviewTranscriptVersion.transcript),
        )
        .where(InterviewTranscriptVersion.id == version_id)
        .with_for_update()
    )


async def list_versions_for_transcript(
    session: AsyncSession, transcript_id: UUID
) -> list[InterviewTranscriptVersion]:
    result = await session.scalars(
        select(InterviewTranscriptVersion)
        .options(selectinload(InterviewTranscriptVersion.segments))
        .where(InterviewTranscriptVersion.transcript_id == transcript_id)
        .order_by(
            InterviewTranscriptVersion.created_at.asc(),
            InterviewTranscriptVersion.version_label.asc(),
        )
    )
    return list(result.all())


async def get_editing_draft(
    session: AsyncSession, transcript_id: UUID
) -> InterviewTranscriptVersion | None:
    return await session.scalar(
        select(InterviewTranscriptVersion)
        .options(selectinload(InterviewTranscriptVersion.segments))
        .where(
            InterviewTranscriptVersion.transcript_id == transcript_id,
            InterviewTranscriptVersion.version_type
            == TranscriptVersionType.DRAFT.value,
            InterviewTranscriptVersion.status == TranscriptVersionStatus.EDITING.value,
        )
    )


async def next_version_no(
    session: AsyncSession,
    transcript_id: UUID,
    version_type: str,
) -> int:
    current = await session.scalar(
        select(func.max(InterviewTranscriptVersion.version_no)).where(
            InterviewTranscriptVersion.transcript_id == transcript_id,
            InterviewTranscriptVersion.version_type == version_type,
        )
    )
    return int(current or 0) + 1


async def add_transcript(
    session: AsyncSession, transcript: InterviewTranscript
) -> InterviewTranscript:
    session.add(transcript)
    await session.flush()
    return transcript


async def add_version(
    session: AsyncSession, version: InterviewTranscriptVersion
) -> InterviewTranscriptVersion:
    session.add(version)
    await session.flush()
    return version


async def add_segment(
    session: AsyncSession, segment: InterviewTranscriptSegment
) -> InterviewTranscriptSegment:
    session.add(segment)
    await session.flush()
    return segment


async def replace_segments(
    session: AsyncSession,
    version: InterviewTranscriptVersion,
    segments: list[InterviewTranscriptSegment],
) -> None:
    version.segments.clear()
    await session.flush()
    for segment in segments:
        session.add(segment)
    await session.flush()
